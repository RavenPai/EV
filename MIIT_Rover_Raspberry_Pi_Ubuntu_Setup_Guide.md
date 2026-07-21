# MIIT Rover Raspberry Pi Ubuntu Setup Guide

Cloud-to-robot installation, verification, and autonomous-navigation roadmap
Prepared for the MIIT Campus Delivery EV project
Updated: 18 July 2026

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

### 2.3 Source/document version mismatch that must be checked

The supplied `project.md` describes:

- `ingest-robot-message`
- later July 2026 database migrations
- low-rate state publication
- 15-second presence publication
- a durable event outbox
- `pi-agent-1.1.0`

The local repository copy available during this analysis contains a 121-line `robot-pi/agent.py` that provides only basic command subscription, acknowledgement, mission-file writing, and serial STOP forwarding. It does not contain the complete state/presence/event publisher described by `project.md`.

This may simply mean that the local copy is older than the user's current GitHub repository. Before installation, verify the exact code that will be copied to the Pi:

```bash
cd /path/to/your/repository
git log -1 --oneline
find supabase/functions -maxdepth 2 -type f -print
find supabase/migrations -maxdepth 1 -type f -print | sort
wc -l robot-pi/agent.py
grep -nE 'MQTT_CA_FILE|PRESENCE_INTERVAL|STATE_INTERVAL|event-outbox|robot_state' robot-pi/agent.py
```

For the full `project.md` behavior, the last command must find the corresponding implementation. If it finds nothing, install and test only the basic bridge, then update the code before expecting telemetry, events, or cloud presence.

### 2.4 Current `navigation.py` must not control a powered robot as written

The earlier `navigation.py` is useful as a camera-classification experiment, but it is unsafe and incompatible with the new delivery architecture because:

- Source and destination are hard-coded.
- It does not consume `mission_request.json`.
- If no sign is detected, it eventually sends **Go Straight**. Sensor uncertainty must cause STOP, not movement.
- Turns are based only on elapsed time, not encoders, odometry, or position.
- It has no LiDAR obstacle avoidance.
- It has no command sequence, TTL, CRC, acknowledgement, or local heartbeat.
- It uses old text commands such as `Go Straight`, while the new bridge uses JSON-line safety frames.
- It uses `cv2.imshow`, which normally fails in a headless systemd service.
- It does not publish the cloud mission events defined in `project.md`.
- A five-second sleep represents package loading rather than a verified local action.

Do not start that program automatically, and do not test it with wheels touching the floor. Its image classifier may later be retained as an additional waypoint-confirmation sensor after the fail-safe behavior is rewritten.

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

- Ubuntu 24.04 arm64: use ROS 2 Jazzy.
- Ubuntu 22.04 arm64: use ROS 2 Humble instead.
- Do not mix ROS distributions or install Jazzy packages on Ubuntu 22.04.

ROS 2 Jazzy officially supports Ubuntu 24.04 arm64 and is supported through May 2029. Python virtual environments isolate the bridge dependencies from Ubuntu and ROS packages. EMQX Serverless requires TLS and documents port 8883 for MQTT clients.

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
systemctl status systemd-timesyncd --no-pager
```

If synchronization is disabled:

```bash
sudo timedatectl set-ntp true
```

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
sudo install -d -o root  -g root  -m 0750 /etc/miit-rover
sudo install -d -o rover -g rover -m 0750 /var/lib/miit-rover
sudo install -d -o rover -g rover -m 0750 /var/lib/miit-rover/event-outbox
```

Intended layout:

```text
/opt/miit-rover/                 application source
/opt/miit-rover/robot-pi/.venv/ Python environment
/etc/miit-rover/robot.env       root-owned MQTT secrets
/etc/miit-rover/emqx-ca.crt     EMQX CA certificate when required
/var/lib/miit-rover/            persistent robot state
/var/lib/miit-rover/commands.db duplicate-command database
/var/lib/miit-rover/event-outbox/
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
grep -nE 'MQTT_CA_FILE|PRESENCE_INTERVAL|STATE_INTERVAL|event-outbox|robot_state' \
  /opt/miit-rover/source/robot-pi/agent.py
```

Decision gate:

- If the full implementation is present, use all state/event/presence steps in this guide.
- If it is absent, the basic command bridge can still be tested, but Supabase telemetry and event transitions will not work.
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
/dev/serial/by-id/<ESP32_BY_ID_DEVICE>
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
sudo -u rover /opt/miit-rover/source/robot-pi/.venv/bin/python -c \
  "import serial,time; s=serial.Serial('$SERIAL_PATH',115200,timeout=1); time.sleep(2); s.write(b'{\"v\":1,\"cmd\":\"STOP\",\"ttlMs\":300}\\n'); s.flush(); print(s.readline().decode(errors='replace')); s.close()"
```

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
sudo install -o root -g root -m 0644 /tmp/emqx-ca.crt /etc/miit-rover/emqx-ca.crt
openssl x509 -in /etc/miit-rover/emqx-ca.crt -noout -subject -issuer -dates
```

If EMQX uses a public CA already trusted by Ubuntu, `/etc/ssl/certs/ca-certificates.crt` may work. Using the CA file supplied by the deployment makes the expected trust source explicit.

Check that the installed agent actually reads `MQTT_CA_FILE`:

```bash
grep -n MQTT_CA_FILE /opt/miit-rover/source/robot-pi/agent.py
```

If the old agent produces no result, setting `MQTT_CA_FILE` alone has no effect. The preferred fix is to update the agent so it passes the configured CA file to Paho. As a temporary system-trust alternative, install the CA into Ubuntu's certificate store:

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
ROBOT_STATE_DIR=/var/lib/miit-rover
ROBOT_AGENT_VERSION=pi-agent-1.1.0
PRESENCE_INTERVAL_SECONDS=15
STATE_INTERVAL_SECONDS=5
ROBOT_STATE_FILE=/var/lib/miit-rover/robot_state.json
ROBOT_EVENT_OUTBOX=/var/lib/miit-rover/event-outbox
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
Wants=network-online.target
After=network-online.target
ConditionPathExists=/opt/miit-rover/source/robot-pi/agent.py

[Service]
Type=simple
User=rover
Group=rover
SupplementaryGroups=dialout video
WorkingDirectory=/opt/miit-rover/source/robot-pi
EnvironmentFile=/etc/miit-rover/robot.env
ExecStart=/opt/miit-rover/source/robot-pi/.venv/bin/python /opt/miit-rover/source/robot-pi/agent.py
Restart=on-failure
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
- Retained presence is online if the full agent is installed.
- No TLS, authentication, serial, or permission error appears.

### Step 12.3 — Understand an old-agent limitation

The 121-line basic `agent.py` opens the serial port during module startup. If the ESP32 is absent, the service exits and restarts. The richer behavior described by `project.md` should instead report the serial fault safely and remain available for diagnostics.

If the service repeatedly fails with a missing serial device, either:

1. connect the ESP32 before starting the basic agent, or
2. update to the robust agent implementation before production use.

Do not weaken safety by pointing it at an unrelated serial device.

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

8. Press Dispatch once.

Expected data path:

```text
Web app
-> Supabase dispatch function
-> robot_commands audit row
-> EMQX command topic
-> Pi agent
-> command validation
-> mission_request.json
-> acknowledgement topic
-> EMQX HTTP action
-> Supabase command status
```

Inspect the local request:

```bash
sudo -u rover jq . /var/lib/miit-rover/mission_request.json
```

It should contain:

- `START_MISSION`
- `commandId`
- delivery ID
- source location ID
- destination location ID
- map version

At this point the robot should still not move because the mission manager has not accepted and executed the mission.

### Step 13.3 — Verify duplicate and expiration safety

Using a temporary authorized test publisher or EMQX test client, verify:

- Resending the exact same `commandId` produces a duplicate acknowledgement and does not execute again.
- A past `expiresAt` is rejected.
- `robotId=robot-02` is rejected by `robot-01`.
- An unsupported command is rejected.
- Malformed JSON produces STOP/fault behavior.

Never give the robot's MQTT account permission to publish its own command messages merely to simplify testing. Use a separate test identity and delete or disable it afterward.

### Step 13.4 — Verify retained presence and offline behavior

This requires the full agent described by `project.md`.

```bash
sudo systemctl stop miit-rover-agent
```

Expected:

- EMQX Last Will publishes retained `online=false`, or
- the Supabase stale-robot job marks the robot offline after its configured timeout.

Restart:

```bash
sudo systemctl start miit-rover-agent
```

Expected:

- retained `online=true`
- `last_seen` refreshes
- frontend shows the robot online

Cloud offline status is for visibility. The ESP32 must stop much faster through the local heartbeat timeout.

## 14. Phase J — State and event return-path test

Only perform this section if `agent.py` implements `robot_state.json` and `event-outbox` publishing.

### Step 14.1 — State snapshot contract

The mission manager must atomically produce:

```text
/var/lib/miit-rover/robot_state.json
```

Example content:

```json
{
  "status": "BUSY",
  "mode": "AUTO",
  "battery": 82,
  "signal": 91,
  "speedMps": 0.0,
  "locationId": "loc-fcs",
  "currentDeliveryId": null,
  "lidar": "OK",
  "camera": "OK",
  "esp32": "OK",
  "motorTempC": 32.0
}
```

Write to a temporary file and rename it. Never let the agent read half-written JSON.

Expected result after the state interval:

- MQTT message on `miit/robots/robot-01/state`
- EMQX action success
- Supabase `public.robots` update
- frontend fleet card update

### Step 14.2 — Event outbox contract

The mission manager writes one complete event per file:

```text
/var/lib/miit-rover/event-outbox/<event-uuid>.json
```

It must first write `<event-uuid>.tmp`, then atomically rename it to `.json`.

Example:

```json
{
  "eventId": "a-real-uuid",
  "deliveryId": "the-active-delivery-uuid",
  "commandId": "the-start-command-uuid",
  "type": "MISSION_STARTED",
  "severity": "INFO",
  "at": "2026-07-18T12:00:00Z",
  "payload": {}
}
```

The same event file and `eventId` are retained during retry. A new ID must not be generated merely because publication failed.

Expected cloud transition:

```text
DISPATCHED --MISSION_STARTED--> TO_SOURCE
```

Test every transition in the table from `project.md` with the motors disconnected before connecting navigation.

## 15. Phase K — Implement the local mission manager

### Step 15.1 — Keep responsibilities separate

Create a second service, for example:

```text
/opt/miit-rover/source/robot-pi/mission_manager.py
```

The MQTT agent should handle:

- cloud connection
- command validation
- duplicate detection
- acknowledgement
- presence/state/event transport

The mission manager should handle:

- active mission state machine
- readiness validation
- source/destination goal lookup
- navigation goal execution
- package load/release confirmation
- local pause/resume/return-home logic
- state snapshots and durable mission events
- recovery after reboot

### Step 15.2 — Required persistent state machine

Use at least:

```text
BOOT_SAFE
IDLE
VALIDATING_MISSION
NAVIGATING_TO_SOURCE
WAITING_FOR_LOAD
NAVIGATING_TO_DESTINATION
WAITING_FOR_RELEASE
RETURNING_HOME
PAUSED
FAULT
ESTOP
```

All moving states must transition to STOP when any of these becomes unhealthy:

- physical E-stop
- ESP32 heartbeat
- ESP32 fault response
- LiDAR health
- localization health
- obstacle safety layer
- command TTL at the motor layer
- motor feedback
- battery critical threshold

### Step 15.3 — Map cloud IDs to real navigation poses

The database uses stable IDs:

```text
loc-home
loc-fcs
loc-fcst
loc-library
loc-data
loc-rector
loc-canteen
```

Create a robot-side map configuration containing real map-frame poses, not the frontend's schematic `x/y` percentages. For example:

```yaml
map_version: miit-campus-v1
locations:
  loc-home:    {x: 0.0,  y: 0.0, yaw: 0.0}
  loc-fcs:     {x: 4.2,  y: 1.5, yaw: 1.57}
  loc-library: {x: 12.4, y: 8.1, yaw: 3.14}
```

The numbers above are examples only. Measure them from the saved SLAM map.

Reject a mission if:

- its location ID is absent
- its `mapVersion` does not match
- source equals destination
- localization is unavailable
- battery/sensors/ESP32 are not ready

### Step 15.4 — Mission request processing

The manager should:

1. Read `mission_request.json` only after an atomic rename by the agent.
2. Compare `commandId` with the last consumed request.
3. Persist the active mission before issuing movement.
4. Validate every prerequisite.
5. Emit `MISSION_STARTED` only after the navigation subsystem accepts the source goal.
6. Emit arrival only after navigation reports success, target ID matches, velocity is nearly zero, and the result is stable briefly.
7. Require a real load/release signal rather than a fixed sleep.
8. Preserve active mission state through reboot.

### Step 15.5 — Do not enable the manager service before code exists

After implementation and unit tests, a service can resemble:

```ini
[Unit]
Description=MIIT Rover local mission manager
After=network-online.target miit-rover-base-controller.service
Requires=miit-rover-base-controller.service

[Service]
Type=simple
User=rover
Group=rover
SupplementaryGroups=dialout video
WorkingDirectory=/opt/miit-rover/source/robot-pi
ExecStart=/opt/miit-rover/source/robot-pi/.venv/bin/python /opt/miit-rover/source/robot-pi/mission_manager.py
Restart=on-failure
RestartSec=2
NoNewPrivileges=true
ProtectSystem=strict
ProtectHome=true
ReadWritePaths=/var/lib/miit-rover /run/miit-rover

[Install]
WantedBy=multi-user.target
```

Do not create and enable this unit with a placeholder mission manager that automatically reports success.

## 16. Phase L — Choose the navigation implementation

### Option A — Restricted marker/line route prototype

Use this only for a short, controlled demonstration route.

Minimum requirements:

- line/route following with a safe stop on loss
- LiDAR obstacle stop independent of camera labels
- wheel encoder feedback
- no time-only turns
- target IDs loaded from the active cloud mission
- explicit source load and destination release input
- low speed and supervised operation
- all required cloud events

Refactor the existing classifier so that:

```text
No image / low confidence / camera failure -> STOP
Unknown marker -> STOP or continue only under a separately verified route controller
Target marker -> stop, verify, then emit arrival
```

Do not use image-classification confidence as the only collision-avoidance sensor.

### Option B — ROS 2 Jazzy + Nav2 (recommended for the long-term project)

Use this for map-based autonomous navigation. The required interfaces are:

```text
/scan       <- YDLidar X2L ROS driver
/odom       <- encoder/IMU odometry
/tf         <- map -> odom -> base_link -> laser_frame
/cmd_vel    -> single base-controller serial owner
Nav2 action <- mission manager goals
```

The robot must have reliable wheel encoder odometry. A 2D LiDAR alone is not a replacement for odometry.

#### Step 16.1 — Confirm Ubuntu before installing ROS

```bash
. /etc/os-release
echo "$PRETTY_NAME"
dpkg --print-architecture
```

For Ubuntu 24.04 arm64, install ROS 2 Jazzy by following the current official ROS Ubuntu deb-package instructions. Repository bootstrap commands can change, so use the official page at installation time:

```text
https://docs.ros.org/en/jazzy/Installation/Ubuntu-Install-Debs.html
```

Install a headless set on the Pi:

```bash
sudo apt install -y \
  ros-jazzy-ros-base \
  ros-jazzy-navigation2 \
  ros-jazzy-nav2-bringup \
  ros-jazzy-slam-toolbox \
  ros-jazzy-robot-localization \
  ros-jazzy-teleop-twist-keyboard \
  python3-colcon-common-extensions \
  python3-rosdep
```

Initialize dependencies once:

```bash
sudo rosdep init
rosdep update
```

If `rosdep init` says it is already initialized, do not overwrite it; run only `rosdep update`.

Source ROS for an interactive shell:

```bash
source /opt/ros/jazzy/setup.bash
ros2 --help
```

Use a laptop for RViz when possible so the Pi can remain headless.

#### Step 16.2 — Identify the LiDAR separately from the ESP32

Connect the YDLidar through its supported USB adapter. Run:

```bash
lsusb
ls -l /dev/serial/by-id/
```

Record which stable path belongs to the ESP32 and which belongs to the LiDAR. Never assume the LiDAR is always `/dev/ttyUSB0`.

Add the ROS/base-controller service user to the device's group, usually `dialout`.

#### Step 16.3 — Install YDLidar SDK and ROS driver

YDLidar's official ROS 2 driver depends on the YDLidar SDK. Use the vendor repository and a version/branch tested with your X2L and ROS distribution:

```bash
mkdir -p ~/ydlidar_build
cd ~/ydlidar_build
git clone https://github.com/YDLIDAR/YDLidar-SDK.git
cd YDLidar-SDK
mkdir build
cd build
cmake ..
make -j2
sudo make install
sudo ldconfig
```

Create the ROS workspace:

```bash
mkdir -p ~/ydlidar_ros2_ws/src
cd ~/ydlidar_ros2_ws/src
git clone -b humble https://github.com/YDLIDAR/ydlidar_ros2_driver.git
cd ~/ydlidar_ros2_ws
source /opt/ros/jazzy/setup.bash
rosdep install --from-paths src --ignore-src -r -y
colcon build --symlink-install
source install/setup.bash
```

The vendor currently documents its `humble` branch for Humble, Jazzy, and similar ROS 2 releases. Pin the tested Git commit in your project record rather than automatically pulling future changes onto the robot.

Avoid vendor instructions that broadly `chmod 777` every serial device. Prefer group permission and stable udev/by-id paths.

#### Step 16.4 — Configure the exact X2L parameters

Copy the driver parameter file into your robot configuration repository and set:

- stable serial path
- exact X2L baud rate from its datasheet/adapter
- triangle LiDAR type if required by the model
- single-channel setting for the exact hardware
- minimum/maximum valid range
- scan frequency
- `laser_frame`
- inversion/reversion based on mounting direction

Do not blindly reuse the driver's default `230400` baud rate. Confirm the X2L model/adapter requirement.

Test without Nav2:

```bash
source /opt/ros/jazzy/setup.bash
source ~/ydlidar_ros2_ws/install/setup.bash
ros2 launch ydlidar_ros2_driver ydlidar_launch.py
```

In another terminal:

```bash
source /opt/ros/jazzy/setup.bash
source ~/ydlidar_ros2_ws/install/setup.bash
ros2 topic hz /scan
ros2 topic echo /scan --once
```

Pass criteria:

- stable scan rate
- no repeated reconnect
- correct ranges when an object is moved around the sensor
- no collision with the ESP32 serial path

#### Step 16.5 — Test the USB camera headlessly

```bash
v4l2-ctl --list-devices
ls -l /dev/v4l/by-id/ 2>/dev/null
v4l2-ctl --device=/dev/video0 --all
v4l2-ctl --device=/dev/video0 --stream-mmap --stream-count=30 --stream-to=/dev/null
```

Use `/dev/v4l/by-id/...` when available. The `rover` user needs the `video` group.

Do not use `cv2.imshow` in a headless service. Publish debug frames only during controlled development, and do not send video through MQTT.

#### Step 16.6 — Implement the base controller

For a normal four-wheel skid-steer chassis:

```text
Nav2 /cmd_vel (linear.x, angular.z)
-> left/right target wheel velocities
-> ESP32 sequence + TTL command
-> motor PID
-> encoder feedback
-> wheel odometry
```

For mecanum/omnidirectional wheels, the controller must additionally handle `linear.y` and four-wheel inverse kinematics. Do not use a differential-drive conversion for a mecanum base.

The ESP32 protocol should include at least:

```text
version
sequence number
command type
velocity targets
short TTL
frame integrity check/CRC
ESP32 acknowledgement sequence
measured wheel velocities
fault flags
physical E-stop state
battery and motor measurements
```

Send a local heartbeat approximately every 50–100 ms. Select and test a short ESP32 timeout that stops safely when the Pi process, cable, or OS fails.

#### Step 16.7 — Establish the ROS frame tree

Required frame relationship:

```text
map -> odom -> base_link -> laser_frame
                     \-> camera_link
```

Provide:

- static transforms from measured sensor mounting positions
- `odom -> base_link` from encoder/IMU state estimation
- `map -> odom` from SLAM or AMCL

Verify:

```bash
ros2 run tf2_tools view_frames
ros2 topic hz /odom
ros2 topic hz /scan
```

Nav2 should not be tuned until these transforms and odometry are stable.

#### Step 16.8 — Map, localize, and navigate

Recommended order:

1. Teleoperate at very low speed with wheels and direction verified.
2. Verify encoder signs and distance scale.
3. Fuse encoder/IMU odometry with `robot_localization` if an IMU is available.
4. Run SLAM Toolbox and create the map.
5. Save the map and assign version `miit-campus-v1`.
6. Measure map poses for each `loc-*` destination.
7. Configure robot footprint and inflation radius from the real chassis.
8. Configure low maximum speed/acceleration.
9. Configure Nav2 and localization.
10. Test one goal without cloud integration.
11. Connect the mission manager to the Nav2 action.

Nav2's official guide covers mapping/localization, footprint, plugins, and physical navigation setup. A production campus route also requires an outdoor/weather/terrain assessment; a 2D indoor-style SLAM setup should not automatically be assumed safe outdoors.

## 17. Phase M — ESP32 safety requirements before motor power

Do not connect motor power until all are implemented and individually tested:

- motor output disabled after ESP32 boot/reset
- independent physical E-stop
- ESP32 task watchdog
- Pi heartbeat timeout
- command TTL
- sequence and frame-integrity validation
- maximum speed and acceleration
- encoder direction and plausibility checks
- stalled-wheel detection
- motor overcurrent response when current sensing exists
- motor overtemperature response when temperature sensing exists
- invalid movement combination rejection
- explicit reset procedure after E-stop
- STOP on unknown command or malformed frame
- STOP on serial disconnect

The web `ESTOP` is an additional remote request. It cannot guarantee delivery over a failed Internet link and is not a substitute for the physical circuit.

## 18. Full hardware test order

Record date, software commit, firmware version, tester, result, and evidence for every step.

1. Pi and ESP32 on; motor power disconnected.
2. Verify Python environment and systemd service.
3. Verify MQTT/TLS identity and topic ACL.
4. Verify valid command acknowledgement.
5. Verify duplicate, expired, malformed, unsupported, and wrong-robot rejection.
6. Verify state, event, and presence return path.
7. Verify EMQX HTTP action retry/error metrics.
8. Verify Supabase event transition rules using simulated events.
9. Verify STOP frame at the ESP32.
10. Kill the Pi agent and confirm ESP32 safe state.
11. Unplug serial and confirm ESP32 safe state.
12. Reboot Pi and ESP32 and confirm STOP-after-boot.
13. Verify physical E-stop without the Pi and Internet.
14. Raise wheels and connect motor power.
15. Confirm every motor's direction at minimum power.
16. Confirm encoder direction and count scaling.
17. Tune low-speed wheel PID with wheels raised.
18. Test command TTL and heartbeat timeout while wheels are raised.
19. Test a tethered low-speed straight line on an isolated floor.
20. Add and verify LiDAR obstacle stop.
21. Verify localization and one local navigation goal.
22. Verify one cloud mission with no cargo.
23. Test Internet loss in every mission state.
24. Test Pi reboot and ESP32 reboot during a paused mission.
25. Test low battery, LiDAR loss, localization loss, and blocked wheel.
26. Test pickup and destination confirmation.
27. Test supervised cargo at low speed in an isolated area.

Do not begin unsupervised campus trials until stopping distance, obstacle detection, watchdogs, E-stop, fault recovery, and route boundaries have documented pass results.

## 19. Final end-to-end acceptance checklist

### Cloud and security

- [ ] Public app requires Supabase authentication.
- [ ] A normal user cannot access another user's delivery.
- [ ] Only verified staff can approve, assign, and dispatch.
- [ ] Edge Function secrets are not in the frontend.
- [ ] Robot MQTT username is unique.
- [ ] Robot ACL is default-deny and least privilege.
- [ ] EMQX REST API key and MQTT password are different credentials.

### Raspberry Pi

- [ ] Ubuntu is supported 64-bit and fully updated.
- [ ] Correct UTC time synchronization is active.
- [ ] Agent runs as `rover`, not root.
- [ ] Secrets file is root-owned mode 600.
- [ ] ESP32 and LiDAR use stable device paths.
- [ ] Agent reconnects after network loss.
- [ ] Agent starts after reboot.
- [ ] Duplicate database survives reboot.
- [ ] Logs do not reveal secrets.

### MQTT and backend return path

- [ ] Valid command is acknowledged.
- [ ] Expired, wrong-robot, malformed, and duplicate commands are safe.
- [ ] Presence becomes online/offline correctly.
- [ ] State reaches `public.robots`.
- [ ] Events reach `public.robot_events`.
- [ ] Impossible delivery transitions are rejected.
- [ ] QoS 1 event retry remains idempotent.

### Navigation and motor safety

- [ ] Exactly one process owns the ESP32 serial port.
- [ ] ESP32 is STOP after boot.
- [ ] Physical E-stop works without Pi/Internet.
- [ ] Pi heartbeat loss stops motors.
- [ ] Serial loss stops motors.
- [ ] LiDAR/localization/odometry faults stop movement.
- [ ] Speed and acceleration limits are enforced locally.
- [ ] Arrival requires position tolerance plus stopped velocity.
- [ ] Internet loss never creates uncontrolled motion.

Only when all applicable items pass is the system ready for a supervised campus pilot.

## 20. Daily operation commands

Check services:

```bash
systemctl is-active miit-rover-agent
sudo systemctl status miit-rover-agent --no-pager
sudo journalctl -u miit-rover-agent -n 100 --no-pager
```

Check devices:

```bash
ls -l /dev/serial/by-id/
ls -l /dev/v4l/by-id/ 2>/dev/null
```

Check time and network:

```bash
timedatectl
ip -br address
getent hosts YOUR_EMQX_HOST
```

Restart only while the robot is physically safe:

```bash
sudo systemctl restart miit-rover-agent
```

Safe OS shutdown:

```bash
sudo systemctl stop miit-rover-agent
sudo shutdown -h now
```

Stopping the Linux service is not itself a motor-safety mechanism. The local ESP32 heartbeat timeout must already be working.

## 21. Troubleshooting

### `KeyError: MQTT_HOST`

The systemd environment file was not loaded or the variable is missing.

```bash
sudo systemctl cat miit-rover-agent
sudo systemctl status miit-rover-agent --no-pager
```

Do not print the secret file into a public log.

### `Permission denied: /dev/ttyUSB0`

```bash
stat -c '%U %G %A %n' /dev/ttyUSB0
id rover
sudo usermod -aG dialout rover
sudo systemctl restart miit-rover-agent
```

### Serial device disappears or changes number

Use:

```bash
ls -l /dev/serial/by-id/
```

Update `ESP32_SERIAL_PORT` to the persistent path, then restart the service.

### `Device or resource busy`

```bash
sudo lsof /dev/ttyUSB0
```

Close `minicom`, `screen`, Arduino Serial Monitor, ModemManager, or a second robot process. Enforce one serial owner.

### MQTT connection refused/not authorized

Check:

- MQTT device username/password, not REST credentials
- correct broker hostname
- port 8883
- EMQX authentication entry
- client ID `robot-01-pi`
- subscribe ACL for the command topic

### TLS certificate verification error

Check:

```bash
timedatectl
openssl x509 -in /etc/miit-rover/emqx-ca.crt -noout -dates
```

Then repeat the `openssl s_client` test with the exact broker hostname and `-servername`.

### Command is acknowledged but robot does not move

This is expected when only the transport bridge is installed. `ACKNOWLEDGED` means the Pi accepted the mission request; it does not mean navigation succeeded. Implement and start the mission manager and base controller, then rely on robot events for physical progress.

### Frontend never receives state/presence/events

Check in this order:

1. Does the installed agent implement state/event/presence publishing?
2. Does EMQX receive the MQTT message?
3. Does the EMQX rule match the topic?
4. Does its HTTP action have the correct `x-emqx-secret`?
5. Is `ingest-robot-message` deployed with the intended JWT setting?
6. Do topic robot ID, MQTT username, client ID prefix, and payload robot ID match?
7. What do Supabase Function logs show?

### `cv2.imshow` or display error

The process is running headlessly. Remove UI display code and use structured logs or optional recorded debug output. Do not run the old `navigation.py` unchanged as a service.

### LiDAR and ESP32 swap `/dev/ttyUSB0` and `/dev/ttyUSB1`

This is exactly why `/dev/serial/by-id/...` paths are required. Assign each service the correct stable path.

### Service restart loop

```bash
sudo systemctl status miit-rover-agent --no-pager
sudo journalctl -u miit-rover-agent -n 200 --no-pager
```

Common causes are missing ESP32, wrong path, environment file error, TLS failure, bad credentials, or a stale source path in `ExecStart`.

## 22. Recommended immediate work sequence for this project

Follow this exact order:

1. Confirm the GitHub source matches the advanced `project.md` version.
2. Complete Ubuntu, Python, service user, directory, and venv setup.
3. Update ESP32 to accept the versioned STOP frame and implement watchdog STOP.
4. Create EMQX `robot-01` MQTT credentials and default-deny ACL.
5. Start the MQTT agent under systemd with motor power disconnected.
6. Verify web dispatch -> EMQX -> Pi -> ACK -> Supabase.
7. Verify presence/state/event ingestion.
8. Replace or refactor the old `navigation.py`; do not use its default-forward behavior.
9. Implement exactly one serial-owning base controller.
10. Implement the mission manager and its persistent state machine.
11. Integrate LiDAR, encoder odometry, localization, and Nav2 or a restricted test-route controller.
12. Complete the supervised hardware test order.

This sequence gives you a working, testable result at every stage without pretending that an MQTT connection is already autonomous navigation.

## 23. Primary technical references

- [Python virtual environments](https://docs.python.org/3/library/venv.html)
- [EMQX Cloud Paho Python connection guide](https://docs.emqx.com/en/cloud/latest/connect_to_deployments/python_sdk.html)
- [EMQX Cloud Serverless deployment and client authentication](https://docs.emqx.com/en/cloud/latest/create/serverless.html)
- [EMQX Cloud authorization guidance](https://docs.emqx.com/en/cloud/latest/best_practices/acl_v5.html)
- [EMQX MQTT client development practices](https://docs.emqx.com/en/cloud/latest/best_practices/client_development.html)
- [ROS 2 Jazzy Ubuntu installation](https://docs.ros.org/en/jazzy/Installation/Ubuntu-Install-Debs.html)
- [ROS 2 release/platform support, REP 2000](https://www.ros.org/reps/rep-2000.html)
- [Nav2 getting started and first-time robot setup](https://docs.nav2.org/getting_started/index.html)
- [Nav2 navigation with SLAM](https://docs.nav2.org/tutorials/docs/navigation2_with_slam.html)
- [YDLidar SDK](https://github.com/YDLIDAR/YDLidar-SDK)
- [YDLidar ROS 2 driver](https://github.com/YDLIDAR/ydlidar_ros2_driver)
