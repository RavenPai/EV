# MIIT Rover — Campus Delivery Application

A working React + TypeScript MVP for the MIIT autonomous EV delivery project. It runs immediately with safe demo data and includes the Supabase database, Edge Function, MQTT command contract, and Raspberry Pi bridge needed for the cloud-connected version.

## What is included

- Campus user workflow: create a delivery, validate payload, track status and view the cargo code.
- Administrator workflow: approve or reject requests, assign a robot and dispatch a mission.
- Operator workflow: monitor fleet position, battery, sensors and safe mission controls.
- Responsive dashboard with a live campus route schematic.
- Local demo mode persisted in browser storage.
- Optional Supabase Auth/PostgreSQL/Realtime data mode.
- EMQX REST-to-MQTT Edge Function with expiring, auditable commands.
- Authenticated EMQX-to-Supabase ingestion for acknowledgements, state, events and presence.
- Raspberry Pi MQTT bridge with TLS, idempotency, expiry validation and ESP32 STOP forwarding.

## Run the application

```bash
npm install
npm run dev
```

Open `http://localhost:4173`. Use the profile menu at the top-right to switch between Campus User, Administrator and Robot Operator.

## Production build

```bash
npm run check
npm run build
npm run preview
```

The deployable frontend is written to `dist/`.

## Connect Supabase

1. Create a Supabase project.
2. Install and authenticate the Supabase CLI.
3. Link the local folder and apply the migration:

```bash
npx supabase link --project-ref YOUR_PROJECT_REF
npx supabase db push
```

4. Create users in Supabase Auth and add matching rows to `public.profiles`. Change the required staff users to `ADMIN` or `OPERATOR`.
5. Copy `.env.example` to `.env.local`, fill in the project values and set `VITE_CLOUD_MODE=true`.

Never put the Supabase service-role key or EMQX credentials in a `VITE_*` variable.

## Deploy the command Edge Function

```bash
npx supabase secrets set \
  EMQX_API_URL=https://YOUR-EMQX-HOST:8443 \
  EMQX_API_KEY=YOUR_API_KEY \
  EMQX_API_SECRET=YOUR_API_SECRET \
  ROBOT_INGEST_SECRET=YOUR_RANDOM_64_CHARACTER_SECRET

npx supabase functions deploy dispatch-delivery
npx supabase functions deploy ingest-robot-message --no-verify-jwt
```

Restrict the EMQX credentials to the publish API and the robot command topic namespace. A command is first recorded in `robot_commands`, published with QoS 1, and then acknowledged by the Pi.

Supabase Cron runs once per minute and changes overdue `PENDING` or
`PUBLISHED` commands to `EXPIRED`. Each expiration creates a
`COMMAND_EXPIRED` warning in `robot_events`; it does not automatically retry
the command or change delivery state.

Configure one EMQX HTTP Server rule for the `acks`, `state`, `events`, and
`presence` topics. It must POST to `ingest-robot-message` with the same
`ROBOT_INGEST_SECRET` in the `x-emqx-secret` header. The complete SQL, request
body, database transition map, and test procedure are in `project.md`.

Migrations `202607200008_require_dispatched_mission_start.sql` and
`202607200009_serialize_mission_start.sql` enforce the cloud workflow boundary:
a new `MISSION_STARTED` event is accepted only when the referenced delivery is
already `DISPATCHED` and assigned to that robot, and concurrent starts cannot
advance the same delivery twice.

Migration `202607210010_robot_connectivity_and_event_order.sql` keeps MQTT
bridge presence separate from fresh operational telemetry and rejects delayed
control events that would overwrite a newer ESTOP, fault, or mission state.

## Automated tests

Run the frontend unit tests:

```bash
npm test
```

The Supabase/EMQX integration suite requires Node.js 20, npm, Docker, and a
running Docker daemon:

```bash
npm run test:integration
```

This command starts the local test Supabase stack when needed, resets only that
local database while skipping the optional seed file, runs database lint
and pgTAP tests, serves `ingest-robot-message` locally, and sends the exact HTTP
request contract used by the EMQX rule. It checks authentication, robot
identity, schema validation, acknowledgements, ordered state, idempotent
events, delivery transitions, command expiration, notifications, and
stale-robot handling.

Before starting the CLI, the runner copies the committed `supabase/` project
into a temporary directory without `supabase/.temp`, so an existing hosted
project link is not inherited. Do not replace this command with a linked or
production database reset. The runner also rejects a non-loopback Supabase URL,
uses a test-only ingestion secret, and needs no cloud secrets. It simulates
EMQX's HTTP action; a smoke test through the deployed EMQX broker and the
physical robot is still required before operation. GitHub Actions runs the Pi
contract checks, frontend checks, and local integration suite on every pull
request and push to `main`.

The local Docker namespace is intentionally fixed as
`miit-rover-integration`. If that local stack is already running, the test
runner reuses and resets its database; do not keep development data in that
test namespace.

## Raspberry Pi bridge

```bash
cd robot-pi
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

Set these variables in a root-owned systemd environment file:

```text
ROBOT_ID=robot-01
MQTT_HOST=your-emqx-host
MQTT_PORT=8883
MQTT_USERNAME=robot-01
MQTT_PASSWORD=replace-me
MQTT_CA_FILE=/etc/miit-rover/emqx-ca.crt
ESP32_SERIAL_PORT=/dev/serial/by-id/YOUR_ESP32_DEVICE
ESP32_READY_DELAY_SECONDS=2
ROBOT_STATE_DIR=/var/lib/miit-rover
ROBOT_AGENT_VERSION=pi-agent-1.2.0
ROBOT_REQUIRE_TIME_SYNC=true
ROBOT_STATE_MAX_AGE_SECONDS=15
ROBOT_COMMAND_INBOX=/var/lib/miit-rover/command-inbox
ROBOT_COMMAND_ARCHIVE=/var/lib/miit-rover/command-archive
ROBOT_EVENT_ARCHIVE=/var/lib/miit-rover/event-archive
```

The MQTT account for each robot should be allowed to subscribe only to its command topic and publish only to its state, event, acknowledgement and presence topics.

The bridge fails closed until system time is synchronized, uses a persistent
MQTT session, verifies its command-topic SUBACK, writes every accepted command
to a separate `command-inbox/{commandId}.json` file, publishes retained
presence, and reads telemetry from
`${ROBOT_STATE_DIR}/robot_state.json`. A separate local mission manager writes
durable event files to `${ROBOT_STATE_DIR}/event-outbox/`; see `project.md` for
the exact state/event schemas and remaining Pi implementation. Every state
snapshot must include its real observation timestamp in `at`; the bridge no
longer replaces it with the publish time or forwards a stale snapshot. Events
accepted by the MQTT broker move to `${ROBOT_STATE_DIR}/event-archive/` so a
failed EMQX HTTP action remains recoverable with the same idempotent `eventId`.
The mission manager moves consumed command files to `command-archive`; it must
not replace the inbox with a single overwriteable mission file.

Run the Pi message-contract checks without connecting to hardware:

```bash
npm run test:pi
```

Use [DoneRaspberrypi.md](DoneRaspberrypi.md) as the installation and live
verification record. It is not evidence that navigation or ESP32 safety is
complete unless its pass/fail table contains captured results.

### Live Pi audit: 21 July 2026

The deployed `robot-01` bridge was inspected over SSH without changing the Pi:

- The deployed git commit and `agent.py` checksum matched this repository's
  pre-hardening version; systemd was enabled and active.
- The Pi had an established MQTT/TLS connection, and an independent read-only
  broker client received its retained presence plus the 15-second heartbeat.
- Supabase did not receive those heartbeats: `robot-01.last_seen` remained at
  18 July 2026. The EMQX HTTP action/rule therefore still needs repair.
- Recent dispatch commands still failed before broker publication with HTTP
  403 from the EMQX Deployment API.
- The robot MQTT identity was permitted to subscribe beyond its intended own
  topic scope. Change EMQX authorization to explicit per-robot allow rules
  followed by default deny.
- The persistent USB serial adapter was present, but no `robot_state.json`,
  mission-manager output, physical STOP confirmation, or autonomous-navigation
  evidence was available.
- The Pi was synchronized at audit time, but Chrony's boot wait unit was
  disabled and the bridge start timestamp predated the clock correction.
  Enable `chrony-wait.service` so expiring commands are not evaluated before
  time is trustworthy.

## MQTT topics

```text
miit/robots/{robotId}/commands
miit/robots/{robotId}/acks
miit/robots/{robotId}/state
miit/robots/{robotId}/events
miit/robots/{robotId}/presence
```

Do not publish video frames or continuous motor-control messages through MQTT.

## Free frontend hosting

For Cloudflare Pages:

1. Push this folder to GitHub or GitLab.
2. Create a Pages project from the repository.
3. Build command: `npm run build:cloudflare`.
4. Output directory: `dist`.
5. Add the three `VITE_*` variables in the Pages project settings.

The included `wrangler.jsonc` configures Cloudflare static assets to serve
`index.html` for React Router navigation requests.

## Safety boundary

The public application sends only mission-level commands plus PAUSE, RESUME, RETURN_HOME and ESTOP requests. A separate Raspberry Pi mission manager must perform local navigation; the MQTT bridge does not implement navigation. The ESP32 performs wheel control and must independently enforce its heartbeat timeout, hardware E-stop and STOP-after-boot behavior. Internet loss must cause a safe local response, not uncontrolled motion.
