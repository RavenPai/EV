# Remaining Raspberry Pi, ESP32, Navigation, and Physical Tasks

Updated: 21 July 2026

This file contains only robot-side installation, configuration, commissioning,
and physical validation that remains. Source-code work and cloud/dashboard
work belong in [Remainding.md](Remainding.md). Verified completed Pi work is
kept in [DoneRaspberrypi.md](DoneRaspberrypi.md).

A task remains here if any required checkbox or pass-evidence item is missing.
Move only the exact verified Pi task title, with dated redacted evidence, to
`DoneRaspberrypi.md`. Laptop/cloud work stays in `Remainding.md`, and source
implementation or unit tests alone never complete deployment or hardware gates.

The historical setup guide is used only as a phase/step index. Its old
`pi-agent-1.1.0`, single `mission_request.json`, Ubuntu 24.04/Jazzy, and
STOP-only examples must not replace the current `pi-agent-1.3.0` contracts.

## Safety and evidence rules

- Keep propulsion power disconnected until Step P16 explicitly allows a
  raised-wheel test.
- Use a physical E-stop that removes propulsion power or driver enable without
  depending on the Pi, network, broker, or web application.
- Keep a human operator at the physical E-stop for every powered test.
- Never expose credentials, private addresses, private keys, or exact device
  identifiers in commands copied to reports or screenshots.
- Record date, laptop commit, deployed hashes, firmware build/hash, tester,
  result, measurements, and redacted evidence for each gate.
- A failed required gate returns the rover to STOP and blocks later powered
  work. The optional host-hardening appendix may be scheduled separately.

## Phase 1 — Reconcile and reverify the installed bridge

### Step P1 — Reconcile the Pi checkout after the laptop commit

Depends on: Laptop Step L3.

The current running files were installed directly from the laptop working tree,
so the older Pi Git checkout is intentionally dirty.

Laptop Step L3 comparison baseline (SHA-256) for implementation commit
`4f77166dc7e78ff366ec5c71435d034b44fa2594`:

| File | Expected SHA-256 |
| --- | --- |
| `robot-pi/agent.py` | `b79f87a22e3f8dc1f2d0cbb07e035878b5e62e3366549952822519730ea744da` |
| `robot-pi/local_store.py` | `7fe1c6d841469de570d474265c572279b7ee50e1972d3ba564c2d0dc31a75182` |
| `robot-pi/message_contract.py` | `2b3abba6ace17fc33f8d6ca700bc3de473c6657461471166add5282f0d0914c5` |
| `robot-pi/miit-rover-agent.service` | `082776694e6ab09edd401aa43f20cd2dbef7db5e809c82d2d5fc005842522008` |
| `robot-pi/requirements.txt` | `a8e8a737ade78e28ceba50a441fdd77a50e1651d5dd64b34ab293a2d1b1bdef0` |
| `robot-pi/robot.env.example` | `d408b79de63cc864d56f45a1890b5b338019a1f97a2219452fcfc9426b1df5a6` |
| `robot-pi/test_agent.py` | `eeb87c25a034d5d4940fd0c2e64f1c25e159af46ceb55f241f0c30c31e9bb2b6` |
| `robot-pi/test_local_store.py` | `5e5c6bce737e62f860038ac9bb4b4625f0050876eca9555e1e880900d8ecd2a8` |
| `robot-pi/test_message_contract.py` | `8b29b1b0f1684b8edd6f57cc71ca870d1ffa8bba86e30e30c918dd76b234ae02` |

These are comparison inputs, not proof of the Pi's present state. Recompute the
installed hashes on the Pi before changing its checkout or deployed files.

- [ ] Fetch the pushed commit without overwriting the running deployment.
- [ ] Compare the committed bridge files with the installed file hashes.
- [ ] Preserve the root-only rollback backup.
- [ ] If hashes match, reconcile the checkout metadata during a safe service
  window.
- [ ] If hashes differ intentionally, stage, test, and install the committed
  bundle through the same backup/automatic-rollback procedure before
  reconciling Git.
- [ ] Re-run all 27 Pi tests as `rover`.
- [ ] Re-run Python compilation, systemd verification, service restart, MQTT
  connection, subscription, and restart-count checks.

Do not use a destructive Git reset or checkout merely to make the tree appear
clean. A known-good deployment must never be overwritten without a tested
replacement and rollback path.

Pass evidence: the running files and pushed commit match, tests pass, the
service is enabled/active, MQTT is ready, and the observed restart count is
stable.

### Step P2 — Establish the live ESP32 serial port-open state

The persistent device link and permissions have been observed, while the
running bridge logged serial unavailable during its earlier startup. Recheck
with propulsion power disconnected.

- [ ] Confirm the configured persistent link resolves to the intended ESP32.
- [ ] Unplug/replug once and confirm the persistent identity remains stable.
- [ ] Confirm `rover` access through `dialout`.
- [ ] Confirm no serial monitor, ModemManager instance, navigation process, or
  second bridge owns the port.
- [ ] Restart the bridge only while physically safe and confirm its successful
  serial port-open log.

The current bridge port-open log does not prove an ESP32 BOOT frame, ACK, or
working firmware exchange. Direct firmware evidence belongs to Step P6;
production gateway evidence belongs to Steps P8–P9.

Pass evidence: the correct device persists, exactly one intended process owns
it, and the bridge can open it without a restart loop.

## Phase 2 — Complete motor-disconnected cloud/bridge verification

### Step P3 — Guide Step 13.3: verify duplicate and rejection safety

Depends on: Laptop Steps L5 and L8 being complete and the Step L7
connector/action configuration being saved. Keep motor power disconnected. Use
a temporary least-privilege test publisher, never the robot's MQTT identity.

Test:

- On the physical identity, exact duplicate/conflict cases use only a
  structurally valid but already expired envelope, so every outcome is rejected
  and side-effect-free.
- Any accepted-command duplicate test runs only against the isolated
  non-physical test identity, process, and state directory with no physical
  serial/controller path.
- Expired command.
- Wrong robot identity.
- Unsupported command.
- Malformed or oversized JSON.
- Wrong QoS.
- Retained command.

Pass evidence:

- Identical rejected physical duplicates preserve one durable rejection and
  create no inbox request or side effect.
- An accepted isolated-test duplicate creates at most one isolated inbox record
  and no physical side effect.
- Invalid messages are safely rejected and cannot arm motion.
- The original durable outcome is retained across reconnect and reboot.
- ACK outbox files survive failure and move to archives only after broker
  acceptance. Truthful physical event durability is tested separately in P5.
- Temporary publisher access is removed after the test.

This evidence contributes to Laptop Step L9.

### Step P4 — Guide Step 13.4: verify presence, offline, reconnect, and reboot

Depends on: Laptop Steps L5 and L8 being complete and the Step L7
connector/action configuration being saved. Keep motor power disconnected.

1. Record service state, PID, and restart count.
2. Stop and start the bridge in a controlled safe window.
3. Interrupt and restore network access.
4. Reboot once.
5. Confirm MQTT reconnect and accepted command subscription after recovery.
6. Confirm laptop-side database/frontend online and offline observations.

Pass evidence: timestamped Pi logs and laptop database evidence agree; the
service does not enter a restart loop. Cloud offline status is visibility only
and is not counted as the local motor-stop mechanism.

This evidence contributes to Laptop Step L9.

### Step P5 — Guide Step 14.2: verify the durable event return path

Depends on: Laptop Steps L5 and L8 being complete and the Step L7
connector/action configuration being saved.

- [ ] Atomically enqueue only events that actually occurred during the
  motor-disconnected physical test, using real UUIDs.
- [ ] Preserve the same `eventId` and content across retry.
- [ ] Reboot/reconnect during one retry.

Pass evidence: each truthful physical event file moves once from event outbox
to archive and has one matching hosted event; retry cannot create a duplicate.
Arbitrary delivery-event sequences remain in Laptop Step L9's isolated test,
not in the physical Pi identity.

This evidence contributes to Laptop Step L9.

## Phase 3 — Commission firmware, electrical safety, and local serial control

### Step P6 — Compile and flash the reviewed ESP32 firmware

Depends on: Laptop Steps L17 and L19. Flash the exact immutable binary whose
binary hash is recorded in the L19 release manifest. If compilation occurs on
the flashing host, prove the produced binary hash matches that manifest before
flashing. Keep propulsion power disconnected and verify the real board/pin map.

- [ ] Record compiler/toolchain versions plus source and binary hashes.
- [ ] Compile successfully for the exact board.
- [ ] Flash the intended device.
- [ ] Verify BOOT/STATE, firmware version, outputs-disabled state, STOP/STATUS
  response, matching ACK, and local reset semantics.

Pass evidence: build and flash logs are successful, protocol frames match the
reviewed build, and every motor output measures inactive.

### Step P7 — Install and de-energized-check independent power safety

Before any motor-power test:

- [ ] Obtain manufacturer-rated motor stall current and use it to size motor
  drivers, fuses, conductors, connectors, supplies, and the E-stop/contactor
  with reviewed margin.
- [ ] Install an independent propulsion power-removal or driver-enable E-stop.
- [ ] Install the auxiliary E-stop status loop, output pull-downs, and local
  reset input.
- [ ] Test continuity and E-stop logic without powering propulsion.

Do not intentionally stall a motor or assembled wheel as a casual measurement.
If measured stall-current evidence is required, use a separately approved,
fused, current-limited bench procedure after the electrical safety review.

Pass evidence: reviewed diagram/BOM, ratings, continuity, and de-energized
control measurements prove the independent interruption path and safe reset
logic. This step does not claim powered wheel stopping; physical interruption
and stopping time are measured in Steps P16–P17.

### Step P8 — Deploy one serial-owner/base-controller service

Depends on: Laptop Steps L16 and L19 plus Pi Steps P6–P7. Install only the
serial-controller artifact in the L19 manifest. Keep propulsion power
disconnected.

- [ ] Make one process the only ESP32 serial owner.
- [ ] Route bridge PAUSE/ESTOP and navigation velocity through protected local
  IPC.
- [ ] Verify BOOT handshake, boot session, sequence, CRC, TTL, ACK/NACK
  matching, state/fault parsing, reconnect, and safe shutdown.
- [ ] Kill the owner process and unplug serial.

Pass evidence: owner inspection always shows one intended PID; safety commands
reach the reviewed firmware; stale session/sequence, bad CRC, expired frames,
cable removal, and process death make the controller report STOP/DISARM and
inactive output signals within the protocol target. Powered stopping evidence
is deferred to Steps P16–P17.

### Step P9 — Complete the motor-disconnected protocol/watchdog campaign

Depends on: Pi Steps P6–P8.

Verify:

- STOP after boot/reset.
- Explicit arm preconditions.
- HEARTBEAT timeout.
- DRIVE TTL timeout.
- CRC, session, sequence, and command rejection.
- ACK/NACK correlation.
- ESTOP latch.
- Physical release plus local reset.
- Serial-unplug and Pi-process-loss behavior.
- Battery/current/temperature/stall fault inputs supported by the hardware.

Pass evidence: measured protocol timeouts meet the written target, every
invalid or lost-control case produces STOP/DISARM state and inactive output
signals, and reset never enables an output. Physical wheel stopping is not
claimed until Steps P16–P17.

## Phase 4 — Deploy the persistent mission manager

### Step P10 — Deploy guide Steps 15.1–15.5

Depends on: Laptop Steps L7, L15, and L19 plus Pi Steps P8–P9. Install only the
mission-manager artifact in the L19 manifest.

- [ ] Install the tested mission-manager package.
- [ ] Before enabling it, inspect the production inbox and quarantine every
  pre-existing test/legacy record that lacks preserved `issuedAt`/`expiresAt`;
  never allow such a record to be consumed later.
- [ ] Consume current per-command inbox files in deterministic order.
- [ ] Confirm `issuedAt`/`expiresAt` survive the inbox handoff and are checked
  again immediately before every mission/control side effect.
- [ ] Confirm ESTOP/PAUSE priority and atomic move to command archive.
- [ ] Confirm persisted mission and command state survives process kill/reboot
  without repeating motion, events, or cargo actions.
- [ ] Verify BOOT_SAFE, IDLE, validation, navigation, waiting, return, pause,
  fault, and ESTOP states with motors disconnected.
- [ ] Verify atomic state-file generation with labelled installed-test fixtures
  and truthful linked event production. The production publisher must suppress
  an incomplete physical snapshot; hosted physical state proof is Step P14.
- [ ] Verify RESUME keeps motion disarmed until local reset and fresh safe
  telemetry, then publishes one linked `RESUMED` event.
- [ ] Install the hardened systemd unit only after installed tests pass.

Pass evidence: installed tests cover every transition/crash point, systemd
verification passes, the service runs unprivileged, reboot returns safely, and
cloud delivery state changes only from valid robot events.

Laptop Step L9 covers the current bridge/cloud regression. Production
mission-manager and navigation acceptance continues in Steps P20–P22.

## Phase 5 — Install navigation and sensors without powered motion

### Step P11 — Select and install the OS-compatible ROS/navigation stack

Depends on: Laptop Steps L18 and L19. Install only the pinned navigation
manifest released by L19.

The verified Pi OS is Ubuntu 26.04 arm64. Do not install the archived guide's
Ubuntu 24.04 Jazzy package set. Recheck the official ROS documentation at the
time of installation. Current compatible documentation:

https://docs.ros.org/en/lyrical/Installation/Alternatives/Ubuntu-Install-Binary.html

- [ ] Install the pinned supported ROS distribution headlessly.
- [ ] Install only reviewed/pinned navigation and driver dependencies.
- [ ] Record exact package and source revisions.
- [ ] Verify the ROS CLI and a basic local publisher/subscriber.

Pass evidence: the installation is reproducible for the actual OS/architecture
and has no mixed ROS distributions.

### Step P12 — Identify and commission LiDAR and camera

- [ ] Assign separate persistent identities to ESP32, LiDAR, and camera.
- [ ] Configure the exact LiDAR model, baud rate, channel type, range, scan
  frequency, frame, and mounting orientation.
- [ ] Verify stable scan rate, plausible ranges, reconnect behavior, and no
  serial-path collision.
- [ ] Verify a headless camera stream through a stable path.

Pass evidence: device paths survive reconnect/reboot, `/scan` is stable with
plausible moved-object ranges, and the camera streams headlessly without
sending video through MQTT.

### Step P13 — Install static transforms and zero-motion odometry plumbing

- [ ] Install measured static sensor transforms.
- [ ] Configure odometry to own the dynamic `odom -> base_link` transform and
  SLAM/localization to own the dynamic `map -> odom` transform; never publish
  either one as a static transform merely to complete the tree.
- [ ] With the rover stationary, verify dynamic `odom -> base_link` and the
  measured static sensor frames coexist correctly. `map -> odom` is not a gate
  until mapping/localization in Step P19.
- [ ] Verify one publisher owns each transform and inspect the frame graph.
- [ ] Confirm zero-motion encoder/IMU data is plausible.

Powered encoder sign, scale, and odometry validation is deferred to Steps
P16–P18.

Pass evidence: the static frame graph has no missing/multiple-parent sensor
frames and zero-motion data is stable.

### Step P14 — Complete Guide Step 14.1 with truthful physical state

Depends on Pi Steps P6, P10, and P12–P13 plus Laptop Steps L5, L7, and L8.
Do not start until real producers can supply every required numeric and enum
field in `robot_state.json`.

This guide-index task is intentionally deferred: its required physical values
cannot be proved safely during the earlier cloud-only phase.

- [ ] Atomically write one fresh snapshot using the current
  `message_contract.py` schema and a timezone-aware observation time.
- [ ] Use actual measured battery, signal, speed, and motor-temperature values;
  never substitute zero, nominal, or guessed values for an unavailable sensor.
- [ ] Report unverified LiDAR, camera, or ESP32 as `OFFLINE`/`WARNING`, never
  `OK`, and keep overall readiness non-ready until every required gate passes.
- [ ] Test fresh publication, local stale/malformed rejection, and recovery to
  a fresh truthful snapshot.
- [ ] Confirm hosted ingestion updates only the physical robot row and cannot
  overwrite newer telemetry with stale data.

Pass evidence: the Pi publication attempt, broker/webhook result, hosted row,
and direct physical measurements agree. The Pi state publisher alone does not
wait for or archive a broker PUBACK, so hosted receipt is checked separately.

### Step P15 — Approve the powered-test hazard and risk plan

This is a required gate before Steps P16 and later powered work.

- [ ] Complete a hazard analysis/FMEA for propulsion, battery, fire, pinch,
  runaway, collision, communications, software, and human-operation risks.
- [ ] Define maximum test energy, speed, acceleration, and stopping-distance
  limits.
- [ ] Define operator/test-lead roles, E-stop placement, commands, and abort
  criteria.
- [ ] Define battery charging/fire response and electrical isolation.
- [ ] Define test-zone barriers, geofence, weather, terrain, slope, pedestrian,
  and communications limits.
- [ ] Approve the step-by-step test procedure and evidence form.

Pass evidence: a named reviewer approves the written plan and every operator
can demonstrate the E-stop/abort procedure.

## Phase 6 — Ordered powered and end-to-end acceptance

No powered portion of a later step may run merely because its software or
configuration appeared earlier in this document.

### Step P16 — Raised-wheel motor commissioning

Requires Pi Steps P6–P9 and P15, including the single serial-owner controller,
independent E-stop, and motor-disconnected watchdog campaign.

1. Raise and mechanically secure the chassis.
2. Enable outputs only in the reviewed commissioning build.
3. Verify every motor direction at minimum power.
4. Verify encoder sign and scale.
5. Tune low-speed wheel PID.
6. Measure heartbeat/TTL stop behavior.
7. Activate the physical E-stop during minimum approved wheel motion; verify
   independent power/enable interruption, latch behavior, and measured time.
8. Simulate stall/fault inputs first; use a separately reviewed fused,
   current-limited fixture if a physical load/stall test is authorized.

Never jam a powered wheel or motor as an informal test.

Pass evidence: measurements stay within written limits and every simulated or
approved fixture fault stops all wheels.

### Step P17 — Tethered isolated-floor basic-motion tests

Requires Step P16.

- [ ] Test low-speed straight and turn behavior.
- [ ] Test strafe only if the verified drivetrain is mecanum/omnidirectional.
- [ ] Measure acceleration, stopping time/distance, odometry error, and
  operator E-stop response.
- [ ] Add LiDAR obstacle stop before navigation goals.

Pass evidence: every value is within approved limits and an obstacle or sensor
loss produces a safe stop.

### Step P18 — Validate moving odometry and the ROS frame tree

Requires Steps P16–P17.

- [ ] Validate encoder signs, scale, wheel velocity, and fused IMU/encoder
  odometry during controlled motion.
- [ ] Verify dynamic `odom -> base_link` plus LiDAR/camera transforms at
  documented rates; do not invent `map -> odom` before localization exists.
- [ ] Measure drift and repeatability.

Pass evidence: no frame conflicts, stable `/odom` and `/scan`, and documented
error within the approved test limits.

### Step P19 — Map, localize, and navigate one local goal

Requires Steps P17–P18.

- [ ] Create, save, checksum, and version the map.
- [ ] Measure real poses for every supported cloud location ID.
- [ ] Configure footprint, inflation, geofence, speed, and acceleration from
  real measurements.
- [ ] Tune localization/navigation conservatively.
- [ ] Verify localization is the sole dynamic `map -> odom` publisher and the
  complete `map -> odom -> base_link` plus sensor-frame chain is valid.
- [ ] Test one obstacle-safe local goal without cloud integration.

Pass evidence: localization is repeatable; obstacle, LiDAR, and localization
faults stop movement; the goal completes only with correct target, position
tolerance, near-zero velocity, and a stability interval.

After this gate, complete Laptop Step L20 and install its released map/location
configuration before Step P20.

### Step P20 — Connect mission manager to the released navigation map

Requires Pi Steps P10 and P19 plus Laptop Step L20.

- [ ] Map goal, result, cancel, pause, resume, and return-home behavior to the
  navigation action.
- [ ] Verify the mission manager rejects a map checksum/version mismatch or an
  unknown cloud location ID.
- [ ] Run a motor-disconnected simulator first.
- [ ] Run one no-cargo local route under the approved controls.

Pass evidence: exactly one linked mission event sequence is produced and
cancel, blockage, localization loss, and reboot recover safely.

### Step P21 — Complete the integrated recovery and fault campaign

Requires Step P20 so the real mission/navigation integration is under test.

Run Wi-Fi loss, broker loss, Pi process loss, serial loss, Pi reboot, ESP32
reboot, low battery, blocked-wheel signal, LiDAR loss, localization loss, and
motor-feedback failure first in the motor-disconnected simulator. Repeat only
approved relevant cases with wheels raised; any floor repetition remains
tethered and supervised.

Pass evidence: no uncontrolled motion, duplicate mission side effect, or
duplicate linked event occurs; recovery always begins disarmed.

### Step P22 — Run one cloud mission without cargo

Depends on: Laptop Step L9 and all earlier required Pi gates.

- [ ] Confirm fresh physical telemetry truthfully reports `ONLINE`/`IDLE` and
  every required real sensor/controller healthy before dispatch.
- [ ] Run one supervised low-speed route from a real cloud delivery.
- [ ] Confirm the web delivery becomes `DISPATCHED` only after the broker
  confirms publication to the matching physical subscriber.
- [ ] Require robot events—not frontend buttons—to advance every state.
- [ ] Use the already approved network-failure procedure; do not introduce an
  untested disconnect during moving operation.

Pass evidence: exact event order, no duplicated actions, safe connectivity
loss, truthful readiness, and a complete redacted cloud/Pi evidence chain.

### Step P23 — Install and bench-verify the cargo controller

Depends on: Laptop Steps L14 and L19 plus Pi Step P22. Install only the released
cargo artifact from the L19 manifest and keep propulsion power disconnected.

- [ ] Install the reviewed lock/load/release hardware, fused power, protected
  local interface, and safe default state.
- [ ] Verify boot, reboot, disconnect, jam/fault, code expiry, replay,
  confirmation timeout, and manual staff recovery on the bench.
- [ ] Verify no cargo actuator action can arm propulsion or bypass the E-stop.
- [ ] Verify audit/event output matches the physical bench result exactly.

Pass evidence: every valid one-time confirmation has one physical effect and
one audit/event result; invalid, repeated, expired, disconnected, or faulted
cases remain safe with no partial delivery transition.

### Step P24 — Verify pickup, release, and supervised cargo mission

Depends on Pi Steps P22–P23.

- [ ] Use real physical load and release confirmations.
- [ ] Prove no departure/release event occurs without confirmation.
- [ ] Run one low-speed supervised cargo mission in an isolated area.

Pass evidence: cargo actions, audit records, events, and physical observations
agree.

### Step P25 — Complete final supervised-pilot acceptance

Depends on: completed Pi Step P24 and Laptop Step L21. Neither file alone is
sufficient to declare the supervised pilot ready.

Use the archived guide only as an index. Complete and sign the cloud/security,
Pi, MQTT return-path, navigation, and motor-safety acceptance governed by the
current `pi-agent-1.3.0` contracts and every required gate in these two
remaining-task files. Passing the older guide checklist alone is insufficient.

A supervised campus pilot is ready only after documented pass evidence exists
for physical E-stop, watchdogs, stopping distance, obstacle detection,
geofence/route boundaries, fault recovery, credential security, and the full
event-driven delivery workflow.

## Optional non-gating host hardening

### Optional Step PA1 — Finish SSH and firewall hardening

- [ ] Confirm a second tested administrator key works.
- [ ] Validate SSH configuration before disabling password authentication.
- [ ] Disable root SSH login.
- [ ] If UFW is used, do not open broker ports inbound. Preserve the required
  outbound DNS, NTP, HTTPS, and MQTT TLS traffic according to the chosen
  outbound policy.
- [ ] Confirm recovery access before closing the current session.

Pass evidence: key login works after SSH restart, configuration validation
passes, and firewall policy does not interrupt the outbound broker session.
