# MIIT Rover ESP32 Safe Motor Controller

This folder contains the corrected v0.2 ESP32 firmware for the MIIT campus
delivery rover. Its GPIOs, motor polarities, Bluetooth name, speed keys, and
movement keys come directly from the user's proven manual sketch.

It assumes:

- classic ESP32 DevKit / ESP32-WROOM-32;
- four brushed DC motors with the mecanum/omnidirectional movement pattern
  demonstrated by the working `moveLeft()` and `moveRight()` functions;
- two L298N boards, one channel per motor;
- Raspberry Pi connected to the ESP32 USB serial port at 115200 baud;
- Bluetooth Classic SPP manual control named `2023_IoT_TEAM`;
- a real physical E-stop; and
- an optional physical AUTO/MANUAL switch. Software mode handoff is enabled by
  default because the supplied working hardware did not include a mode-switch
  pin.

## What this release does

- Starts and restarts with every motor output at zero.
- Accepts the Pi bridge's timed JSON `STOP` frame and distinct latching
  `ESTOP` frame.
- Requires version, session, increasing sequence, short TTL, and CRC-16 for
  ARM, HEARTBEAT, and DRIVE.
- Converts AUTO-mode `vx`, `vy`, and `wz` chassis targets into the exact four
  wheel patterns used by the working manual program.
- Preserves the working Bluetooth keys: `F B L R G I S`, speed `0-9`, and `q`.
- Uses a separate DRIVE expiry, so heartbeat traffic cannot preserve an old
  movement command.
- Stops and disarms on Pi timeout, command expiry, bad JSON, bad CRC, old
  sequence, mode change, or Bluetooth disconnect. An optional repeated-command
  watchdog can replace legacy latched Bluetooth behavior.
- Latches physical, Pi hard-stop, and software ESTOP conditions.
- Allows an E-stop latch to be cleared only with a local held reset button;
  clearing never arms the robot.
- Limits speed and applies an acceleration/deceleration slew limit.
- Supports Arduino-ESP32 2.x and 3.x LEDC APIs.
- Publishes JSON ACK/NACK and state frames to the Raspberry Pi.
- Subscribes the Arduino loop task to the ESP32 task watchdog.

This release does **not** provide wheel-velocity PID, encoder odometry,
overcurrent measurement, motor-temperature measurement, stall detection, or
Nav2 integration. It is a safe communication and raised-wheel commissioning
controller, not final unattended campus firmware.

The checked-in commissioning configuration enables unauthenticated Classic
Bluetooth SPP manual control and legacy latched movement for compatibility with
the proven bench sketch. Do **not** use those defaults for production or an
unattended rover. Disable `MIIT_ENABLE_BLUETOOTH_MANUAL`, or first implement and
review authenticated pairing, a physical mode selector, explicit arming, and a
short repeated-command timeout.

## Critical motor-driver check

The project report lists 24 W and 48 W, 12 V motors. Those figures imply about
2 A and 4 A at rated load before considering the much larger stall current.
The official L298 data sheet gives **2 A DC per channel as an absolute maximum**
and shows a large bridge voltage drop at high current. Therefore the two L298N
modules in the report are not a credible driver for a 4 A motor and may also be
unsafe for a nominal 2 A motor once startup/stall current and module cooling are
included.

Do not select a replacement driver from motor watts alone. Measure or obtain
each motor's stall current, then select drivers, wiring, fuses, connectors, and
the E-stop contactor with margin. Do not enable this firmware's motor outputs
until that check is complete.

References:

- [ST L298 data sheet](https://www.st.com/resource/en/datasheet/l298.pdf)
- [Arduino-ESP32 LEDC API](https://docs.espressif.com/projects/arduino-esp32/en/latest/api/ledc.html)
- [Arduino-ESP32 2.x to 3.x migration guide](https://docs.espressif.com/projects/arduino-esp32/en/latest/migration_guides/2.x_to_3.0.html)
- [Espressif BluetoothSerial notes](https://github.com/espressif/arduino-esp32/blob/master/libraries/BluetoothSerial/README.md)
- [ArduinoJson 7 deserialization API](https://arduinojson.org/v7/api/json/deserializejson/)
- [ESP-IDF Task Watchdog](https://docs.espressif.com/projects/esp-idf/en/stable/esp32/api-reference/system/wdts.html)

## Files

| File | Purpose |
| --- | --- |
| `MIIT_Rover_ESP32/MIIT_Rover_ESP32.ino` | ESP32 firmware |
| `MIIT_Rover_ESP32/config.h` | Pins, limits, safety features, and enable flags |
| `tools/pi_serial_test.py` | Safe STOP/STATUS and explicit raised-wheel Pi test |
| `tests/test_protocol.py` | Host-side CRC and JSON framing tests |
| `CHANGELOG.md` | Differences between the initial and corrected firmware |

## Wiring map supplied in `config.h`

Remove all four L298N ENA/ENB jumpers before connecting the PWM GPIOs.

| Rover function | ESP32 GPIO | L298N terminal |
| --- | ---: | --- |
| Left 1 PWM | 14 | Driver 1 ENA_LEFT1 |
| Left 1 direction | 18, 19 | Driver 1 IN1_LEFT1, IN2_LEFT1 |
| Left 2 PWM | 15 | Driver 1 ENA_LEFT2 |
| Left 2 direction | 21, 22 | Driver 1 IN1_LEFT2, IN2_LEFT2 |
| Right 1 PWM | 23 | Driver 2 ENB_RIGHT1 |
| Right 1 direction | 25, 26 | Driver 2 IN1_RIGHT1, IN2_RIGHT1 |
| Right 2 PWM | 27 | Driver 2 ENB_RIGHT2 |
| Right 2 direction | 32, 33 | Driver 2 IN1_RIGHT2, IN2_RIGHT2 |
| E-stop status loop | 34 | External circuit, not L298N |
| Optional Pi hard-stop permit | 35 | External circuit, disabled initially |
| Optional AUTO/MANUAL switch | 36 | Disabled initially in `config.h` |
| Local reset button | 13 | Button to GND |

GPIO34, GPIO35, and GPIO36 have no internal pull-up on a classic ESP32. Fit the
external 10 kOhm resistors described in `config.h`; never leave them floating.
GPIO15 is an ESP32 strapping pin. It is retained because the supplied sketch is
already proven with PWM on GPIO15, but the robot must still be power-cycled and
boot-tested with the complete driver wiring attached.

### Physical E-stop

Use a dual-contact or safety-rated arrangement:

1. The primary normally-closed contact removes propulsion power or the common
   driver-enable/contactor signal independently of the ESP32, Pi, Internet, and
   firmware.
2. An auxiliary normally-closed status contact connects GPIO34 to GND.
3. Add an external 10 kOhm pull-up from GPIO34 to ESP32 3.3 V. Healthy is LOW;
   pressed, disconnected, or broken wire is HIGH and latches a stop.

The web ESTOP and GPIO34 software reading are additional layers. Neither is the
physical power-removal circuit.

### Power

- Do not power a motor from an ESP32 or Raspberry Pi pin/USB port.
- Do not power the Pi/ESP32 from the noisy L298N module's 5 V regulator.
- Use a correctly rated 5 V buck supply for logic and a separately fused motor
  supply.
- Join logic grounds at a deliberate common point unless an isolated interface
  is used.
- Add external pull-downs to every motor enable/direction line so a resetting
  ESP32 cannot leave an L298N input floating.

## First upload with Arduino IDE

1. Disconnect motor battery/power. Raise all wheels anyway.
2. Install Arduino IDE 2.x.
3. In Boards Manager, install the current stable **esp32 by Espressif Systems**.
4. In Library Manager, install **ArduinoJson by Benoit Blanchon, major version
   7**.
5. Open `MIIT_Rover_ESP32/MIIT_Rover_ESP32.ino`.
6. Select **ESP32 Dev Module** only if the board is a classic DevKit/WROOM-32.
7. Open `config.h` and check every pin against the actual wires.
8. Leave `MIIT_ENABLE_MOTOR_OUTPUTS 0` for this first upload.
9. Compile, select the ESP32 port, and upload.
10. Open Serial Monitor at 115200 baud. Select newline line ending.

Expected boot output is one JSON `BOOT` frame followed by a `STATE` frame every
250 ms. With the default build, `motorOutputsEnabled` is false and fault bit 8
is set. If the E-stop status loop is not yet wired, `estopLatched` is also true.
That is intentional.

The firmware is written for a classic ESP32. If compilation reports that
Bluetooth SPP is unavailable, the board is probably C3/S2/S3/C6 or Bluetooth is
disabled. Set `MIIT_ENABLE_BLUETOOTH_MANUAL 0`; do not change the error check in
the firmware.

## Raspberry Pi STOP and STATUS test

On the Pi, keep motor power disconnected and find the stable device path:

```bash
ls -l /dev/serial/by-id/
```

Activate the rover Python environment containing `pyserial`, then run:

```bash
python3 tools/pi_serial_test.py \
  --port /dev/serial/by-id/YOUR_ESP32_DEVICE
```

The script waits for the ESP32's USB reset, sends the compatible legacy STOP,
requests STATUS, and sends STOP again before closing. It never moves the robot
without two extra explicit flags.

Do not run this script while `agent.py`, Arduino Serial Monitor, `screen`, or
another controller owns the same port. Exactly one process may own the ESP32
serial device.

## Enabling a raised-wheel motion test

Do this only after all of these are true:

- the motor driver is correctly rated from measured motor stall current;
- each motor channel is fused and wired correctly;
- the physical E-stop cuts propulsion independently;
- GPIO34 status wiring and the local reset button work;
- motor power is initially disconnected and every wheel is raised;
- an operator can reach the physical E-stop;
- each motor direction has been checked one motor at a time.

Then change this line in `config.h` and upload again:

```cpp
#define MIIT_ENABLE_MOTOR_OUTPUTS 1
```

For a one-second low target with every wheel raised:

```bash
python3 tools/pi_serial_test.py \
  --port /dev/serial/by-id/YOUR_ESP32_DEVICE \
  --motion-test \
  --wheels-raised-and-area-clear \
  --vx 100 \
  --vy 0 \
  --wz 0 \
  --duration 1.0
```

The tester sends DRIVE repeatedly faster than its TTL. It sends STOP in a
`finally` block even after Ctrl+C. The ESP32 also disarms if the process, USB
cable, or command stream disappears.

If a wheel turns the wrong direction, do not swap code randomly. Disconnect
motor power, identify the specific motor, and either swap its two motor leads or
change only its `*_INVERT` value in `config.h`. Test each corner again.

The supplied inversion values already reproduce the exact `HIGH/LOW` patterns
in the proven manual sketch. Change them only if the physical motor wires have
also changed.

## Manual mode over Bluetooth

Bluetooth manual control is a supervised fallback for a classic ESP32. It is
not Internet manual driving. Pair/connect to `2023_IoT_TEAM`, exactly as with
the supplied working program.

The default configuration keeps compatibility with that program:

```cpp
#define MIIT_BT_LEGACY_LATCHED_COMMANDS 1
#define MIIT_MANUAL_REQUIRE_ARM_COMMAND 0
#define MIIT_USE_PHYSICAL_MODE_SWITCH 0
```

Therefore a valid direction key arms MANUAL mode when local safety conditions
are healthy, and the direction remains active until another direction, `S`, an
E-stop, mode handoff, or Bluetooth disconnection. `A` is additionally supported
for a future controller with a dedicated arm button.

| Character | Action |
| --- | --- |
| `F` | Forward |
| `B` | Reverse |
| `L` | Move/strafe left |
| `R` | Move/strafe right |
| `G` | Turn left |
| `I` | Turn right |
| `0` ... `9` | Map PWM duty from 100 ... 255 exactly like the working sketch |
| `q` | PWM duty 255 |
| `A` | Explicit arm at zero output, optional |
| `S` | Immediate STOP and disarm |
| `E` | Latching software ESTOP |

For safer repeated-command behavior, change
`MIIT_BT_LEGACY_LATCHED_COMMANDS` to `0`; the phone app must then repeat its
direction key faster than 300 ms. Bluetooth disconnection stops and disarms in
both configurations.

After `E`, release every hardware stop condition and hold the local GPIO13 reset
button for one second. Reset clears the latch but never arms the motors.

## Raspberry Pi auto-mode protocol

Each frame is one compact JSON object followed by `\n` at 115200 baud.

STOP and ESTOP are deliberately accepted without CRC because an attacker or
corrupted sender that can only add a stop cannot create motion:

```json
{"v":1,"cmd":"STOP","ttlMs":300}
{"v":1,"cmd":"ESTOP"}
```

Motion-capable commands use this schema:

```json
{"v":1,"session":"pi-boot-uuid","seq":1,"cmd":"ARM","vx":0,"vy":0,"wz":0,"ttlMs":200,"crc16":"ABCD"}
```

The CRC is CRC-16/CCITT-FALSE, polynomial `0x1021`, initial value `0xFFFF`, no
reflection, no final XOR. It covers this ASCII canonical string:

```text
v|session|seq|cmd|vx|vy|wz|ttlMs
```

Example vectors are printed with:

```bash
python3 tools/pi_serial_test.py --print-vectors
```

Allowed protected commands:

| Command | Behavior |
| --- | --- |
| `ARM` | Establishes a Pi boot session and arms at zero output only if safe |
| `HEARTBEAT` | Refreshes Pi link only; never refreshes a DRIVE target |
| `DRIVE` | Sets signed mecanum chassis `vx/vy/wz` targets until its TTL expires |

The chassis signs follow the ROS coordinate convention:

| Target | Positive | Negative |
| --- | --- | --- |
| `vx` | Forward | Backward |
| `vy` | Move left | Move right |
| `wz` | Turn left/counter-clockwise | Turn right/clockwise |

The firmware mixes those values into the same logical wheel signs as the
working Bluetooth functions:

| Movement | Left 1 | Left 2 | Right 1 | Right 2 |
| --- | ---: | ---: | ---: | ---: |
| Forward / `F` | + | + | + | + |
| Backward / `B` | - | - | - | - |
| Move left / `L` | + | - | - | + |
| Move right / `R` | - | + | + | - |
| Turn left / `G` | - | - | + | + |
| Turn right / `I` | + | + | - | - |

The Pi should send a valid frame every 50-100 ms. `DRIVE` values are limited by
`MAX_AUTO_PERMILLE`. Any expired, duplicate, old-sequence, wrong-session,
overspeed, malformed, or CRC-failed motion frame stops and disarms the robot.

After a STOP, timeout, or rejected frame, send a new valid ARM at zero output
before DRIVE. If the Pi process restarts, generate a new session UUID. Remote
ESTOP cannot be cleared over UART.

With the default software mode handoff, a valid Pi ARM changes a stopped MANUAL
controller to AUTO. If manual motion was active, the first ARM stops it and is
rejected; send a new-sequence ARM after confirming the stop. Bluetooth `S`
immediately stops AUTO and returns control to MANUAL. For field use, wire the
GPIO36 selector and set `MIIT_USE_PHYSICAL_MODE_SWITCH 1` so mode ownership is
physically explicit.

## Fault bits

`activeFaults` is the current/latching bit mask. `faultHistory` remains set until
the ESP32 restarts so that intermittent failures are visible.

| Decimal bit | Meaning |
| ---: | --- |
| 1 | Physical E-stop input/open loop |
| 2 | Optional Pi hard-stop input |
| 4 | Remote/Bluetooth ESTOP |
| 8 | Motor outputs disabled at compile time |
| 16 | PWM initialization failed |
| 32 | Task watchdog setup failed |
| 64 | Pi heartbeat timeout |
| 128 | DRIVE TTL expired |
| 256 | Malformed JSON |
| 512 | Protocol/limit error |
| 1024 | CRC error |
| 2048 | Session/sequence error |
| 4096 | Physical mode change |
| 8192 | Bluetooth manual timeout |

## Integration rule for the Raspberry Pi

The present `robot-pi/agent.py` can use this firmware's legacy STOP behavior,
but it is not a motor base controller. The old `navigation.py` is incompatible
because it sends text such as `Go Straight` and commands forward motion when
sign detection is lost.

The current agent keeps the cases separate: cloud `PAUSE` sends
`{"v":1,"cmd":"STOP","ttlMs":300}`, while cloud `ESTOP` first sends
`{"v":1,"cmd":"ESTOP"}`. The latter creates the ESP32 latch and intentionally
has no TTL. Clearing that latch requires the local reset button after all hard
stop inputs are healthy; UART cannot clear or arm it.

For the final system, exactly one Pi process must own the serial port. That
process should scale ROS 2 `/cmd_vel` (`linear.x`, `linear.y`, `angular.z`) into
the firmware's `vx/vy/wz` permille fields, generate protected frames, read
acknowledgements, and publish ESP32 health. The MQTT agent and mission manager
must request actions through that single local base controller rather than
opening the serial port themselves.

## Required work before autonomous campus use

1. Replace/verify the motor driver from real stall-current measurements.
2. Add correctly specified wheel encoders for usable mecanum odometry.
3. Implement and tune closed-loop wheel-velocity PID.
4. Return measured velocity and encoder counts to the Pi.
5. Add driver current/temperature feedback and stalled-wheel detection.
6. Validate a physical power-removal E-stop and braking/stopping distance.
7. Integrate the single Pi serial owner with ROS 2 odometry and `/cmd_vel`.
8. Complete LiDAR, localization, Nav2, geofence, and recovery tests.

Do not use open-loop PWM values from this firmware as odometry, and do not run
unattended campus delivery trials with this commissioning release.
