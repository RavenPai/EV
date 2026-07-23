#pragma once

#include <Arduino.h>

// ---------------------------------------------------------------------------
// MIIT Rover hardware configuration
// Target board: classic ESP32 DevKit / ESP32-WROOM-32 from Espressif.
// Review every value against the real wiring before enabling motor output.
// ---------------------------------------------------------------------------

// The first upload is intentionally communication-only. Change this to 1 only
// after the E-stop circuit, motor-driver current rating, wiring, and wheel-raised
// test have all passed.
#define MIIT_ENABLE_MOTOR_OUTPUTS 1

// BluetoothSerial is Classic Bluetooth SPP and is available on the original
// ESP32, not on ESP32-C3/S2/S3/C6 boards. Set to 0 if it is unavailable.
#define MIIT_ENABLE_BLUETOOTH_MANUAL 1

// Compatibility with the proven 2023_IoT_TEAM phone controller:
//   1 = F/B/L/R/G/I remains active until S, another command, mode change, or
//       Bluetooth disconnect. This matches the user's working sketch.
//   0 = the movement character must be repeated before the timeout below.
#define MIIT_BT_LEGACY_LATCHED_COMMANDS 1

// The original phone controller has no separate A (arm) button. Leave this at
// 0 for compatibility; a valid movement character arms only in MANUAL mode and
// only after every configured safety input is healthy. Set to 1 when the phone
// controller has a dedicated A button.
#define MIIT_MANUAL_REQUIRE_ARM_COMMAND 0

// Safety input features. GPIO34/35/36 need external pull resistors because they
// do not provide internal pull-ups on the classic ESP32.
#define MIIT_REQUIRE_ESTOP_LOOP 1
#define MIIT_USE_PI_HARD_STOP_INPUT 0
#define MIIT_USE_PHYSICAL_MODE_SWITCH 0

namespace cfg {

constexpr uint32_t SERIAL_BAUD = 115200;
constexpr char FIRMWARE_VERSION[] = "0.2.0-mecanum-safe-bench";
constexpr char BLUETOOTH_NAME[] = "EvDelivery";

// Exact GPIO map from the user's proven manual sketch.
// L298N Driver 1, Left Motor 1.
constexpr uint8_t LEFT1_PWM = 14;
constexpr uint8_t LEFT1_IN1 = 18;
constexpr uint8_t LEFT1_IN2 = 19;
// Positive logical wheel motion must reproduce forward(): LOW, HIGH.
constexpr bool LEFT1_INVERT = true;

// L298N Driver 2, Right Motor 1.
constexpr uint8_t RIGHT1_PWM = 23;
constexpr uint8_t RIGHT1_IN1 = 25;
constexpr uint8_t RIGHT1_IN2 = 26;
// Positive logical wheel motion must reproduce forward(): HIGH, LOW.
constexpr bool RIGHT1_INVERT = false;

// L298N Driver 1, Left Motor 2.
constexpr uint8_t LEFT2_PWM = 15;
constexpr uint8_t LEFT2_IN1 = 21;
constexpr uint8_t LEFT2_IN2 = 22;
// Positive logical wheel motion must reproduce forward(): LOW, HIGH.
constexpr bool LEFT2_INVERT = true;

// L298N Driver 2, Right Motor 2.
constexpr uint8_t RIGHT2_PWM = 27;
constexpr uint8_t RIGHT2_IN1 = 32;
constexpr uint8_t RIGHT2_IN2 = 33;
// Positive logical wheel motion must reproduce forward(): LOW, HIGH.
constexpr bool RIGHT2_INVERT = true;

// Physical E-stop status loop:
//   GPIO34 -- external 10 kOhm pull-up to 3.3 V
//   GPIO34 -- normally-closed auxiliary E-stop contact -- GND
// Healthy = LOW. Pressed, broken wire, or disconnected = HIGH and latched stop.
// The E-stop's primary safety contacts must independently remove motor power or
// motor-driver enable; this GPIO is status feedback only.
constexpr uint8_t ESTOP_LOOP_PIN = 34;
constexpr uint8_t ESTOP_HEALTHY_LEVEL = LOW;

// Optional Pi hardware-permit line:
//   GPIO35 -- external 10 kOhm pull-up to 3.3 V
//   Pi/open-drain or optocoupler holds it LOW only while locally safe.
// HIGH/open = hard stop. Enable MIIT_USE_PI_HARD_STOP_INPUT only after wiring it.
constexpr uint8_t PI_HARD_STOP_PIN = 35;
constexpr uint8_t PI_HARD_STOP_ACTIVE_LEVEL = HIGH;

// Optional physical mode switch:
//   GPIO36 -- external 10 kOhm pull-up to 3.3 V
//   switch to GND = MANUAL, open/high = AUTO
constexpr uint8_t MODE_SELECT_PIN = 36;
constexpr uint8_t MANUAL_MODE_LEVEL = LOW;

// Local reset/acknowledge button: GPIO13 to GND; internal pull-up is used.
// Hold after releasing the physical E-stop to clear the latch. It never arms.
constexpr uint8_t LOCAL_RESET_PIN = 13;
constexpr uint8_t LOCAL_RESET_ACTIVE_LEVEL = LOW;
constexpr uint32_t LOCAL_RESET_HOLD_MS = 1000;

// These match the proven working sketch. Arduino-ESP32 2.x and 3.x LEDC APIs
// are both supported by the main firmware.
constexpr uint32_t PWM_FREQUENCY_HZ = 5000;
constexpr uint8_t PWM_RESOLUTION_BITS = 8;
constexpr uint16_t PWM_MAX_DUTY = (1U << PWM_RESOLUTION_BITS) - 1U;

// Four independent LEDC channels used only by Arduino-ESP32 2.x.
constexpr uint8_t LEFT1_CHANNEL = 0;
constexpr uint8_t LEFT2_CHANNEL = 1;
constexpr uint8_t RIGHT1_CHANNEL = 2;
constexpr uint8_t RIGHT2_CHANNEL = 3;

// AUTO commands are chassis vx/vy/wz permille values. The wheel mixer scales
// the resulting four targets to this raised-wheel commissioning limit.
constexpr int16_t MAX_AUTO_PERMILLE = 350;

// Manual PWM behavior exactly mirrors the working sketch:
// 0..9 maps from duty 100..255, and q means duty 255.
constexpr uint8_t MIN_MANUAL_PWM_DUTY = 100;
constexpr uint8_t MAX_MANUAL_PWM_DUTY = 255;
constexpr uint8_t DEFAULT_MANUAL_PWM_DUTY = 100;
constexpr int16_t SLEW_PERMILLE_PER_SECOND = 800;

// Pi sends DRIVE or HEARTBEAT every 50-100 ms. DRIVE has its own short TTL, so
// HEARTBEAT alone can never keep an old motion command alive.
constexpr uint32_t PI_LINK_TIMEOUT_MS = 300;
constexpr uint32_t MIN_COMMAND_TTL_MS = 50;
constexpr uint32_t MAX_COMMAND_TTL_MS = 300;
constexpr uint32_t MANUAL_COMMAND_TIMEOUT_MS = 300;

constexpr uint32_t CONTROL_PERIOD_MS = 10;
constexpr uint32_t STATUS_PERIOD_MS = 250;
constexpr uint32_t TASK_WATCHDOG_TIMEOUT_MS = 1000;
constexpr size_t SERIAL_LINE_MAX = 384;
constexpr size_t SESSION_MAX = 40;

}  // namespace cfg
