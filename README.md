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
- Raspberry Pi MQTT bridge with TLS, idempotency, expiry validation, timed
  `STOP` forwarding for `PAUSE`, and a distinct latching `ESTOP` request.

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
concurrent starts cannot advance the same delivery twice. Migration `011`
retains the normal `DISPATCHED` requirement but also accepts an `ASSIGNED`
delivery during the narrow publish-response race when the event is linked to
the valid reserved `START_MISSION` command; the robot event itself proves that
the command reached the robot.

Migration `202607210010_robot_connectivity_and_event_order.sql` keeps MQTT
bridge presence separate from fresh operational telemetry and rejects delayed
control events that would overwrite a newer ESTOP, fault, or mission state.

Migration `202607210011_robot_ingestion_safety_followup.sql` is the append-only
hardening layer for the current Edge Functions. It adds server receipt time in
`robots.telemetry_received_at`, serializes mission/control reservations, keeps
ambiguous broker calls in the non-expiring `PUBLISH_UNKNOWN` state, and uses
`finalize_robot_command_publish()` to finalize command publication and delivery
dispatch atomically. While a robot is `PAUSED`, mission progress and
`RETURN_HOME` motion remain blocked until the authorized `RESUMED` event. A
timeout or broker 5xx must be reconciled from robot
evidence or an explicit staff decision; it must not be blindly retried with a
new command ID. Conflicting event content under an existing event ID is rejected.

The EMQX rule must forward the complete broker envelope, including its message
identifier:

```sql
SELECT
  bin2hexstr(id) AS mqttMessageId,
  topic,
  payload,
  clientid,
  username,
  qos,
  timestamp
FROM
  "miit/robots/+/acks",
  "miit/robots/+/state",
  "miit/robots/+/events",
  "miit/robots/+/presence"
```

```json
{
  "mqttMessageId": "${mqttMessageId}",
  "topic": "${topic}",
  "payload": ${payload},
  "clientid": "${clientid}",
  "username": "${username}",
  "qos": ${qos},
  "timestamp": ${timestamp}
}
```

EMQX exposes its broker message `id` as binary, so the rule must convert it
with `bin2hexstr(id)`. Keep `${mqttMessageId}` quoted in the JSON body: the
result is normally shown as 32 hexadecimal characters, while the ingestion
endpoint deliberately accepts it as an opaque non-empty string of at most 256
characters.

For command publication, only EMQX HTTP 200 may finalize the command and move an
assigned delivery to `DISPATCHED`. HTTP 202 with
`no_matching_subscribers` is a known non-delivery: the command is recorded as
`FAILED` and the delivery remains `ASSIGNED`. Timeouts, 5xx, and other
unrecognized response statuses remain `PUBLISH_UNKNOWN` for reconciliation.

## Automated tests

Run the frontend unit tests:

```bash
npm test
```

Run the hardware-free Pi bridge and ESP32 protocol tests:

```bash
npm run test:pi
npm run test:esp32
```

The ESP32 test verifies host-side framing, CRC vectors, and rejection of motion
without a matching positive acknowledgement. It does not compile, flash, or
hardware-test the firmware.

Run the fast EMQX command-publish response test:

```bash
npm run test:emqx-publish
```

It verifies that only HTTP 200 is classified as delivered and that HTTP 202
`no_matching_subscribers` cannot dispatch a delivery.

The Supabase/EMQX integration suite requires Node.js 22 or newer, npm, Docker,
and a running Docker daemon. CI uses the current Node.js 24 LTS line:

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
and ESP32 contract checks, frontend checks, the EMQX publish-response test, and
the local integration suite on every pull request and push to `main`.

Current audit status: the frontend suite, EMQX publish-response test, and Pi and
ESP32 host suites pass locally without production credentials (12 frontend, 27
Pi, and 4 ESP32 tests). The Docker-backed pgTAP/Edge Function suite has not yet
been run against the new `011` migration. Do not deploy the pending database
and Edge Function changes until `npm run test:integration` passes.

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
ROBOT_AGENT_VERSION=pi-agent-1.3.0
ROBOT_REQUIRE_TIME_SYNC=true
ROBOT_STATE_MAX_AGE_SECONDS=15
ROBOT_COMMAND_INBOX=/var/lib/miit-rover/command-inbox
ROBOT_COMMAND_ARCHIVE=/var/lib/miit-rover/command-archive
ROBOT_EVENT_OUTBOX=/var/lib/miit-rover/event-outbox
ROBOT_EVENT_ARCHIVE=/var/lib/miit-rover/event-archive
ROBOT_ACK_OUTBOX=/var/lib/miit-rover/ack-outbox
ROBOT_ACK_ARCHIVE=/var/lib/miit-rover/ack-archive
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

Command acknowledgements are persisted to `ack-outbox` before the inbound QoS-1
packet is acknowledged and move to `ack-archive` only after broker PUBACK.
Broker PUBACK is not proof that the EMQX HTTP action reached Supabase; reconcile
both event and ACK archives with the database before replaying an identical
record.

Run the Pi message-contract checks without connecting to hardware:

```bash
npm run test:pi
```

Use [DoneRaspberrypi.md](DoneRaspberrypi.md) as the verified completed-work
record for the Raspberry Pi bridge deployment.

### Live Pi audit and bridge update: 21 July 2026

An initial read-only audit found the pre-hardening bridge. A later
user-authorized maintenance pass deployed the exact tested EV-folder bridge
bundle:

- `pi-agent-1.3.0` is installed under the enabled systemd service. All 27 Pi
  tests passed locally, from the staged Pi bundle, and again from the installed
  root-owned source as the `rover` account.
- After restart, time synchronization completed, MQTT/TLS connected, the QoS-1
  command subscription was accepted, and the service remained active with zero
  observed restarts.
- The agent durably queued an `ESP32_DISCONNECTED` event, received broker
  acceptance, and moved it to the event archive. This verifies Pi-to-broker
  publishing, not the separate EMQX-to-Supabase HTTP action.
- Supabase did not receive those heartbeats: `robot-01.last_seen` remained at
  the old value during the pre-deployment audit. Database ingestion must be
  rechecked after the EMQX HTTP action/rule and pending cloud deployment are
  completed.
- Recent dispatch commands still failed before broker publication with HTTP
  403 from the EMQX Deployment API.
- A separate operator-reported Step 13.2 controlled test did reach the earlier
  Pi bridge: a protected valid `START_MISSION` request remains on the Pi and
  its command ID matches a durable processed-command row. This records a
  successful command handoff, not physical delivery execution.
- The robot MQTT identity was permitted to subscribe beyond its intended own
  topic scope. Change EMQX authorization to explicit per-robot allow rules
  followed by default deny.
- The deployment-start check found no ESP32 USB serial device. A later read-only
  check found the persistent link and `dialout` permissions present, but the
  running service has not recorded a successful post-start ESP32 handshake.
  Re-verify the active one-owner serial session before relying on the transport.
- No `robot_state.json`, mission-manager output, physical STOP confirmation, or
  autonomous-navigation evidence was available. No motion command, firmware
  flash, or physical safety test was attempted during the bridge update.
- The repository contains ESP32 v0.2 commissioning source, but it has not been
  compiled, flashed, or hardware-verified. A single final serial-port owner for
  navigation and safety traffic is also still unresolved.
- Chrony's boot wait is now enabled, source/config ownership is hardened, the
  secret environment file is root-only, and the broker CA remains readable by
  the service account.
- The bridge was deployed directly from uncommitted local files. Its Pi Git
  checkout is therefore intentionally dirty against the older commit; commit
  and push the matching EV-folder changes before reconciling or pulling that
  checkout.

## MQTT topics

```text
miit/robots/{robotId}/commands
miit/robots/{robotId}/acks
miit/robots/{robotId}/state
miit/robots/{robotId}/events
miit/robots/{robotId}/presence
```

Do not publish video frames or continuous motor-control messages through MQTT.

## Cloudflare Workers frontend deployment

The checked-in `wrangler.jsonc` deploys the static Vite output as Cloudflare
Worker assets with SPA fallback. It is not a Pages-project configuration.

1. Configure the three `VITE_*` build variables without committing their values.
2. Authenticate Wrangler for the intended Cloudflare account.
3. Run:

```bash
npm run deploy:cloudflare
```

The included `wrangler.jsonc` configures Cloudflare static assets to serve
`index.html` for React Router navigation requests.

## Remaining work

The remaining work is split by execution location:

- [Remainding.md](Remainding.md) — EV-folder source changes, local tests,
  Supabase, EMQX, GitHub, Cloudflare, and laptop-side acceptance.
- [RemaindingRaspberryPi.md](RemaindingRaspberryPi.md) — Pi deployment,
  ESP32 commissioning, navigation, physical safety, and supervised testing.

The two immediate application safeguards are:

1. Add server-enforced rate limiting and abuse protection to authenticated
   delivery creation and command calls.
2. Add operational alerting for failed or backlogged EMQX HTTP actions. Manual
   dashboard inspection and Pi archive reconciliation are useful diagnostics,
   but they are not automated alerting.

## Safety boundary

The public application sends only mission-level commands plus PAUSE, RESUME,
RETURN_HOME and ESTOP requests. A separate Raspberry Pi mission manager must
perform local navigation; the MQTT bridge does not implement navigation. It
also does not yet provide the local event producer required to prove safe
`RESUMED` and clear a cloud ESTOP/FAULT latch. The ESP32 performs wheel control
and must independently enforce its heartbeat timeout, hardware E-stop and
STOP-after-boot behavior. Internet loss must cause a safe local response, not
uncontrolled motion.
