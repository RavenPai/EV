# Completed Raspberry Pi Work

MIIT Campus Delivery EV
Verified and updated: 21 July 2026

This file records only completed Raspberry Pi work. Evidence is identified as
either independently verified from the EV folder/Pi or reported by the
operator. Credentials, broker addresses, SSH addresses, private keys, and
exact USB identifiers are intentionally excluded.

## Completion summary

| Completed task | Verification evidence |
|---|---|
| Raspberry Pi platform verified | The rover host was verified as a 64-bit ARM Ubuntu system with the expected stable robot hostname. |
| Runtime identity verified | The bridge runs as the dedicated `rover` service account with its required runtime group access. |
| USB serial identity and permissions verified | A stable USB-serial device link is present, the device node is owned by `root:dialout` with mode `0660`, and `rover` has `dialout` membership. |
| MQTT bridge upgraded | The tested EV-folder bundle was installed as `pi-agent-1.3.0`. |
| Bridge modules installed | `agent.py`, `local_store.py`, and `message_contract.py` were installed together as one matching bundle. |
| Python environment verified | The installed virtual environment imports the required MQTT and serial libraries. |
| Durable storage installed | Command inbox/archive, ACK outbox/archive, and event outbox/archive directories exist with `rover` ownership and mode `0750`. |
| External environment preserved | Existing robot, MQTT, TLS, serial, and runtime settings were preserved outside Git. |
| Agent configuration updated | `ROBOT_AGENT_VERSION`, ACK outbox, and ACK archive settings were installed. |
| systemd unit installed | The maintained unit passed `systemd-analyze verify` and was installed under `/etc/systemd/system`. |
| Service enabled and started | The upgraded bridge is enabled at boot and active under systemd. |
| Service stability verified | The post-deployment observation reported `NRestarts=0`. |
| Clock safety enabled | The system clock is synchronized and `chrony-wait.service` is enabled. |
| MQTT/TLS verified | The upgraded bridge established its encrypted MQTT connection. |
| Command subscription verified | EMQX accepted the robot's QoS-1 command-topic subscription. |
| Durable event publishing verified | A generated safety event moved from `event-outbox` to `event-archive` after broker acceptance. |
| Configuration hardened | Source is root-controlled, the environment file is root-only, and the service account retains broker-CA read access. |
| Rollback protection created | A root-only backup of the previous bridge, unit, and external environment was created before installation. |
| Temporary files cleaned | The private staging directory was removed after successful verification. |
| Documentation protected | No private connection values were added to the repository documentation. |

## Completed setup-guide milestones through Step 13.2

The referenced setup guide is an archived guide for the earlier bridge. These
completed titles use its step numbering while recording the current deployed
paths and `pi-agent-1.3.0` behavior where appropriate.

Done **## 4. Safety conditions before entering commands**

Evidence: Operator-reported. Motor power was kept disconnected for the
controlled cloud-to-Pi test.

Done **### Step 5.1 — Log in and record system information**

Evidence: Independently verified. The Pi is a 64-bit ARM Ubuntu system with
working Python, storage, network, and synchronized time.

Done **### Step 5.2 — Set the robot hostname**

Evidence: Independently verified. The Pi uses the expected stable robot
hostname.

Done **### Step 5.3 — Confirm time synchronization**

Evidence: Independently verified. The clock is synchronized and the Chrony
boot wait is enabled.

Done **### Step 5.4 — Update Ubuntu**

Evidence: Operator-reported completion.

Done **### Step 5.5 — Install base packages and Python**

Evidence: Operator-reported plus runtime verification. The installed Python
environment, Git, TLS tooling, and bridge dependencies are operational.

Done **### Step 5.6 — Check network and DNS**

Evidence: Independently verified through the working DNS/network route and
live MQTT/TLS session.

Done **### Step 5.7 — Optional SSH hardening: key-access portion**

Evidence: Independently verified. Administrative SSH key authentication works.

Done **### Step 6.1 — Create a non-login service account**

Evidence: Independently verified. The dedicated `rover` account and required
runtime group membership are installed.

Done **### Step 6.2 — Create the production layout**

Evidence: Independently verified. Production source, configuration, and
persistent-state directories have the intended ownership and permissions.

Done **### Step 7.1 — Clone the repository**

Evidence: Independently verified by outcome. The project source is installed
in the production source tree.

Done **### Step 7.2 — Record and inspect the deployed version**

Evidence: Independently verified. The deployed runtime hashes match the tested
EV-folder bundle and the bridge reports `pi-agent-1.3.0`.

Done **### Step 8.1 — Build the environment**

Evidence: Independently verified. The virtual environment imports the pinned
MQTT and serial dependencies and passes the Pi test suite.

Done **### Step 9.1 — Keep motor power disconnected**

Evidence: Operator-reported for the direct serial and controlled dispatch
tests. A stable USB-serial link is currently present.

Done **### Step 9.2 — Verify permissions**

Evidence: Independently verified. The serial node uses `dialout` permissions
and `rover` belongs to that group.

Done **### Step 9.3 — Confirm protocol compatibility**

Evidence: Independently verified in source and host tests. The Pi bridge and
ESP32 v0.2 source share the framed protocol and the protocol tests pass.

Done **### Step 9.4 — Direct STOP test with motor power disconnected**

Evidence: Operator-reported completion with motor power disconnected.

Done **### Step 10.1 — Create MQTT device credentials**

Evidence: Independently verified by the successful authenticated MQTT client
session.

Done **### Step 10.3 — Install the EMQX CA certificate**

Evidence: Independently verified. The protected CA is installed and readable
by the runtime account.

Done **### Step 10.4 — Test DNS, TCP, SNI, and TLS**

Evidence: Independently verified by the bridge's successful verified MQTT/TLS
connection.

Done **## 11. Phase G — Create the protected environment file**

Evidence: Independently verified. The external environment is stored outside
Git, root-owned with mode `0600`, and supplies the running bridge configuration.

Done **### Step 12.1 — Create the service unit**

Evidence: Independently verified. The hardened maintained unit is installed.

Done **### Step 12.2 — Validate and start**

Evidence: Independently verified. Systemd validation passes; the service is
enabled and active, MQTT is connected, the QoS-1 subscription is accepted, and
the post-deployment observation reported zero restarts.

Done **### Step 12.3 — Understand an old-agent limitation**

Evidence: Independently verified by replacement. The robust `pi-agent-1.3.0`
remains available for diagnostics when serial opening fails.

Done **### Step 13.2 — Dispatch one controlled test**

Evidence: Operator-reported and independently verified on the Pi. The operator
reported completing the web procedure. The protected legacy request is valid
`START_MISSION` JSON with a valid command ID, delivery ID, source, destination,
map version, and request timestamp. Its command ID matches a durable row in
`commands.db`.

### Completed mission-related result

The Step 13.2 evidence verifies one controlled mission-command dispatch from the
web workflow to the earlier Pi bridge, followed by validation, durable local
handoff, and durable processed-command recording. Step 13.2 intentionally uses
motor power disconnected and ends at command handoff, so the completed result
is a **mission dispatch/handoff test**, not a physical or autonomous delivery.

## Installed bridge files

The deployed source directory is `/opt/miit-rover/source/robot-pi/` and contains:

```text
agent.py
local_store.py
message_contract.py
requirements.txt
miit-rover-agent.service
robot.env.example
test_agent.py
test_local_store.py
test_message_contract.py
```

The active unit is `/etc/systemd/system/miit-rover-agent.service`.

## Completed bridge behavior

The installed bridge implements:

- MQTT/TLS with the configured CA and robot identity.
- Persistent MQTT sessions and QoS 1 command subscription.
- Manual MQTT acknowledgement only after the command outcome is durable.
- Exact command schema, identity, payload, TTL, topic, QoS, retained flag,
  message-size, and finite-JSON validation.
- SQLite-backed command outcomes and safe duplicate replay.
- Rejection of command IDs reused with different content.
- Atomic per-command inbox handoff and command archives.
- Durable ACK and event outbox/archive processing.
- Deterministic command-linked event IDs and replay consistency checks.
- State freshness, bounds, and timestamp validation.
- Structured startup, MQTT, command, ACK, event, state, and serial logging.
- Resilient periodic presence/state publishing loops.
- Graceful shutdown and retained offline-presence handling.

## Verified permissions

| Path | Ownership | Mode |
|---|---:|---:|
| `/opt/miit-rover/source` | `root:root` | `0755` |
| `/opt/miit-rover/source/robot-pi` | `root:root` | `0755` |
| `/etc/miit-rover` | `root:rover` | `0750` |
| `/etc/miit-rover/robot.env` | `root:root` | `0600` |
| `/etc/miit-rover/emqx-ca.crt` | `root:rover` | `0640` |
| `/var/lib/miit-rover` | `rover:rover` | `0750` |
| `/var/lib/miit-rover/ack-outbox` | `rover:rover` | `0750` |
| `/var/lib/miit-rover/ack-archive` | `rover:rover` | `0750` |

The service also applies `NoNewPrivileges`, `PrivateTmp`, strict system
protection, protected home directories, a `0077` umask, and runtime write
access limited to `/var/lib/miit-rover`.

## Test evidence

All 27 Raspberry Pi tests passed in each completed validation stage:

1. In the local EV project before transfer.
2. From the staged bundle using the Pi's installed virtual environment.
3. From the installed root-owned source while running as `rover`.

The tests cover command contracts and TTLs, transport requirements, retained
message rejection, duplicate/conflicting replay, durable outcome ordering,
ACK persistence, atomic file recovery, event identity and schemas, state
freshness and bounds, and outbox-to-archive movement.

Python compilation passed for all runtime modules. SHA-256 checks confirmed
that the staged and installed runtime files matched the tested local files.

## Live service evidence

The verified deployment sequence was:

```text
agent_starting version=pi-agent-1.3.0
time_sync_ready
mqtt_connected
mqtt_command_subscription_ready
event_broker_accepted
event archived
```

Final checks reported:

```text
service state: active
service enabled: yes
observed restarts: 0
clock synchronized: yes
clock wait enabled: yes
```

## Deployment integrity

The completed deployment used local tests, private staging, Pi-side tests,
hash verification, a root-only backup, controlled service replacement,
installed-source tests as `rover`, systemd verification, guarded restart,
MQTT readiness verification, durable event-cycle verification, and staging
cleanup. Automatic rollback remained armed throughout installation and was
not needed.
