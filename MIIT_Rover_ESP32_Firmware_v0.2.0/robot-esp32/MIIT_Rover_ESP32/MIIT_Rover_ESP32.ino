#include <Arduino.h>
#include <ArduinoJson.h>
#include <ctype.h>
#include <esp_arduino_version.h>
#include <esp_err.h>
#include <esp_idf_version.h>
#include <esp_task_wdt.h>

#include "config.h"

#if MIIT_ENABLE_BLUETOOTH_MANUAL
#if !defined(CONFIG_BT_ENABLED) || !defined(CONFIG_BLUEDROID_ENABLED)
#error "Classic Bluetooth is not enabled for this board. Set MIIT_ENABLE_BLUETOOTH_MANUAL to 0."
#endif
#if !defined(CONFIG_BT_SPP_ENABLED)
#error "Bluetooth SPP is only available on the classic ESP32. Set MIIT_ENABLE_BLUETOOTH_MANUAL to 0."
#endif
#include <BluetoothSerial.h>
BluetoothSerial SerialBT;
#endif

namespace {

enum class ControlMode : uint8_t { AUTO_MODE, MANUAL_MODE };

enum Fault : uint32_t {
  FAULT_NONE = 0,
  FAULT_ESTOP_INPUT = 1UL << 0,
  FAULT_PI_HARD_STOP = 1UL << 1,
  FAULT_REMOTE_ESTOP = 1UL << 2,
  FAULT_OUTPUTS_DISABLED = 1UL << 3,
  FAULT_PWM_INIT = 1UL << 4,
  FAULT_TASK_WATCHDOG = 1UL << 5,
  FAULT_PI_TIMEOUT = 1UL << 6,
  FAULT_COMMAND_EXPIRED = 1UL << 7,
  FAULT_PARSE = 1UL << 8,
  FAULT_PROTOCOL = 1UL << 9,
  FAULT_CRC = 1UL << 10,
  FAULT_SEQUENCE = 1UL << 11,
  FAULT_MODE_CHANGE = 1UL << 12,
  FAULT_MANUAL_TIMEOUT = 1UL << 13,
};

constexpr uint32_t NON_RESETTABLE_FAULTS =
    FAULT_OUTPUTS_DISABLED | FAULT_PWM_INIT | FAULT_TASK_WATCHDOG;
constexpr uint32_t ESTOP_FAULTS =
    FAULT_ESTOP_INPUT | FAULT_PI_HARD_STOP | FAULT_REMOTE_ESTOP;

struct MotorOutput {
  uint8_t pwmPin;
  uint8_t in1Pin;
  uint8_t in2Pin;
  uint8_t channel;
  bool inverted;
  int8_t lastDirection;
};

MotorOutput left1Motor = {cfg::LEFT1_PWM, cfg::LEFT1_IN1, cfg::LEFT1_IN2,
                          cfg::LEFT1_CHANNEL, cfg::LEFT1_INVERT, 0};
MotorOutput left2Motor = {cfg::LEFT2_PWM, cfg::LEFT2_IN1, cfg::LEFT2_IN2,
                          cfg::LEFT2_CHANNEL, cfg::LEFT2_INVERT, 0};
MotorOutput right1Motor = {cfg::RIGHT1_PWM, cfg::RIGHT1_IN1, cfg::RIGHT1_IN2,
                           cfg::RIGHT1_CHANNEL, cfg::RIGHT1_INVERT, 0};
MotorOutput right2Motor = {cfg::RIGHT2_PWM, cfg::RIGHT2_IN1, cfg::RIGHT2_IN2,
                           cfg::RIGHT2_CHANNEL, cfg::RIGHT2_INVERT, 0};

enum WheelIndex : uint8_t {
  LEFT_1 = 0,
  LEFT_2 = 1,
  RIGHT_1 = 2,
  RIGHT_2 = 3,
  WHEEL_COUNT = 4,
};

// This order intentionally matches the user's proven sketch: Left1, Left2,
// Right1, Right2.
MotorOutput *const motors[WHEEL_COUNT] = {&left1Motor, &left2Motor,
                                          &right1Motor, &right2Motor};

bool pwmReady = false;
bool taskWatchdogReady = false;
bool armed = false;
bool estopLatched = false;
uint32_t activeFaults = FAULT_NONE;
uint32_t faultHistory = FAULT_NONE;

ControlMode currentMode = ControlMode::MANUAL_MODE;
int16_t wheelTarget[WHEEL_COUNT] = {0, 0, 0, 0};
int16_t wheelApplied[WHEEL_COUNT] = {0, 0, 0, 0};
uint8_t manualPwmDuty = cfg::DEFAULT_MANUAL_PWM_DUTY;
bool bluetoothPreviouslyConnected = false;

uint32_t lastPiFrameMs = 0;
uint32_t motionDeadlineMs = 0;
bool motionDeadlineValid = false;
uint32_t lastManualCommandMs = 0;
uint32_t lastControlMs = 0;
uint32_t lastStatusMs = 0;

char activeSession[cfg::SESSION_MAX + 1] = {0};
bool sessionActive = false;
uint32_t lastSequence = 0;
bool sequenceActive = false;

char serialLine[cfg::SERIAL_LINE_MAX + 1] = {0};
size_t serialLineLength = 0;
bool discardSerialLine = false;

uint32_t resetPressStartedMs = 0;
bool resetHoldHandled = false;
bool resetReleasedSinceLatch = false;

const char *modeName(ControlMode mode) {
  return mode == ControlMode::AUTO_MODE ? "AUTO" : "MANUAL";
}

bool deadlineReached(uint32_t now, uint32_t deadline) {
  return static_cast<int32_t>(now - deadline) >= 0;
}

void setFault(uint32_t fault) {
  activeFaults |= fault;
  faultHistory |= fault;
}

void requireFreshLocalResetGesture() {
  resetPressStartedMs = 0;
  resetHoldHandled = false;
  resetReleasedSinceLatch = false;
}

bool estopLoopHealthy() {
#if MIIT_REQUIRE_ESTOP_LOOP
  return digitalRead(cfg::ESTOP_LOOP_PIN) == cfg::ESTOP_HEALTHY_LEVEL;
#else
  return true;
#endif
}

bool piHardStopActive() {
#if MIIT_USE_PI_HARD_STOP_INPUT
  return digitalRead(cfg::PI_HARD_STOP_PIN) == cfg::PI_HARD_STOP_ACTIVE_LEVEL;
#else
  return false;
#endif
}

ControlMode readControlMode() {
#if MIIT_USE_PHYSICAL_MODE_SWITCH
  return digitalRead(cfg::MODE_SELECT_PIN) == cfg::MANUAL_MODE_LEVEL
             ? ControlMode::MANUAL_MODE
             : ControlMode::AUTO_MODE;
#else
  return currentMode;
#endif
}

void writePwm(const MotorOutput &motor, uint16_t duty) {
#if ESP_ARDUINO_VERSION_MAJOR >= 3
  ledcWrite(motor.pwmPin, duty);
#else
  ledcWrite(motor.channel, duty);
#endif
}

bool attachPwm(const MotorOutput &motor) {
#if ESP_ARDUINO_VERSION_MAJOR >= 3
  return ledcAttach(motor.pwmPin, cfg::PWM_FREQUENCY_HZ,
                    cfg::PWM_RESOLUTION_BITS);
#else
  const double actualFrequency =
      ledcSetup(motor.channel, cfg::PWM_FREQUENCY_HZ,
                cfg::PWM_RESOLUTION_BITS);
  ledcAttachPin(motor.pwmPin, motor.channel);
  return actualFrequency > 0.0;
#endif
}

void setMotorPinsLow(MotorOutput &motor) {
  writePwm(motor, 0);
  digitalWrite(motor.in1Pin, LOW);
  digitalWrite(motor.in2Pin, LOW);
  motor.lastDirection = 0;
}

void hardDisableMotors() {
  for (uint8_t index = 0; index < WHEEL_COUNT; ++index) {
    setMotorPinsLow(*motors[index]);
    wheelTarget[index] = 0;
    wheelApplied[index] = 0;
  }
  motionDeadlineValid = false;
}

void writeMotor(MotorOutput &motor, int16_t permille) {
#if !MIIT_ENABLE_MOTOR_OUTPUTS
  (void)permille;
  setMotorPinsLow(motor);
  return;
#else
  int16_t command = motor.inverted ? -permille : permille;
  command = constrain(command, -1000, 1000);

  if (command == 0) {
    setMotorPinsLow(motor);
    return;
  }

  const int8_t direction = command > 0 ? 1 : -1;
  if (motor.lastDirection != 0 && motor.lastDirection != direction) {
    // Remove drive before changing bridge direction. The slew limiter normally
    // crosses zero first; this is a second defensive layer.
    setMotorPinsLow(motor);
    return;
  }

  writePwm(motor, 0);
  if (direction > 0) {
    digitalWrite(motor.in1Pin, HIGH);
    digitalWrite(motor.in2Pin, LOW);
  } else {
    digitalWrite(motor.in1Pin, LOW);
    digitalWrite(motor.in2Pin, HIGH);
  }

  const uint32_t magnitude = static_cast<uint32_t>(abs(command));
  const uint16_t duty = static_cast<uint16_t>(
      (magnitude * static_cast<uint32_t>(cfg::PWM_MAX_DUTY)) / 1000UL);
  writePwm(motor, duty);
  motor.lastDirection = direction;
#endif
}

void setWheelTargets(int16_t left1, int16_t left2, int16_t right1,
                     int16_t right2) {
  wheelTarget[LEFT_1] = left1;
  wheelTarget[LEFT_2] = left2;
  wheelTarget[RIGHT_1] = right1;
  wheelTarget[RIGHT_2] = right2;
}

void applyWheelOutputs() {
  for (uint8_t index = 0; index < WHEEL_COUNT; ++index) {
    writeMotor(*motors[index], wheelApplied[index]);
  }
}

void mixMecanumTargets(int32_t vx, int32_t vy, int32_t wz,
                       int16_t limitPermille) {
  // ROS REP-103 signs: +vx forward, +vy left, +wz counter-clockwise/left.
  // The four signs reproduce the user's proven F/L/R/G/I motor patterns.
  int32_t mixed[WHEEL_COUNT] = {
      vx + vy - wz,  // Left 1
      vx - vy - wz,  // Left 2
      vx - vy + wz,  // Right 1
      vx + vy + wz,  // Right 2
  };

  int32_t largestMagnitude = 0;
  for (uint8_t index = 0; index < WHEEL_COUNT; ++index) {
    const int32_t magnitude = labs(mixed[index]);
    if (magnitude > largestMagnitude) {
      largestMagnitude = magnitude;
    }
  }

  if (largestMagnitude > limitPermille && largestMagnitude > 0) {
    for (uint8_t index = 0; index < WHEEL_COUNT; ++index) {
      mixed[index] = (mixed[index] * limitPermille) / largestMagnitude;
    }
  }

  setWheelTargets(static_cast<int16_t>(mixed[LEFT_1]),
                  static_cast<int16_t>(mixed[LEFT_2]),
                  static_cast<int16_t>(mixed[RIGHT_1]),
                  static_cast<int16_t>(mixed[RIGHT_2]));
}

void safeStop(uint32_t fault = FAULT_NONE) {
  if (fault != FAULT_NONE) {
    setFault(fault);
  }
  armed = false;
  hardDisableMotors();
}

bool hardwareReadyToArm() {
  if (!pwmReady || !taskWatchdogReady) {
    return false;
  }
#if !MIIT_ENABLE_MOTOR_OUTPUTS
  return false;
#endif
  if (!estopLoopHealthy() || piHardStopActive() || estopLatched) {
    return false;
  }
  return (activeFaults & NON_RESETTABLE_FAULTS) == 0;
}

void sendJson(JsonDocument &doc, Stream &stream) {
  serializeJson(doc, stream);
  stream.write('\n');
}

void addCommonState(JsonDocument &doc) {
  doc["mode"] = modeName(currentMode);
  doc["armed"] = armed;
  doc["estopLatched"] = estopLatched;
  doc["estopInputHealthy"] = estopLoopHealthy();
  doc["piHardStop"] = piHardStopActive();
  doc["motorOutputsEnabled"] = MIIT_ENABLE_MOTOR_OUTPUTS != 0;
  doc["activeFaults"] = activeFaults;
  doc["faultHistory"] = faultHistory;
  doc["left1Target"] = wheelTarget[LEFT_1];
  doc["left2Target"] = wheelTarget[LEFT_2];
  doc["right1Target"] = wheelTarget[RIGHT_1];
  doc["right2Target"] = wheelTarget[RIGHT_2];
  doc["left1Applied"] = wheelApplied[LEFT_1];
  doc["left2Applied"] = wheelApplied[LEFT_2];
  doc["right1Applied"] = wheelApplied[RIGHT_1];
  doc["right2Applied"] = wheelApplied[RIGHT_2];
  doc["manualPwmDuty"] = manualPwmDuty;
}

void sendState(const char *type = "STATE") {
  JsonDocument doc;
  doc["v"] = 1;
  doc["type"] = type;
  doc["firmware"] = cfg::FIRMWARE_VERSION;
  doc["uptimeMs"] = millis();
  addCommonState(doc);
  sendJson(doc, Serial);
}

void sendAck(const char *command, bool ok, const char *reason,
             bool hasSequence = false, uint32_t sequence = 0) {
  JsonDocument doc;
  doc["v"] = 1;
  doc["type"] = ok ? "ACK" : "NACK";
  doc["cmd"] = command;
  if (hasSequence) {
    doc["seq"] = sequence;
  }
  doc["ok"] = ok;
  if (reason != nullptr && reason[0] != '\0') {
    doc["reason"] = reason;
  }
  addCommonState(doc);
  sendJson(doc, Serial);
}

#if MIIT_ENABLE_BLUETOOTH_MANUAL
void sendBluetoothReply(const char *message) {
  SerialBT.println(message);
}
#endif

uint16_t crc16CcittFalse(const uint8_t *data, size_t length) {
  uint16_t crc = 0xFFFF;
  for (size_t i = 0; i < length; ++i) {
    crc ^= static_cast<uint16_t>(data[i]) << 8;
    for (uint8_t bit = 0; bit < 8; ++bit) {
      crc = (crc & 0x8000U) ? static_cast<uint16_t>((crc << 1U) ^ 0x1021U)
                            : static_cast<uint16_t>(crc << 1U);
    }
  }
  return crc;
}

bool validSessionName(const char *session) {
  if (session == nullptr) {
    return false;
  }
  const size_t length = strlen(session);
  if (length == 0 || length > cfg::SESSION_MAX) {
    return false;
  }
  for (size_t i = 0; i < length; ++i) {
    const char c = session[i];
    if (!(isalnum(static_cast<unsigned char>(c)) || c == '-' || c == '_' ||
          c == '.' || c == ':')) {
      return false;
    }
  }
  return true;
}

bool parseCrc16(JsonVariantConst value, uint16_t &result) {
  if (!value.is<const char *>()) {
    return false;
  }
  const char *text = value.as<const char *>();
  if (text == nullptr || strlen(text) != 4) {
    return false;
  }
  for (size_t i = 0; i < 4; ++i) {
    if (!isxdigit(static_cast<unsigned char>(text[i]))) {
      return false;
    }
  }
  char *end = nullptr;
  const unsigned long parsed = strtoul(text, &end, 16);
  if (end == nullptr || *end != '\0' || parsed > 0xFFFFUL) {
    return false;
  }
  result = static_cast<uint16_t>(parsed);
  return true;
}

bool computeFrameCrc(uint32_t version, const char *session, uint32_t sequence,
                     const char *command, int32_t vx, int32_t vy, int32_t wz,
                     uint32_t ttlMs, uint16_t &result) {
  char canonical[160];
  const int written = snprintf(
      canonical, sizeof(canonical), "%lu|%s|%lu|%s|%ld|%ld|%ld|%lu",
      static_cast<unsigned long>(version), session,
      static_cast<unsigned long>(sequence), command, static_cast<long>(vx),
      static_cast<long>(vy), static_cast<long>(wz),
      static_cast<unsigned long>(ttlMs));
  if (written <= 0 || static_cast<size_t>(written) >= sizeof(canonical)) {
    return false;
  }
  result = crc16CcittFalse(reinterpret_cast<const uint8_t *>(canonical),
                           static_cast<size_t>(written));
  return true;
}

bool sequenceIsNewer(uint32_t candidate, uint32_t previous) {
  return static_cast<int32_t>(candidate - previous) > 0;
}

void rejectProtectedFrame(const char *command, const char *reason,
                          uint32_t fault, bool hasSequence,
                          uint32_t sequence) {
  safeStop(fault);
  sendAck(command, false, reason, hasSequence, sequence);
}

void clearTransientFaultsForArm() {
  activeFaults &= (NON_RESETTABLE_FAULTS | ESTOP_FAULTS);
}

void handleProtectedCommand(JsonDocument &doc, const char *command) {
  const bool hasSequence = doc["seq"].is<uint32_t>();
  const uint32_t sequence = hasSequence ? doc["seq"].as<uint32_t>() : 0;

  if (!doc["session"].is<const char *>() || !hasSequence ||
      !doc["ttlMs"].is<uint32_t>() || !doc["crc16"].is<const char *>()) {
    rejectProtectedFrame(command, "MISSING_OR_INVALID_FIELDS", FAULT_PROTOCOL,
                         hasSequence, sequence);
    return;
  }

  const char *session = doc["session"].as<const char *>();
  const uint32_t ttlMs = doc["ttlMs"].as<uint32_t>();
  if (!validSessionName(session) || ttlMs < cfg::MIN_COMMAND_TTL_MS ||
      ttlMs > cfg::MAX_COMMAND_TTL_MS) {
    rejectProtectedFrame(command, "BAD_SESSION_OR_TTL", FAULT_PROTOCOL, true,
                         sequence);
    return;
  }

  int32_t vx = 0;
  int32_t vy = 0;
  int32_t wz = 0;
  if (!doc["vx"].isNull()) {
    if (!doc["vx"].is<int32_t>()) {
      rejectProtectedFrame(command, "BAD_VX_VALUE", FAULT_PROTOCOL, true,
                           sequence);
      return;
    }
    vx = doc["vx"].as<int32_t>();
  }
  if (!doc["vy"].isNull()) {
    if (!doc["vy"].is<int32_t>()) {
      rejectProtectedFrame(command, "BAD_VY_VALUE", FAULT_PROTOCOL, true,
                           sequence);
      return;
    }
    vy = doc["vy"].as<int32_t>();
  }
  if (!doc["wz"].isNull()) {
    if (!doc["wz"].is<int32_t>()) {
      rejectProtectedFrame(command, "BAD_WZ_VALUE", FAULT_PROTOCOL, true,
                           sequence);
      return;
    }
    wz = doc["wz"].as<int32_t>();
  }

  uint16_t receivedCrc = 0;
  uint16_t calculatedCrc = 0;
  if (!parseCrc16(doc["crc16"], receivedCrc) ||
      !computeFrameCrc(1, session, sequence, command, vx, vy, wz, ttlMs,
                       calculatedCrc) ||
      receivedCrc != calculatedCrc) {
    rejectProtectedFrame(command, "CRC_MISMATCH", FAULT_CRC, true, sequence);
    return;
  }

  const bool isArm = strcmp(command, "ARM") == 0;
  if (!isArm) {
    if (!sessionActive || strcmp(session, activeSession) != 0) {
      rejectProtectedFrame(command, "SESSION_MISMATCH", FAULT_SEQUENCE, true,
                           sequence);
      return;
    }
    if (!sequenceActive || !sequenceIsNewer(sequence, lastSequence)) {
      rejectProtectedFrame(command, "DUPLICATE_OR_OLD_SEQUENCE",
                           FAULT_SEQUENCE, true, sequence);
      return;
    }
  } else if (sessionActive && strcmp(session, activeSession) == 0 &&
             sequenceActive && !sequenceIsNewer(sequence, lastSequence)) {
    rejectProtectedFrame(command, "DUPLICATE_OR_OLD_SEQUENCE", FAULT_SEQUENCE,
                         true, sequence);
    return;
  }

  // The frame is authentic only in the frame-integrity sense. UART CRC is not
  // cryptographic authentication; physical access to the serial link is trusted.
  strlcpy(activeSession, session, sizeof(activeSession));
  sessionActive = true;
  lastSequence = sequence;
  sequenceActive = true;

  const uint32_t now = millis();

  if (isArm) {
    if (vx != 0 || vy != 0 || wz != 0) {
      rejectProtectedFrame(command, "ARM_MUST_HAVE_ZERO_TARGETS",
                           FAULT_PROTOCOL, true, sequence);
      return;
    }
    clearTransientFaultsForArm();
#if MIIT_USE_PHYSICAL_MODE_SWITCH
    if (currentMode != ControlMode::AUTO_MODE) {
      safeStop(FAULT_MODE_CHANGE);
      sendAck(command, false, "NOT_IN_AUTO_MODE", true, sequence);
      return;
    }
#else
    if (currentMode == ControlMode::MANUAL_MODE && armed) {
      safeStop(FAULT_MODE_CHANGE);
      sendAck(command, false, "MANUAL_WAS_ACTIVE_RETRY_ARM", true, sequence);
      return;
    }
    if (currentMode != ControlMode::AUTO_MODE) {
      safeStop();
      currentMode = ControlMode::AUTO_MODE;
      activeFaults &= ~FAULT_MODE_CHANGE;
    }
#endif
    if (!hardwareReadyToArm()) {
      safeStop();
      sendAck(command, false, "HARDWARE_NOT_SAFE", true, sequence);
      return;
    }
    hardDisableMotors();
    armed = true;
    lastPiFrameMs = now;
    sendAck(command, true, "ARMED_ZERO_OUTPUT", true, sequence);
    return;
  }

  if (strcmp(command, "HEARTBEAT") == 0) {
    if (vx != 0 || vy != 0 || wz != 0) {
      rejectProtectedFrame(command, "HEARTBEAT_MUST_HAVE_ZERO_TARGETS",
                           FAULT_PROTOCOL, true, sequence);
      return;
    }
    lastPiFrameMs = now;
    sendAck(command, true, armed ? "LINK_OK" : "LINK_OK_DISARMED", true,
            sequence);
    return;
  }

  if (strcmp(command, "DRIVE") == 0) {
    if (currentMode != ControlMode::AUTO_MODE || !armed ||
        !hardwareReadyToArm()) {
      safeStop();
      sendAck(command, false, "NOT_ARMED_OR_NOT_SAFE", true, sequence);
      return;
    }
    if (vx < -cfg::MAX_AUTO_PERMILLE || vx > cfg::MAX_AUTO_PERMILLE ||
        vy < -cfg::MAX_AUTO_PERMILLE || vy > cfg::MAX_AUTO_PERMILLE ||
        wz < -cfg::MAX_AUTO_PERMILLE || wz > cfg::MAX_AUTO_PERMILLE) {
      rejectProtectedFrame(command, "TARGET_EXCEEDS_LIMIT", FAULT_PROTOCOL,
                           true, sequence);
      return;
    }
    mixMecanumTargets(vx, vy, wz, cfg::MAX_AUTO_PERMILLE);
    lastPiFrameMs = now;
    motionDeadlineMs = now + ttlMs;
    motionDeadlineValid = true;
    sendAck(command, true, "TARGET_ACCEPTED", true, sequence);
    return;
  }

  rejectProtectedFrame(command, "UNKNOWN_PROTECTED_COMMAND", FAULT_PROTOCOL,
                       true, sequence);
}

void latchEmergencyStop(uint32_t fault, const char *reason) {
  estopLatched = true;
  requireFreshLocalResetGesture();
  safeStop(fault);
  sendAck("ESTOP", true, reason);
}

void handlePiLine(const char *line) {
  JsonDocument doc;
  const DeserializationError error = deserializeJson(doc, line);
  if (error) {
    safeStop(FAULT_PARSE);
    sendAck("INVALID", false, "MALFORMED_JSON");
    return;
  }

  if (!doc["v"].is<uint32_t>() || doc["v"].as<uint32_t>() != 1 ||
      !doc["cmd"].is<const char *>()) {
    safeStop(FAULT_PROTOCOL);
    sendAck("INVALID", false, "BAD_VERSION_OR_COMMAND");
    return;
  }

  const char *command = doc["cmd"].as<const char *>();
  if (command == nullptr || strlen(command) == 0 || strlen(command) > 24) {
    safeStop(FAULT_PROTOCOL);
    sendAck("INVALID", false, "BAD_COMMAND");
    return;
  }

  // STOP is deliberately fail-open for stopping: it does not need a CRC,
  // session, or sequence. It can never cause motion.
  if (strcmp(command, "STOP") == 0) {
    safeStop();
    const bool hasSequence = doc["seq"].is<uint32_t>();
    sendAck(command, true, "STOPPED", hasSequence,
            hasSequence ? doc["seq"].as<uint32_t>() : 0);
    return;
  }

  // ESTOP is also accepted without CRC because accepting an extra stop is safe.
  // It latches and needs the local reset button after all hard-stop inputs clear.
  if (strcmp(command, "ESTOP") == 0) {
    latchEmergencyStop(FAULT_REMOTE_ESTOP, "REMOTE_ESTOP_LATCHED");
    return;
  }

  if (strcmp(command, "STATUS") == 0 || strcmp(command, "PING") == 0) {
    sendAck(command, true, "STATUS_ONLY");
    return;
  }

  if (strcmp(command, "ARM") == 0 || strcmp(command, "HEARTBEAT") == 0 ||
      strcmp(command, "DRIVE") == 0) {
    handleProtectedCommand(doc, command);
    return;
  }

  safeStop(FAULT_PROTOCOL);
  sendAck(command, false, "UNKNOWN_COMMAND");
}

void servicePiSerial() {
  while (Serial.available() > 0) {
    const char c = static_cast<char>(Serial.read());
    if (c == '\r') {
      continue;
    }
    if (c == '\n') {
      if (!discardSerialLine && serialLineLength > 0) {
        serialLine[serialLineLength] = '\0';
        handlePiLine(serialLine);
      }
      serialLineLength = 0;
      discardSerialLine = false;
      continue;
    }

    if (discardSerialLine) {
      continue;
    }
    if (serialLineLength >= cfg::SERIAL_LINE_MAX) {
      discardSerialLine = true;
      serialLineLength = 0;
      safeStop(FAULT_PROTOCOL);
      sendAck("INVALID", false, "SERIAL_FRAME_TOO_LONG");
      continue;
    }
    serialLine[serialLineLength++] = c;
  }
}

void clearEmergencyLatchLocally() {
  if (!estopLatched) {
    sendAck("LOCAL_RESET", false, "NO_ESTOP_LATCH_TO_CLEAR");
    return;
  }
  if (!estopLoopHealthy() || piHardStopActive()) {
    sendAck("LOCAL_RESET", false, "HARD_STOP_INPUT_STILL_ACTIVE");
    return;
  }

  hardDisableMotors();
  armed = false;
  estopLatched = false;
  requireFreshLocalResetGesture();
  activeFaults &= NON_RESETTABLE_FAULTS;
  sessionActive = false;
  sequenceActive = false;
  activeSession[0] = '\0';
  sendAck("LOCAL_RESET", true, "LATCH_CLEARED_REMAINS_DISARMED");
}

void serviceLocalResetButton(uint32_t now) {
  const bool pressed =
      digitalRead(cfg::LOCAL_RESET_PIN) == cfg::LOCAL_RESET_ACTIVE_LEVEL;
  if (!pressed) {
    resetPressStartedMs = 0;
    resetHoldHandled = false;
    if (estopLatched) {
      resetReleasedSinceLatch = true;
    }
    return;
  }

  if (!estopLatched || !resetReleasedSinceLatch) {
    // A reset button held before a new ESTOP is not a valid reset gesture.
    // Require release after the latch, then a new press-and-hold.
    resetPressStartedMs = 0;
    resetHoldHandled = false;
    return;
  }

  if (resetPressStartedMs == 0) {
    resetPressStartedMs = now;
    return;
  }

  if (!resetHoldHandled &&
      static_cast<uint32_t>(now - resetPressStartedMs) >=
          cfg::LOCAL_RESET_HOLD_MS) {
    resetHoldHandled = true;
    clearEmergencyLatchLocally();
  }
}

void serviceSafetyInputs(uint32_t now) {
  if (!estopLoopHealthy()) {
    if (!estopLatched || (activeFaults & FAULT_ESTOP_INPUT) == 0) {
      estopLatched = true;
      requireFreshLocalResetGesture();
      safeStop(FAULT_ESTOP_INPUT);
      sendAck("ESTOP_INPUT", true, "PHYSICAL_ESTOP_OR_OPEN_LOOP");
    } else {
      hardDisableMotors();
      armed = false;
    }
  }

  if (piHardStopActive()) {
    if (!estopLatched || (activeFaults & FAULT_PI_HARD_STOP) == 0) {
      estopLatched = true;
      requireFreshLocalResetGesture();
      safeStop(FAULT_PI_HARD_STOP);
      sendAck("PI_HARD_STOP", true, "PI_HARD_STOP_LATCHED");
    } else {
      hardDisableMotors();
      armed = false;
    }
  }

  const ControlMode sampledMode = readControlMode();
  if (sampledMode != currentMode) {
    currentMode = sampledMode;
    safeStop(FAULT_MODE_CHANGE);
    sessionActive = false;
    sequenceActive = false;
    activeSession[0] = '\0';
    sendAck("MODE_CHANGE", true, "STOPPED_AND_DISARMED");
  }

  serviceLocalResetButton(now);
}

int16_t moveToward(int16_t current, int16_t target, uint32_t elapsedMs) {
  if (current == target) {
    return current;
  }
  int32_t step = (static_cast<int32_t>(cfg::SLEW_PERMILLE_PER_SECOND) *
                  static_cast<int32_t>(elapsedMs)) /
                 1000;
  if (step < 1) {
    step = 1;
  }
  if (target > current) {
    const int32_t candidate = static_cast<int32_t>(current) + step;
    return static_cast<int16_t>(candidate > target ? target : candidate);
  }
  const int32_t candidate = static_cast<int32_t>(current) - step;
  return static_cast<int16_t>(candidate < target ? target : candidate);
}

void serviceCommandWatchdogs(uint32_t now) {
  if (!armed) {
    return;
  }

  if (currentMode == ControlMode::AUTO_MODE) {
    if (static_cast<uint32_t>(now - lastPiFrameMs) >
        cfg::PI_LINK_TIMEOUT_MS) {
      safeStop(FAULT_PI_TIMEOUT);
      sendAck("WATCHDOG", false, "PI_HEARTBEAT_TIMEOUT");
      return;
    }
    if (motionDeadlineValid && deadlineReached(now, motionDeadlineMs)) {
      safeStop(FAULT_COMMAND_EXPIRED);
      sendAck("WATCHDOG", false, "DRIVE_TTL_EXPIRED");
      return;
    }
  }
#if !MIIT_BT_LEGACY_LATCHED_COMMANDS
  else if (static_cast<uint32_t>(now - lastManualCommandMs) >
           cfg::MANUAL_COMMAND_TIMEOUT_MS) {
    safeStop(FAULT_MANUAL_TIMEOUT);
    sendAck("WATCHDOG", false, "MANUAL_COMMAND_TIMEOUT");
#if MIIT_ENABLE_BLUETOOTH_MANUAL
    sendBluetoothReply("STOPPED: command timeout; send A to arm again");
#endif
  }
#endif
}

void serviceMotorControl(uint32_t now) {
  const uint32_t elapsed = now - lastControlMs;
  if (elapsed < cfg::CONTROL_PERIOD_MS) {
    return;
  }
  lastControlMs = now;

  if (!armed || !hardwareReadyToArm()) {
    hardDisableMotors();
    return;
  }

  for (uint8_t index = 0; index < WHEEL_COUNT; ++index) {
    wheelApplied[index] =
        moveToward(wheelApplied[index], wheelTarget[index], elapsed);
  }
  applyWheelOutputs();
}

#if MIIT_ENABLE_BLUETOOTH_MANUAL
int16_t manualSpeedPermille() {
  return static_cast<int16_t>(
      (static_cast<uint32_t>(manualPwmDuty) * 1000UL + 127UL) / 255UL);
}

bool armManual(uint32_t now) {
  clearTransientFaultsForArm();
#if MIIT_USE_PHYSICAL_MODE_SWITCH
  if (currentMode != ControlMode::MANUAL_MODE) {
    safeStop(FAULT_MODE_CHANGE);
    sendBluetoothReply("NACK: physical switch is not MANUAL");
    return false;
  }
#else
  if (currentMode == ControlMode::AUTO_MODE && armed) {
    sendBluetoothReply("NACK: send S first to stop AUTO mode");
    return false;
  }
  if (currentMode != ControlMode::MANUAL_MODE) {
    safeStop();
    currentMode = ControlMode::MANUAL_MODE;
    activeFaults &= ~FAULT_MODE_CHANGE;
  }
#endif
  if (!hardwareReadyToArm()) {
    safeStop();
    sendBluetoothReply("NACK: hardware not safe; check STATUS on USB serial");
    return false;
  }
  hardDisableMotors();
  armed = true;
  lastManualCommandMs = now;
  sendBluetoothReply("ACK: MANUAL ARMED at zero output");
  return true;
}

void handleManualCharacter(char command, uint32_t now) {
  command = static_cast<char>(toupper(static_cast<unsigned char>(command)));
  if (command == '\r' || command == '\n' || command == ' ') {
    return;
  }

  if (command == 'E') {
    estopLatched = true;
    requireFreshLocalResetGesture();
    safeStop(FAULT_REMOTE_ESTOP);
    sendBluetoothReply("ESTOP LATCHED: release hazards then hold local RESET");
    return;
  }
  if (command == 'S') {
    safeStop();
#if !MIIT_USE_PHYSICAL_MODE_SWITCH
    currentMode = ControlMode::MANUAL_MODE;
    activeFaults &= ~FAULT_MODE_CHANGE;
#endif
    sendBluetoothReply("STOPPED and disarmed");
    return;
  }
  if (command == 'A') {
    armManual(now);
    return;
  }

  if (command >= '0' && command <= '9') {
    const uint16_t digit = static_cast<uint16_t>(command - '0');
    manualPwmDuty = static_cast<uint8_t>(
        cfg::MIN_MANUAL_PWM_DUTY +
        (digit * (cfg::MAX_MANUAL_PWM_DUTY - cfg::MIN_MANUAL_PWM_DUTY)) / 9U);
    sendBluetoothReply("ACK: manual speed set");
    return;
  }
  if (command == 'Q') {
    manualPwmDuty = cfg::MAX_MANUAL_PWM_DUTY;
    sendBluetoothReply("ACK: manual speed set to 255");
    return;
  }

  const bool movementCommand = command == 'F' || command == 'B' ||
                               command == 'L' || command == 'R' ||
                               command == 'G' || command == 'I';
  if (!movementCommand) {
    safeStop(FAULT_PROTOCOL);
    sendBluetoothReply("NACK: unknown command; robot stopped");
    return;
  }

  if (!armed) {
#if MIIT_MANUAL_REQUIRE_ARM_COMMAND
    sendBluetoothReply("NACK: send A before movement");
    return;
#else
    if (!armManual(now)) {
      return;
    }
#endif
  }

  if (currentMode != ControlMode::MANUAL_MODE || !hardwareReadyToArm()) {
    safeStop();
    sendBluetoothReply("NACK: MANUAL mode is not safe");
    return;
  }

  const int16_t speed = manualSpeedPermille();
  switch (command) {
    case 'F':
      // forward(): L1+, L2+, R1+, R2+
      setWheelTargets(speed, speed, speed, speed);
      break;
    case 'B':
      // backward(): L1-, L2-, R1-, R2-
      setWheelTargets(-speed, -speed, -speed, -speed);
      break;
    case 'L':
      // moveLeft(): L1+, L2-, R1-, R2+
      setWheelTargets(speed, -speed, -speed, speed);
      break;
    case 'R':
      // moveRight(): L1-, L2+, R1+, R2-
      setWheelTargets(-speed, speed, speed, -speed);
      break;
    case 'G':
      // turnLeft(): L1-, L2-, R1+, R2+
      setWheelTargets(-speed, -speed, speed, speed);
      break;
    case 'I':
      // turnRight(): L1+, L2+, R1-, R2-
      setWheelTargets(speed, speed, -speed, -speed);
      break;
    default:
      return;
  }

  lastManualCommandMs = now;
#if !MIIT_BT_LEGACY_LATCHED_COMMANDS
  motionDeadlineMs = now + cfg::MANUAL_COMMAND_TIMEOUT_MS;
  motionDeadlineValid = true;
#endif
}

void serviceBluetoothManual(uint32_t now) {
  const bool connected = SerialBT.hasClient();
  if (bluetoothPreviouslyConnected && !connected &&
      currentMode == ControlMode::MANUAL_MODE && armed) {
    safeStop(FAULT_MANUAL_TIMEOUT);
    sendAck("BLUETOOTH", false, "BLUETOOTH_DISCONNECTED");
  }
  bluetoothPreviouslyConnected = connected;

  while (SerialBT.available() > 0) {
    handleManualCharacter(static_cast<char>(SerialBT.read()), now);
  }
}
#endif

bool configurePwmAndPins() {
  bool ok = true;
  for (MotorOutput *motor : motors) {
    pinMode(motor->in1Pin, OUTPUT);
    pinMode(motor->in2Pin, OUTPUT);
    digitalWrite(motor->in1Pin, LOW);
    digitalWrite(motor->in2Pin, LOW);
    ok = attachPwm(*motor) && ok;
    writePwm(*motor, 0);
  }
  return ok;
}

bool configureTaskWatchdog() {
#if ESP_IDF_VERSION_MAJOR >= 5
  esp_err_t status = esp_task_wdt_status(nullptr);
  esp_task_wdt_config_t watchdogConfig = {};
  watchdogConfig.timeout_ms = cfg::TASK_WATCHDOG_TIMEOUT_MS;
  watchdogConfig.idle_core_mask = 0;
  watchdogConfig.trigger_panic = true;
  if (status == ESP_ERR_INVALID_STATE) {
    const esp_err_t initialized = esp_task_wdt_init(&watchdogConfig);
    if (initialized != ESP_OK) {
      return false;
    }
    status = esp_task_wdt_status(nullptr);
  } else if (esp_task_wdt_reconfigure(&watchdogConfig) != ESP_OK) {
    return false;
  }
  if (status != ESP_OK) {
    const esp_err_t added = esp_task_wdt_add(nullptr);
    if (added != ESP_OK) {
      return false;
    }
  }
  return esp_task_wdt_status(nullptr) == ESP_OK;
#else
  const uint32_t roundedSeconds =
      (cfg::TASK_WATCHDOG_TIMEOUT_MS + 999U) / 1000U;
  const uint32_t timeoutSeconds = roundedSeconds < 1U ? 1U : roundedSeconds;
  const esp_err_t initialized = esp_task_wdt_init(timeoutSeconds, true);
  if (initialized != ESP_OK && initialized != ESP_ERR_INVALID_STATE) {
    return false;
  }
  const esp_err_t added = esp_task_wdt_add(nullptr);
  return added == ESP_OK || added == ESP_ERR_INVALID_ARG;
#endif
}

void feedTaskWatchdog() {
  if (taskWatchdogReady) {
    esp_task_wdt_reset();
  }
}

}  // namespace

void setup() {
  // Set safety inputs before any attempt to arm outputs.
  pinMode(cfg::ESTOP_LOOP_PIN, INPUT);
  pinMode(cfg::PI_HARD_STOP_PIN, INPUT);
  pinMode(cfg::MODE_SELECT_PIN, INPUT);
  pinMode(cfg::LOCAL_RESET_PIN, INPUT_PULLUP);

  pwmReady = configurePwmAndPins();
  hardDisableMotors();

  Serial.begin(cfg::SERIAL_BAUD);
  delay(100);

  currentMode = readControlMode();
  if (!pwmReady) {
    setFault(FAULT_PWM_INIT);
  }
#if !MIIT_ENABLE_MOTOR_OUTPUTS
  setFault(FAULT_OUTPUTS_DISABLED);
#endif
  if (!estopLoopHealthy()) {
    estopLatched = true;
    requireFreshLocalResetGesture();
    setFault(FAULT_ESTOP_INPUT);
  }
  if (piHardStopActive()) {
    estopLatched = true;
    requireFreshLocalResetGesture();
    setFault(FAULT_PI_HARD_STOP);
  }

  taskWatchdogReady = configureTaskWatchdog();
  if (!taskWatchdogReady) {
    setFault(FAULT_TASK_WATCHDOG);
  }

#if MIIT_ENABLE_BLUETOOTH_MANUAL
  SerialBT.begin(cfg::BLUETOOTH_NAME);
#endif

  lastControlMs = millis();
  lastStatusMs = millis();
  sendState("BOOT");
}

void loop() {
  const uint32_t now = millis();

  serviceSafetyInputs(now);
  servicePiSerial();
#if MIIT_ENABLE_BLUETOOTH_MANUAL
  serviceBluetoothManual(now);
#endif
  serviceCommandWatchdogs(now);
  serviceMotorControl(now);

  if (static_cast<uint32_t>(now - lastStatusMs) >= cfg::STATUS_PERIOD_MS) {
    lastStatusMs = now;
    sendState();
  }

  feedTaskWatchdog();
  delay(2);
}
