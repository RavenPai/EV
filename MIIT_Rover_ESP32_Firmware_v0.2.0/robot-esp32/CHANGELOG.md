# Changelog

## 0.2.0 — Espressif/manual-hardware correction

- Replaced the assumed GPIO map with the proven pins from the working manual
  program: PWM `14, 15, 23, 27` and direction pins `18/19, 21/22, 25/26,
  32/33`.
- Matched the proven per-motor forward polarity, including the different Right
  1 direction.
- Restored Bluetooth name `2023_IoT_TEAM`.
- Restored manual commands `F`, `B`, `L`, `R`, `G`, `I`, `S`, speed `0-9`, and
  `q=255`.
- Recognized the chassis as mecanum/omnidirectional rather than differential.
- Replaced AUTO left/right targets with ROS-sign `vx`, `vy`, `wz` targets and a
  four-wheel mixer reproducing the manual movement patterns.
- Restored the proven PWM configuration of 5 kHz and 8-bit duty while retaining
  compatibility code for Arduino-ESP32 core 2.x and 3.x.
- Added stop-on-Bluetooth-disconnect while retaining the original latched phone
  commands by default.

## 0.1.0 — Initial commissioning controller

- Added fail-safe boot, UART JSON framing, ARM/session/sequence/CRC validation,
  command TTL, Pi heartbeat watchdog, E-stop latch, mode handling, and a safe Pi
  serial test tool.
