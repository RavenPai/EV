# MIIT Rover Raspberry Pi Ubuntu Setup Guide

Cloud-to-robot installation, verification, and autonomous-navigation roadmap  
Prepared for the MIIT Campus Delivery EV project  
Updated and live-audited: 21 July 2026

## 0. Live verification record

This file now contains both the installation runbook and a dated evidence
record. Credentials, private keys, broker passwords, and SSH addresses are
intentionally excluded.

The audit was read-only: it inspected the Pi and cloud database and connected
an independent MQTT client without publishing commands, state, or events.

| Check | Result | Evidence from 21 July 2026 |
|---|---|---|
| Pi identity and OS | PASS | Hostname `robot-01`; Ubuntu 26.04 LTS arm64 |
| Deployed source | PARTIAL / UPDATE REQUIRED | Clean `main` at commit `9dd8f8b`, but the Pi still runs the pre-hardening 492-line `pi-agent-1.1.0`; the new local files are not deployed yet |
| systemd bridge | PASS | Unit enabled and active; process runs as `rover` with no current restart loop |
| Clock synchronization at boot | PARTIAL | Clock is synchronized now, but Chrony's wait unit is disabled and the bridge started before the clock correction |
| MQTT/TLS session | PASS | Established outbound TLS socket on port 8883 |
| Pi presence publication | PASS | Broker returned retained online presence and the next 15-second heartbeat from `pi-agent-1.1.0` |
| Stable serial device | PASS | Persistent CP2102 `/dev/serial/by-id/...` link exists and `rover` belongs to `dialout` |
| CA access | PASS | The configured CA exists and is readable by the service account |
| Config ownership | **FAIL HARDENING** | `/etc/miit-rover`, `robot.env`, and the CA are owned by `rover`; production config must be controlled by root with only required CA read access delegated |
| EMQX-to-Supabase return path | **FAIL** | Immediately after a fresh Pi heartbeat, Supabase still showed the old 18 July `last_seen` and `telemetry_at = null` |
| Web command publication | **FAIL** | Recent `START_MISSION`, `PAUSE`, and `RETURN_HOME` audit rows failed before MQTT publication with EMQX Deployment API HTTP 403 |
| Broker authorization | **FAIL** | The robot identity received successful subscriptions beyond its required own-topic scope; per-robot allow rules plus default deny are not enforced |
| Mission-manager state | **NOT PRESENT** | No `robot_state.json` existed, so telemetry and operational readiness are not being published |
| Mission event evidence | **NOT PRESENT** | No mission-event outbox/archive output or event-driven delivery transition was available |
| ESP32 physical STOP/watchdog | **NOT VERIFIED** | USB-UART presence is not proof of motor stop, heartbeat timeout, frame acknowledgement, or hardware E-stop behavior |
| Autonomous navigation | **NOT VERIFIED** | No mission manager, localization, route execution, arrival proof, or supervised route result was captured |

## 0.1 Deeper diagnostic pass — same day, later session

A second, deeper pass was run against the same live Pi (SSH), the linked
Supabase project (`supabase` CLI, already authenticated and linked to
`nkvmpjznvkqmgcposaup`), and the live EMQX broker (an independent, read-only
MQTT client using the robot device credentials; it only subscribed, it never
published). No destructive or write action was taken against the Pi, the
database, or the broker during this pass.

| Check | Result | Evidence |
|---|---|---|
| Pi state vs. this file | UNCHANGED | Still `pi-agent-1.1.0` at 492 lines; `message_contract.py` and `local_store.py` still absent; `chrony-wait.service` still disabled; `/etc/miit-rover`, `robot.env`, and the CA are still `rover:rover` |
| Why the hardening files never reached the Pi | ROOT CAUSE FOUND | The Pi's clone is at `git log -1` = `9dd8f8b`, identical to `origin/main`. The hardening files exist only in an uncommitted local working tree and were never pushed. `git pull` on the Pi has nothing new to fetch. |
| Deployed Supabase Edge Functions | STALE | `dispatch-delivery` (v7) and `ingest-robot-message` (v2) were both last deployed 18 July 2026, before migrations `202607200008/9` and `202607210010` were authored |
| Migration `202607210010` on the linked database | NOT APPLIED | `supabase migration list --linked` shows every earlier migration through `202607200009` applied; `202607210010` shows an empty remote column |
| Root cause of the unapplied migration | BUG FOUND AND FIXED IN SOURCE | The migration's own `comment on function public.apply_robot_presence(text, boolean, text)` referenced a 3-argument signature, but the function it just created two hundred lines earlier in the same file takes 4 arguments (`..., timestamptz`). `COMMENT ON FUNCTION` requires an exact match, so this statement — and therefore the whole migration transaction — would abort on `supabase db push`. This alone explains why `bridge_last_seen`/`telemetry_at` have never advanced. |
| Related bug in the ingestion function | BUG FOUND AND FIXED IN SOURCE | `ingest-robot-message/index.ts` validated the presence message's `at` field and then discarded it; its call to the `apply_robot_presence` RPC omitted the now-required `p_observed_at` argument entirely. Even with the migration syntax fixed, every presence message would have failed at the RPC call with a schema-cache/"function not found" error. |
| Matching pgTAP test drift | FIXED IN SOURCE | `supabase/tests/database/006_robot_presence_order.sql` called `apply_robot_presence` with the old 3-argument form and asserted the old 3-argument privilege signature. Updated to the 4-argument form with an explicitly ordered timestamp per call so the ordering guard is actually exercised. |
| **New finding: ESTOP/FAULT safety latch has no reset path** | **BLOCKING — NOT YET DEPLOYED** | The same migration adds a `BEFORE UPDATE` trigger (`enforce_robot_safety_latch`) that re-asserts `mode='ESTOP'`/`status='FAULT'` on the `robots` row unless the session has set `app.robot_safety_reset` to that robot's ID. Nothing in this repository — not the Fleet page, not `dispatch-delivery`, not `ingest-robot-message`, not the existing `RESUMED` handling inside `apply_robot_event` (`202607170004_robot_ingestion.sql`, lines 327–338) — ever sets that value. As written, once any robot reaches `ESTOP` or `FAULT` (including from a normal `MISSION_FAILED` event), **no software path in this codebase can ever clear it again**; only a manual superuser SQL statement could. Deploying this migration as-is would make the very E-stop test required later in this file (§13.5) leave `robot-01` permanently latched in the database. This migration is **intentionally held back from `supabase db push` and from Pi deployment** until a reset mechanism and its authorization rule (who may clear an E-stop, and how) are decided. |
| EMQX ACL scope for `robot-01` (re-verified live) | STILL FAIL, more precisely | An independent read-only subscriber authenticated as `robot-01` against `b21f8b00.ala.asia-southeast1.emqxsl.com:8883` received **granted** subscriptions to `miit/robots/robot-01/commands` (expected — this one is correct), `miit/robots/other-robot-99/state` (must be denied), and the cross-robot wildcard `miit/robots/+/commands` (must be denied). Only the fully open `#` wildcard was rejected (SUBACK failure code 128). The broker currently has no rule scoping `robot-01` to only its own topic segment — it is closer to "deny only the literal `#`" than to the documented least-privilege allowlist. |
| ESP32 serial identity | CONFIRMED | `/dev/serial/by-id/usb-Silicon_Labs_CP2102_USB_to_UART_Bridge_Controller_0001-if00-port0` exists on the Pi, symlinks to `/dev/ttyUSB0`, and `rover` remains in the `dialout` group |
| Supabase secrets (`EMQX_API_URL`/`EMQX_API_KEY`/`EMQX_API_SECRET`/`ROBOT_INGEST_SECRET`) | NOT CHECKED | Listing secret names was withheld by this session's own safety controls; their presence/correctness must still be confirmed directly in the Supabase dashboard |
| Pi root/sudo actions (ownership, `chrony-wait`, service restart) | NOT PERFORMED | `evdelivery` has `sudo` group membership but sudo requires an interactive password this session cannot supply non-interactively; commands are listed below for manual execution |

None of the source fixes above have been pushed to GitHub, pulled onto the Pi,
pushed to the Supabase database, or redeployed as an Edge Function yet. They
are committed to disk in the working tree only, pending the E-stop-latch
decision and your go-ahead.

### Required next actions

Do these before calling the cloud-to-robot workflow complete:

1. Replace the failing EMQX Deployment API credentials/URL in Supabase until a
   dispatch command becomes `PUBLISHED` and the delivery becomes `DISPATCHED`.
   Confirm `EMQX_API_URL`, `EMQX_API_KEY`, `EMQX_API_SECRET`, and
   `ROBOT_INGEST_SECRET` are actually set as Supabase function secrets; this
   session could not list secret names to check.
2. Decide and implement a real reset path for the `ESTOP`/`FAULT` safety latch
   **before** pushing migration `202607210010`. As written, nothing in this
   repository ever sets `app.robot_safety_reset`, so once a robot reaches
   `ESTOP` or `FAULT` no software path can clear it again — not the Fleet page,
   not a `RESUMED` event, nothing. Pick an explicit, authorized mechanism (for
   example, an `ADMIN`/`OPERATOR`-only RPC or Edge Function action that sets
   the GUC and clears `mode`/`status` together, exposed through a distinct
   "Clear fault" control that is visually separate from `RETURN_HOME`/`RESUME`)
   before this migration is applied anywhere.
3. Push migration `202607210010` (the `comment on function` signature bug and
   the missing `p_observed_at` argument in `ingest-robot-message` are already
   fixed in this working tree) and redeploy `ingest-robot-message`, only after
   step 2 is resolved:

   ```bash
   supabase db push
   supabase functions deploy ingest-robot-message
   ```

   A live Pi presence heartbeat must then update `robots.bridge_last_seen`
   within 15 seconds. A fresh state snapshot must separately advance
   `robots.last_seen` and `robots.telemetry_at`.
4. Restrict `robot-01` to subscribe only to its own `commands` topic and publish
   only to its own `acks`, `state`, `events`, and `presence`; default deny every
   unmatched operation. Re-verified live today: `robot-01` can still subscribe
   to another robot's `state` topic and to the cross-robot wildcard
   `miit/robots/+/commands`; only the fully open `#` is currently denied.
5. Commit and push the new EV-folder Pi hardening files, then pull them on the
   Pi; the live checkout cannot deploy uncommitted local files, and confirmed
   today that the Pi's clone is still exactly at `origin/main` with none of
   `local_store.py`, `message_contract.py`, or the rewritten `agent.py` present.

   ```bash
   git add robot-pi/ supabase/ scripts/ .github/ .gitignore README.md project.md package.json vitest.config.ts
   git commit -m "Harden Pi agent, add mission event/state handling, fix presence RPC"
   git push origin main
   ```

   Then on the Pi:

   ```bash
   cd /opt/miit-rover/source
   git config --global --add safe.directory /opt/miit-rover/source
   sudo -u rover git pull
   cd robot-pi
   sudo -u rover .venv/bin/python -m unittest discover -s . -p 'test_*.py' -v
   ```

   Require a nonzero test count and all tests passing before restart.
6. Restore root control of `/etc/miit-rover`, protect `robot.env` as
   `root:root` mode `0600`, give `rover` read access only to the CA, and enable
   `chrony-wait.service` before restarting the bridge. `evdelivery` has `sudo`
   group membership but this session cannot supply an interactive sudo
   password, so run these manually on the Pi:

   ```bash
   sudo chown -R root:root /etc/miit-rover
   sudo chmod 750 /etc/miit-rover
   sudo chmod 600 /etc/miit-rover/robot.env
   sudo chgrp rover /etc/miit-rover/emqx-ca.crt
   sudo chmod 640 /etc/miit-rover/emqx-ca.crt
   sudo -u rover test -r /etc/miit-rover/emqx-ca.crt && echo "rover can still read the CA"
   sudo systemctl enable chrony-wait.service
   sudo systemctl start chrony-wait.service
   systemctl is-active chrony-wait.service
   sudo systemctl restart miit-rover-agent
   sudo systemctl status miit-rover-agent --no-pager
   ```
7. Implement or deploy the separate mission manager so it writes fresh state
   with an `at` observation timestamp and durable linked mission events.
8. Perform the motor-disconnected, wheels-raised, watchdog, physical E-stop,
   and network-loss tests and add the measured results to this table. Do not
   trigger a real ESTOP or `MISSION_FAILED` on `robot-01` until action 2 above
   has a working reset path, or the test itself will strand the robot in
   `FAULT` in the database.

## 1. What this guide will achieve

This guide explains how to prepare the Raspberry Pi from a newly installed Ubuntu system and progress through four clearly separated completion levels:

1. **Pi base ready** — Ubuntu, Python, permissions, networking, and secure remote administration work.
2. **Cloud bridge ready** — the Pi connects to EMQX through MQTT/TLS, receives commands, validates them, acknowledges them, and starts automatically after boot.
3. **Pi-to-ESP32 ready** — the Pi can issue a safe STOP request, the ESP32 has a local watchdog, and communication loss produces a stop.
4. **Autonomous delivery ready** — a mission manager, LiDAR, odometry, localization, Nav2 or a constrained route controller, state/event reporting, and hardware safety mechanisms all pass supervised tests.

These levels must not be confused. Completing the MQTT agent does **not** make the robot autonomously navigable. It only completes the secure cloud-to-Pi transport layer.

The intended production architecture is:

```text
Public web application
        |
        | HTTPS + Supabase user session
        v
Supabase database + Edge Functions
        |
        | EMQX REST publish API
        v
EMQX Cloud MQTT broker
        |
        | MQTT/TLS, QoS 1, port 8883
        v
Raspberry Pi MQTT agent
        |
        +---- mission request ----> Local mission manager
        |                              |
        |                              +--> ROS 2/Nav2 or constrained navigation
        |                              +--> LiDAR/camera/odometry/localization
        |
        +---- local safety request --> ESP32 gateway
                                       |
                                       v
                               ESP32 + motor controller
```

The public frontend, Supabase backend, database, and EMQX broker do not run on the Pi. The Pi runs only robot-side software.

## 2. Analysis of the supplied `project.md`

### 2.1 Strong parts of the design

The project document makes several correct architectural decisions:

- The website sends high-level mission commands, not continuous Internet steering.
- Supabase authenticates users and limits dispatch to `ADMIN` and `OPERATOR` roles.
- Commands are audited before they are published.
- MQTT commands have UUIDs and expiration timestamps.
- The Pi checks schema version, robot identity, expiration, and duplicates again.
- MQTT QoS 1 duplicate delivery is handled through persistent command IDs.
- The ESP32 remains responsible for motor output and independent stopping.
- Robot acknowledgements, state, events, and presence are intended to return to Supabase.
- Autonomous navigation is explicitly kept on the robot rather than in the cloud.

This is a good trust boundary for a campus delivery prototype.

### 2.2 Important incompleteness described by the document itself

The document says the following physical components are not yet complete:

- Mission manager
- ROS 2/Nav2 or another real navigation controller
- LiDAR-to-navigation integration
- Odometry and localization
- ESP32 motor-control firmware
- Pi heartbeat watchdog on the ESP32
- Physical emergency-stop behavior
- Cargo lock and safe release flow

Therefore, the current repository is an integration foundation, not a finished autonomous vehicle.

### 2.3 Deployed source version

The live audit found the current 492-line bridge on the Pi. It includes TLS,
command validation, persistent command IDs, acknowledgements, retained
presence, state publication, event outbox handling, and lazy serial opening.
The earlier 121-line source assessment was stale and must not be used for the
current repository.

Before every update, record the exact code copied to the Pi:

```bash
cd /path/to/your/repository
git log -1 --oneline
find supabase/functions -maxdepth 2 -type f -print
find supabase/migrations -maxdepth 1 -type f -print | sort
wc -l robot-pi/agent.py
grep -nE 'MQTT_CA_FILE|PRESENCE_INTERVAL|STATE_INTERVAL|event-archive|robot_state' robot-pi/agent.py
```

The local working-tree post-audit version also requires `local_store.py` and
`message_contract.py`, archives
broker-accepted events for replay, validates state freshness, and reports
structured service logs. Copy the whole `robot-pi` source set, not `agent.py`
alone.

### 2.4 Navigation remains a separate, unverified component

No `navigation.py` or mission-manager implementation is maintained in this EV
repository, and none was verified during the live audit. Do not infer movement
capability from the active MQTT bridge. Any external or older navigation script
must pass the state machine, localization, obstacle, heartbeat, command TTL,
ESP32 acknowledgement, and supervised hardware tests in this guide before it
is enabled on a powered robot.

### 2.5 One serial-port owner is required

The MQTT agent currently opens the ESP32 serial port to issue STOP. A future navigation process will also need to communicate with the ESP32. Two processes must not open and write to the same serial device independently.

Use one of these designs:

**Recommended ROS design**

```text
MQTT agent --local ESTOP/PAUSE request-->
                                          base-controller ROS node --> one serial connection --> ESP32
Mission manager/Nav2 --/cmd_vel---------->
```

**Smaller non-ROS design**

```text
MQTT agent ------ Unix socket ------>
                                      motor_gateway.py --> one serial connection --> ESP32
Mission manager - Unix socket ------>
```

Only `base-controller` or `motor_gateway.py` owns `/dev/esp32`. Resolve this before adding movement commands.

## 3. Assumptions and recommended versions

This guide assumes:

- Raspberry Pi 5 or Pi 4 with a supported 64-bit Ubuntu installation
- Ubuntu 24.04 LTS 64-bit is preferred for a new installation
- Python is installed from Ubuntu packages
- ESP32 is connected by a data-capable USB cable
- EMQX Serverless is used with MQTT/TLS on port 8883
- Robot identity is `robot-01`
- EMQX MQTT username is also `robot-01`
- The repository contains `robot-pi/agent.py` and `robot-pi/requirements.txt`

For navigation:

- Ubuntu 26.04 arm64: use [ROS 2 Lyrical Luth](https://docs.ros.org/en/lyrical/Installation/Alternatives/Ubuntu-Install-Binary.html).
- Ubuntu 24.04 arm64: use ROS 2 Jazzy.
- Ubuntu 22.04 arm64: use ROS 2 Humble instead.
- Do not mix ROS distributions or install packages for a different Ubuntu release.

The audited Pi runs Ubuntu 26.04 arm64, so its matching release is ROS 2
[Lyrical Luth](https://docs.ros.org/en/kilted/Releases/Release-Lyrical-Luth.html),
an LTS release supported through May 2031. Python virtual environments isolate
the bridge dependencies from Ubuntu and ROS packages. EMQX Serverless requires
TLS and documents port 8883 for MQTT clients.

## 4. Safety conditions before entering commands

For all initial tests:

1. Disconnect motor power physically.
2. Raise the chassis so no wheel can touch the floor when motor testing begins.
3. Keep the Pi, ESP32 logic, and motor power paths correctly regulated.
4. Never power motors from a Raspberry Pi GPIO or USB port.
5. Install a physical emergency-stop circuit that removes motor-driver enable or motor power independently of the Pi and Internet.
6. Keep a human operator within reach of the physical E-stop.
7. Make ESP32 output default to STOP after boot or reset.
8. Do not treat the web `ESTOP` button as the physical emergency stop.

## 5. Phase A — Prepare Ubuntu

### Step 5.1 — Log in and record system information

Open a terminal directly on the Pi, or connect through SSH if it is already enabled. Run:

```bash
uname -m
cat /etc/os-release
hostnamectl
python3 --version
free -h
df -h /
ip -br address
timedatectl
```

Expected essentials:

```text
Architecture: aarch64
Ubuntu: 24.04 LTS or another supported 64-bit release
Time synchronized: yes
NTP service: active
```

If `uname -m` shows `armv7l`, the OS is 32-bit. Reinstall a 64-bit image before using ROS 2 and modern Pi software.

Save the output in your project test record. It is valuable when diagnosing driver or package problems.

### Step 5.2 — Set the robot hostname

Use the stable robot identity as the host name:

```bash
sudo hostnamectl set-hostname robot-01
hostnamectl
```

Reboot later. Do not use the same hostname for multiple robots.

### Step 5.3 — Confirm time synchronization

Cloud commands expire, and telemetry timestamps must be ordered correctly. Run:

```bash
timedatectl status
timedatectl show -p NTPSynchronized
systemctl status chrony --no-pager || systemctl status systemd-timesyncd --no-pager
```

If synchronization is disabled:

```bash
sudo timedatectl set-ntp true
```

The audited Ubuntu image uses Chrony. Its bridge started before the clock was
corrected because `chrony-wait.service` was disabled. On an image using Chrony,
enable the wait unit so `time-sync.target` is a real startup barrier:

```bash
sudo systemctl enable chrony-wait.service
sudo systemctl start chrony-wait.service
systemctl is-active chrony-wait.service
```

On an image using `systemd-timesyncd`, enable the distribution's
`systemd-time-wait-sync.service` instead when it is available.

You may keep the displayed timezone as `Asia/Yangon`; software must still generate MQTT timestamps in UTC.

Do not continue to command testing until `System clock synchronized` is `yes`.

### Step 5.4 — Update Ubuntu

```bash
sudo apt update
sudo apt full-upgrade -y
sudo reboot
```

Reconnect after the reboot and run:

```bash
uname -a
sudo apt update
```

### Step 5.5 — Install base packages and Python

Ubuntu often already contains Python, but install the complete project tooling explicitly:

```bash
sudo apt install -y \
  python3 \
  python3-venv \
  python3-pip \
  python3-dev \
  build-essential \
  git \
  curl \
  ca-certificates \
  jq \
  sqlite3 \
  openssl \
  netcat-openbsd \
  usbutils \
  udev \
  v4l-utils \
  minicom \
  mosquitto-clients
```

Verify:

```bash
python3 --version
python3 -m pip --version
git --version
openssl version
```

Do not use `sudo pip install ...` for this project. The agent will use its own `.venv`.

### Step 5.6 — Check network and DNS

```bash
ip route
resolvectl status
ping -c 3 1.1.1.1
ping -c 3 cloudflare.com
```

The Pi needs outbound Internet access. No incoming Internet port is needed for MQTT because the Pi initiates the connection to EMQX.

If Wi-Fi is used, create a stable campus Wi-Fi plan. A normal captive portal that requires a browser login is unsuitable for an unattended robot.

### Step 5.7 — Optional but recommended SSH hardening

Install and enable SSH if needed:

```bash
sudo apt install -y openssh-server
sudo systemctl enable --now ssh
sudo systemctl status ssh --no-pager
```

Find the Pi address:

```bash
hostname -I
```

From the development computer, create an Ed25519 key and copy it to the Pi. On Linux/macOS:

```bash
ssh-keygen -t ed25519
ssh-copy-id YOUR_ADMIN_USER@PI_IP_ADDRESS
```

On Windows PowerShell, `ssh-keygen -t ed25519` is also available on modern Windows. Copy the public key only after confirming the target IP and account.

Open a second terminal and confirm key login works before disabling passwords:

```bash
ssh YOUR_ADMIN_USER@PI_IP_ADDRESS
```

Then create an SSH configuration drop-in using `sudoedit`:

```bash
sudoedit /etc/ssh/sshd_config.d/99-miit-rover.conf
```

Use:

```text
PermitRootLogin no
PasswordAuthentication no
PubkeyAuthentication yes
```

Validate before restarting:

```bash
sudo sshd -t
sudo systemctl restart ssh
```

If key login has not been tested, do not disable password login yet.

### Step 5.8 — Optional firewall

If SSH is the only required incoming service:

```bash
sudo apt install -y ufw
sudo ufw default deny incoming
sudo ufw default allow outgoing
sudo ufw allow OpenSSH
sudo ufw enable
sudo ufw status verbose
```

Do not open MQTT port 8883 on the Pi firewall. Port 8883 is an outbound connection to EMQX.

## 6. Phase B — Create the rover service account and directories

### Step 6.1 — Create a non-login service account

```bash
sudo useradd \
  --system \
  --user-group \
  --home-dir /var/lib/miit-rover \
  --shell /usr/sbin/nologin \
  rover
```

If the command reports that `rover` already exists, inspect it rather than creating another account:

```bash
getent passwd rover
id rover
```

Give the service access to USB serial devices and USB cameras:

```bash
sudo usermod -aG dialout,video rover
id rover
```

Group changes take effect for newly started processes. Restart the systemd service after changing groups.

### Step 6.2 — Create the production layout

```bash
sudo install -d -o rover -g rover -m 0750 /opt/miit-rover
sudo install -d -o root  -g rover -m 0750 /etc/miit-rover
sudo install -d -o rover -g rover -m 0750 /var/lib/miit-rover
sudo install -d -o rover -g rover -m 0750 /var/lib/miit-rover/command-inbox
sudo install -d -o rover -g rover -m 0750 /var/lib/miit-rover/command-archive
sudo install -d -o rover -g rover -m 0750 /var/lib/miit-rover/event-outbox
sudo install -d -o rover -g rover -m 0750 /var/lib/miit-rover/event-archive
```

Intended layout:

```text
/opt/miit-rover/                 application source
/opt/miit-rover/robot-pi/.venv/ Python environment
/etc/miit-rover/robot.env       root-owned MQTT secrets
/etc/miit-rover/emqx-ca.crt     EMQX CA certificate when required
/var/lib/miit-rover/            persistent robot state
/var/lib/miit-rover/commands.db duplicate-command database
/var/lib/miit-rover/command-inbox/
/var/lib/miit-rover/command-archive/
/var/lib/miit-rover/event-outbox/
/var/lib/miit-rover/event-archive/
```

## 7. Phase C — Install the correct project source

### Step 7.1 — Clone the repository

Use the URL of the repository containing the completed `project.md` version:

```bash
sudo -u rover git clone YOUR_GITHUB_REPOSITORY_URL /opt/miit-rover/source
```

If the repository is private, use a read-only GitHub deploy key. Do not place a personal GitHub password or token in a shell command or service file.

Locate the Pi folder:

```bash
find /opt/miit-rover/source -maxdepth 3 -type f -name agent.py -print
find /opt/miit-rover/source -maxdepth 3 -type f -name requirements.txt -print
```

For the examples below, this guide assumes:

```text
/opt/miit-rover/source/robot-pi/agent.py
/opt/miit-rover/source/robot-pi/requirements.txt
```

If your repository contains an extra `miit-delivery-app` directory, adjust every path consistently.

### Step 7.2 — Record and inspect the deployed version

```bash
sudo -u rover git -C /opt/miit-rover/source log -1 --oneline
wc -l /opt/miit-rover/source/robot-pi/agent.py
grep -nE 'MQTT_CA_FILE|PRESENCE_INTERVAL|STATE_INTERVAL|event-archive|robot_state' \
  /opt/miit-rover/source/robot-pi/agent.py
test -f /opt/miit-rover/source/robot-pi/message_contract.py
test -f /opt/miit-rover/source/robot-pi/local_store.py
```

Decision gate:

- Require the current `agent.py`, `message_contract.py`, and `local_store.py` together before
  enabling the service.
- If either is absent, stop the deployment and update the whole repository.
- Do not claim that a delivery is physically complete merely because an MQTT acknowledgement was published.

## 8. Phase D — Create the Python virtual environment

### Step 8.1 — Build the environment

```bash
cd /opt/miit-rover/source/robot-pi
sudo -u rover python3 -m venv .venv
sudo -u rover .venv/bin/python -m pip install --upgrade pip setuptools wheel
sudo -u rover .venv/bin/python -m pip install -r requirements.txt
```

Verify packages:

```bash
sudo -u rover .venv/bin/python -m pip check
sudo -u rover .venv/bin/python -c "import paho.mqtt.client as mqtt; import serial; print('Python bridge dependencies OK')"
sudo -u rover .venv/bin/python -m pip show paho-mqtt pyserial
sudo -u rover .venv/bin/python -m unittest discover -s . -p 'test_*.py' -v
```

Expected project pins are currently:

```text
paho-mqtt==2.1.0
pyserial==3.5
```

Do not install OpenCV, TensorFlow, ROS, or LiDAR libraries into this small bridge environment unless the bridge actually imports them. Navigation dependencies should remain separate.

## 9. Phase E — Connect and identify the ESP32

### Step 9.1 — Keep motor power disconnected

Connect the ESP32 to the Pi using a data-capable USB cable. Keep the motor battery or motor-driver enable disconnected.

Run:

```bash
lsusb
sudo dmesg --follow
```

Unplug and reconnect the ESP32 while observing `dmesg`. Exit with `Ctrl+C`.

Then run:

```bash
ls -l /dev/ttyUSB* /dev/ttyACM* 2>/dev/null
ls -l /dev/serial/by-id/ 2>/dev/null
```

Prefer a persistent path such as:

```text
/dev/serial/by-id/usb-Silicon_Labs_CP2102N_...
```

over `/dev/ttyUSB0`, because USB numbering can change after reboot or when the LiDAR is attached.

### Step 9.2 — Verify permissions

Replace the path below with the detected device:

```bash
stat -c '%A %U %G %n' /dev/ttyUSB0
id rover
```

The device is normally owned by group `dialout`, and the `rover` account must be in that group.

If a serial port is unexpectedly busy:

```bash
sudo lsof /dev/ttyUSB0
systemctl status ModemManager --no-pager
```

Ubuntu Desktop's ModemManager can sometimes probe USB serial devices. Disable it only if logs show that it is actually taking the ESP32 port and mobile-broadband support is not needed:

```bash
sudo systemctl disable --now ModemManager
```

### Step 9.3 — Confirm protocol compatibility

The current bridge's safety frame is:

```json
{"v":1,"cmd":"STOP","ttlMs":300}
```

The ESP32 firmware must:

1. Read one JSON frame per newline at 115200 baud.
2. Validate the version and command.
3. Stop all motor outputs immediately on `STOP`.
4. Reject expired or malformed motion frames.
5. Stop if the Pi heartbeat disappears.
6. Start in STOP after every boot/reset.

If the ESP32 still expects text such as `Stop` or `Go Straight`, update one side before continuing. Do not run two incompatible protocols.

### Step 9.4 — Direct STOP test with motor power disconnected

Use the project virtual environment and the persistent serial path. Replace `SERIAL_PATH` first:

```bash
SERIAL_PATH=/dev/serial/by-id/YOUR_ESP32_DEVICE
sudo systemctl stop miit-rover-agent
if sudo lsof "$SERIAL_PATH"; then echo 'ABORT: serial port still has an owner'; exit 1; fi
sudo -u rover /opt/miit-rover/source/robot-pi/.venv/bin/python -c \
  "import serial,time; s=serial.Serial('$SERIAL_PATH',115200,timeout=1); time.sleep(2); s.write(b'{\"v\":1,\"cmd\":\"STOP\",\"ttlMs\":300}\\n'); s.flush(); print(s.readline().decode(errors='replace')); s.close()"
sudo systemctl start miit-rover-agent
```

The service must be stopped for this direct test so only one process owns the
serial port. If the test aborts or fails, restart it manually only after the
port owner and failure are understood.

Expected result:

- The ESP32 logs a valid STOP frame.
- Motor-enable outputs remain inactive.
- Preferably the ESP32 returns an acknowledgement.

If opening the serial port resets the ESP32, the two-second wait is required. The production protocol must tolerate reconnect and reboot safely.

## 10. Phase F — Configure the EMQX robot identity

### Step 10.1 — Create MQTT device credentials

In the EMQX deployment console:

```text
Access Control
  -> Authentication (or Client Authentication)
  -> Add
```

Create a unique device user:

```text
Username: robot-01
Password: long, random, unique password
```

This is an MQTT username/password. It is not the EMQX REST API key used by the Supabase Edge Function.

Record from the EMQX deployment Overview page:

```text
MQTT host: broker hostname only
MQTT TLS port: normally 8883 for Serverless
CA certificate: download the deployment CA file
```

Do not write `mqtts://` in `MQTT_HOST`; use only the hostname.

### Step 10.2 — Configure least-privilege topic authorization

For username `robot-01`, allow:

| Action | Topic |
|---|---|
| Subscribe | `miit/robots/robot-01/commands` |
| Publish | `miit/robots/robot-01/acks` |
| Publish | `miit/robots/robot-01/state` |
| Publish | `miit/robots/robot-01/events` |
| Publish | `miit/robots/robot-01/presence` |

Do not allow the robot account to publish to its command topic.

Important: EMQX Cloud documentation notes that built-in authorization may initially operate in a blacklist-style mode where unmatched operations are allowed. Configure the deployment's unmatched/default authorization action to deny, or add the correct all-user deny rule after specific robot allow rules according to the current console's matching order. Test both allowed and forbidden topics.

The MQTT client ID should be:

```text
robot-01-pi
```

The ingestion function described by `project.md` expects the username, client-ID prefix, topic robot segment, and payload robot ID to agree.

### Step 10.3 — Install the EMQX CA certificate

Copy the downloaded CA file to the Pi. From the development computer:

```bash
scp /path/to/emqx-ca.crt YOUR_ADMIN_USER@PI_IP_ADDRESS:/tmp/emqx-ca.crt
```

On the Pi:

```bash
sudo install -o root -g rover -m 0640 /tmp/emqx-ca.crt /etc/miit-rover/emqx-ca.crt
openssl x509 -in /etc/miit-rover/emqx-ca.crt -noout -subject -issuer -dates
```

If EMQX uses a public CA already trusted by Ubuntu, `/etc/ssl/certs/ca-certificates.crt` may work. Using the CA file supplied by the deployment makes the expected trust source explicit.

Check that the installed agent reads `MQTT_CA_FILE` and that the service user
can traverse the directory and read the certificate:

```bash
grep -n MQTT_CA_FILE /opt/miit-rover/source/robot-pi/agent.py
sudo -u rover test -r /etc/miit-rover/emqx-ca.crt
```

The current agent passes the configured file to Paho. Do not use a
`root:root` mode `0750` directory for a CA opened by the `rover` process; file
mode `0644` cannot compensate for missing directory traversal permission. As a
system-trust alternative, install the CA into Ubuntu's certificate store:

```bash
sudo install -o root -g root -m 0644 \
  /etc/miit-rover/emqx-ca.crt \
  /usr/local/share/ca-certificates/miit-emqx-ca.crt
sudo update-ca-certificates
```

Then repeat the TLS test. Do not disable certificate verification and do not use Paho's insecure TLS mode.

### Step 10.4 — Test DNS, TCP, SNI, and TLS

Replace `YOUR_EMQX_HOST`:

```bash
getent hosts YOUR_EMQX_HOST
nc -vz YOUR_EMQX_HOST 8883
openssl s_client \
  -connect YOUR_EMQX_HOST:8883 \
  -servername YOUR_EMQX_HOST \
  -CAfile /etc/miit-rover/emqx-ca.crt \
  -verify_return_error </dev/null
```

Look for:

```text
Verify return code: 0 (ok)
```

If verification fails, check:

- Pi time synchronization
- exact broker hostname
- SNI hostname
- correct CA file
- network/firewall access to 8883

## 11. Phase G — Create the protected environment file

Create the file using an editor that does not expose the password in command history:

```bash
sudoedit /etc/miit-rover/robot.env
```

Use this template:

```text
ROBOT_ID=robot-01
MQTT_HOST=YOUR_EMQX_BROKER_HOST
MQTT_PORT=8883
MQTT_USERNAME=robot-01
MQTT_PASSWORD=YOUR_UNIQUE_MQTT_PASSWORD
MQTT_CA_FILE=/etc/miit-rover/emqx-ca.crt
ESP32_SERIAL_PORT=/dev/serial/by-id/YOUR_ESP32_DEVICE
ESP32_READY_DELAY_SECONDS=2
ROBOT_STATE_DIR=/var/lib/miit-rover
ROBOT_AGENT_VERSION=pi-agent-1.2.0
ROBOT_REQUIRE_TIME_SYNC=true
TIME_SYNC_RETRY_SECONDS=5
PRESENCE_INTERVAL_SECONDS=15
STATE_INTERVAL_SECONDS=5
ROBOT_STATE_MAX_AGE_SECONDS=15
ROBOT_COMMAND_INBOX=/var/lib/miit-rover/command-inbox
ROBOT_COMMAND_ARCHIVE=/var/lib/miit-rover/command-archive
ROBOT_STATE_FILE=/var/lib/miit-rover/robot_state.json
ROBOT_EVENT_OUTBOX=/var/lib/miit-rover/event-outbox
ROBOT_EVENT_ARCHIVE=/var/lib/miit-rover/event-archive
ROBOT_LOG_LEVEL=INFO
```

Rules:

- `MQTT_USERNAME` must equal `ROBOT_ID` for the current ingestion identity checks.
- Do not add `https://` or `mqtts://` to `MQTT_HOST`.
- Use the actual MQTT device password, not the REST API secret.
- If the password contains spaces or special environment-file syntax, enclose its value in quotes and test carefully.
- Never commit this file to Git.

Protect it:

```bash
sudo chown root:root /etc/miit-rover/robot.env
sudo chmod 600 /etc/miit-rover/robot.env
sudo ls -l /etc/miit-rover/robot.env
```

Do not display the file with `cat` during screenshots, presentations, or support requests.

## 12. Phase H — Run the agent with systemd

### Step 12.1 — Create the service unit

```bash
sudoedit /etc/systemd/system/miit-rover-agent.service
```

Use:

```ini
[Unit]
Description=MIIT Rover MQTT bridge
Wants=network-online.target time-sync.target
After=network-online.target time-sync.target
ConditionPathExists=/opt/miit-rover/source/robot-pi/agent.py
ConditionPathExists=/opt/miit-rover/source/robot-pi/message_contract.py
ConditionPathExists=/opt/miit-rover/source/robot-pi/local_store.py

[Service]
Type=simple
User=rover
Group=rover
SupplementaryGroups=dialout video
WorkingDirectory=/opt/miit-rover/source/robot-pi
EnvironmentFile=/etc/miit-rover/robot.env
ExecStart=/opt/miit-rover/source/robot-pi/.venv/bin/python /opt/miit-rover/source/robot-pi/agent.py
Restart=always
RestartSec=3
TimeoutStopSec=10
UMask=0077
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=true
ReadWritePaths=/var/lib/miit-rover

[Install]
WantedBy=multi-user.target
```

The ESP32 watchdog, not systemd, must guarantee that killing or crashing this process stops motor output quickly.

### Step 12.2 — Validate and start

```bash
sudo systemd-analyze verify /etc/systemd/system/miit-rover-agent.service
sudo systemctl daemon-reload
sudo systemctl enable miit-rover-agent
sudo systemctl start miit-rover-agent
sudo systemctl status miit-rover-agent --no-pager
```

Follow logs:

```bash
sudo journalctl -u miit-rover-agent -f
```

Show the last 100 lines without following:

```bash
sudo journalctl -u miit-rover-agent -n 100 --no-pager
```

Expected results:

- Service state is `active (running)`.
- EMQX shows client ID `robot-01-pi`.
- The client subscribes to `miit/robots/robot-01/commands`.
- Retained presence is online.
- No TLS, authentication, serial, or permission error appears.

### Step 12.3 — Verify current-agent startup behavior

The current agent opens the serial port lazily after MQTT connection. If the
ESP32 is absent, it queues an `ESP32_DISCONNECTED` event and remains available
for diagnostics. Confirm the structured journal contains `mqtt_connected` and
either `esp32_connected` or `esp32_unavailable`.

Do not treat `esp32_connected` as proof of the watchdog or physical STOP. It
only proves the serial port opened. Do not weaken safety by pointing the service
at an unrelated serial device.

## 13. Phase I — Test cloud-to-Pi without motor power

### Step 13.1 — Confirm cloud prerequisites

Before using the web app, confirm:

- Supabase migrations matching `project.md` are applied.
- `dispatch-delivery` is deployed.
- `EMQX_API_URL`, `EMQX_API_KEY`, and `EMQX_API_SECRET` are Supabase secrets.
- `ingest-robot-message` is deployed for the return path.
- `ROBOT_INGEST_SECRET` matches the EMQX HTTP action header.
- The EMQX rule covers `acks`, `state`, `events`, and `presence`.
- `robot-01` exists in `public.robots`.
- A verified staff profile has `ADMIN` or `OPERATOR`.

### Step 13.2 — Dispatch one controlled test

1. Keep motor power disconnected.
2. Open the public delivery application.
3. Sign in with the verified admin account.
4. Create a test delivery.
5. Approve it.
6. Assign `robot-01`.
7. Open a Pi terminal running:

```bash
sudo journalctl -u miit-rover-agent -f
```

8. Click **Dispatch mission** once.
9. Verify a new `robot_commands` row becomes `PUBLISHED`, has
   `published_at`, and the delivery becomes `DISPATCHED`.
10. Verify the Pi logs `command_acknowledged`, creates a distinct
    `command-inbox/{commandId}.json`, and records the command ID in
    `commands.db`.
11. Verify the acknowledgement changes the command to `ACKNOWLEDGED`.
12. Do not use a frontend button to advance the delivery. Only a linked
    `MISSION_STARTED` event may advance `DISPATCHED` to `TO_SOURCE`.

Useful cloud query:

```sql
select id, command_type, status, issued_at, published_at,
       acknowledged_at, expires_at, result
from public.robot_commands
where robot_id = 'robot-01'
order by issued_at desc
limit 10;
```

**Live audit result:** FAIL at step 9. Recent command rows had
`status = FAILED`, `published_at = null`, and `result.httpStatus = 403`.
Therefore the command never reached the broker or Pi. Replace the Supabase
EMQX Deployment API URL/App ID/App Secret with credentials authorized for that
deployment, then repeat this test.

### Step 13.3 — Verify the live EMQX return action

While the Pi service is active, record the broker heartbeat timestamp and run:

```sql
select id, status, mode, bridge_online, bridge_last_seen,
       last_seen, telemetry_at, firmware_version
from public.robots
where id = 'robot-01';
```

After migration `202607210010`, `bridge_last_seen` must move forward within 15
seconds of presence. `last_seen` and `telemetry_at` move only when the mission
manager supplies a valid fresh state snapshot. Inspect the EMQX rule action
metrics and latest HTTP response if neither expected field advances. Confirm:

- The action is attached to the rule, not merely saved as a connector.
- The rule selects `miit/robots/+/acks`, `/state`, `/events`, and `/presence`.
- The request URL ends in `/functions/v1/ingest-robot-message`.
- `content-type` is `application/json`.
- `x-emqx-secret` exactly matches the Supabase function secret.
- The request body includes `topic`, decoded `payload`, `clientid`, `username`,
  `qos`, and `timestamp` using the documented EMQX template variables.
- EMQX treats non-2xx responses as failures and has retries enabled.

**Live audit result:** FAIL. The broker contained fresh retained presence and
15-second heartbeats, but Supabase kept the old 18 July `last_seen` and null
telemetry time. Fix this action before testing mission events.

### Step 13.4 — Verify least-privilege MQTT authorization

For `robot-01`, the only allowed operations are:

```text
SUBSCRIBE miit/robots/robot-01/commands
PUBLISH   miit/robots/robot-01/acks
PUBLISH   miit/robots/robot-01/state
PUBLISH   miit/robots/robot-01/events
PUBLISH   miit/robots/robot-01/presence
```

Test that another robot's topic, wildcard subscriptions, publishing to
`commands`, and every unmatched operation are denied. **Live audit result:**
FAIL. An independent client using the robot identity received successful
subscriptions outside this allowlist. Add explicit allow rules followed by
default deny, then repeat negative tests without publishing forged robot data.

### Step 13.5 — Record mission-manager and physical safety evidence

Do not mark Levels 3 or 4 complete until this file includes dated results for:

- Fresh `robot_state.json` updates including a real UTC `at` value.
- Event files moving from `event-outbox` to `event-archive` and advancing the
  correct delivery once in Supabase.
- ESP32 STOP acknowledgement and measured stop latency.
- Serial-heartbeat loss and Pi-process-death stop behavior.
- Physical E-stop operation without Pi, Wi-Fi, or Internet.
- Wheels-raised route execution, obstacle stop, localization failure, reboot,
  and network-loss recovery.

--------------
