# MIIT Rover Campus Delivery Project

## 1. Project summary

MIIT Rover is a full-stack minimum viable product for coordinating autonomous electric-vehicle deliveries across the MIIT campus. It combines:

- A responsive React and TypeScript web application.
- A safe browser-only demo mode.
- Supabase Authentication, PostgreSQL, Row Level Security, and Realtime support.
- A Supabase Edge Function that turns authorized web actions into auditable MQTT commands.
- An authenticated EMQX-to-Supabase ingestion Edge Function for acknowledgements, telemetry, events, and presence.
- An EMQX-compatible MQTT topic and command contract.
- A Raspberry Pi bridge that validates commands and forwards only constrained instructions to an ESP32.
- Production packaging for static or worker-backed web hosting.

The application deliberately separates mission management from vehicle control. A campus user can request a delivery, staff can approve and dispatch it, and operators can request safe high-level actions. The public web application never streams steering, throttle, or continuous wheel commands.

This repository is an MVP and integration foundation, not a complete autonomous navigation stack. A local ESP32 v0.2 raised-wheel commissioning firmware package now exists, but it has not been compiled, flashed, or hardware-verified and is not final unattended motor firmware. Local mapping, route planning, obstacle avoidance, closed-loop motor control, cargo-lock control, and verified physical E-stop behavior remain robot-side responsibilities.

## 2. Product goals

The project is designed to demonstrate and support the following operational flow:

1. A campus user signs in or enters demo mode.
2. The user chooses registered pickup and destination points.
3. The user supplies package, recipient, weight, priority, and handling information.
4. The request enters an approval queue.
5. An administrator or operator approves the request.
6. Staff assigns a suitable robot.
7. Staff dispatches a mission-level command.
8. The command is written to an audit table before publication.
9. EMQX delivers the command to the assigned Raspberry Pi.
10. The Pi validates schema, robot identity, expiration, and duplicate status.
11. The Pi writes one `command-inbox/{commandId}.json` handoff or sends the appropriate timed `STOP`/latching `ESTOP` safety frame to the ESP32.
12. The robot publishes an acknowledgement and retains local authority over physical motion.
13. EMQX Data Integration forwards acknowledgements, state, events, and presence to the authenticated ingestion function.
14. PostgreSQL atomically records events and advances delivery state from robot evidence.
15. Supabase Realtime refreshes the frontend with the resulting delivery and robot state.

The user-facing product also provides:

- Delivery history, filtering, search, and details.
- A visual delivery timeline.
- A schematic campus map with route and robot position.
- Robot battery, signal, speed, mode, and sensor health.
- PAUSE, RESUME, RETURN_HOME, and ESTOP requests.
- Role-specific navigation and views.
- Database-backed cloud notifications with persistent read state, plus local demo alerts and toast feedback.

## 3. System architecture

The high-level system path is:

```text
Browser
  |
  | HTTPS + Supabase user session
  v
React application
  |
  +---------------------> Supabase Auth
  |
  +---------------------> PostgreSQL + RLS + Realtime
  |
  +---------------------> dispatch-delivery Edge Function
                              |
                              | EMQX REST publish API
                              v
                            EMQX
                              |
                              | MQTT over TLS, QoS 1
                              v
                        Raspberry Pi bridge
                              |
                              +----> command-inbox/{commandId}.json
                              |
                              +----> ESP32 UART timed STOP / latching ESTOP
                              |
                              +----> MQTT acknowledgements, state,
                                     events, and retained presence
                                         |
                                         v
                                      EMQX rule
                                         |
                                         | HTTPS + shared ingest secret
                                         v
                              ingest-robot-message Edge Function
                                         |
                                         v
                              PostgreSQL + Supabase Realtime
```

The trust boundary is intentional:

- The browser is allowed to request missions and constrained operational commands.
- Supabase validates identity and data access.
- The Edge Function validates staff authorization and constructs the command envelope.
- EMQX transports commands but does not make motion decisions.
- The Raspberry Pi validates the command again and coordinates local navigation.
- The ESP32 owns low-level wheel output and must enforce independent stopping behavior.

## 4. Runtime modes

### 4.1 Demo mode

Demo mode is active when `VITE_CLOUD_MODE` is not exactly `true`, or when the required public Supabase values are unavailable.

Characteristics:

- No sign-in is required.
- The default role is `ADMIN`.
- The profile menu can switch between `USER`, `ADMIN`, and `OPERATOR`.
- Deliveries and robots are initialized from `src/data/demo.ts`.
- Delivery and robot changes persist in browser `localStorage`.
- Mission progress can be advanced manually through demo checkpoints.
- Robot commands update the local state immediately.
- Reset Demo restores the original sample data.

Local storage keys:

```text
miit-rover-deliveries-v1
miit-rover-robots-v1
```

Demo mode is useful for UI demonstrations and automated tests. It does not communicate with Supabase, EMQX, or a physical robot.

### 4.2 Cloud mode

Cloud mode is active when all of the following are true:

```text
VITE_CLOUD_MODE=true
VITE_SUPABASE_URL is configured
VITE_SUPABASE_PUBLISHABLE_KEY is configured
```

Cloud mode is deliberately disabled while Vitest runs so the deterministic demo workflow tests do not depend on a remote authentication service.

Characteristics:

- `CloudAuthGate` requires a Supabase session.
- Sign-up creates a Supabase Auth user with `full_name` metadata.
- A database trigger creates the matching `public.profiles` row.
- The current profile role controls the application role.
- New delivery requests snapshot the authenticated profile's trimmed full name and email.
- Deliveries and robots are read from PostgreSQL.
- Realtime subscriptions refresh the frontend after delivery or robot changes.
- RLS controls which records each authenticated user can read or modify.
- Dispatch and robot control requests invoke the Edge Function.
- Robot acknowledgements, telemetry, events, and presence return through the EMQX HTTP Server integration.
- Cloud delivery checkpoints are advanced by validated robot events, not operator checkpoint buttons.

## 5. Roles and authorization

The application defines three roles:

| Role | Primary responsibility | Visible application areas |
|---|---|---|
| `USER` | Request and track campus deliveries | Overview, New delivery, Deliveries |
| `ADMIN` | Manage users and delivery operations | All application areas |
| `OPERATOR` | Dispatch missions and monitor robots | Overview, Deliveries, Dispatch center, Robot fleet, System setup |

In demo mode, role switching is a presentation and workflow feature.

In cloud mode, the role is loaded from `public.profiles.role`. A new account receives `USER` by default. Staff access must be assigned in the database by an authorized administrator.

The frontend hides navigation items that are not relevant to the selected role. The database and Edge Function remain the real security enforcement layers:

- Users can read their own deliveries.
- Staff can read all deliveries.
- Users can create their own requests.
- Users can cancel only their own waiting requests.
- Staff can update deliveries.
- Only staff can read command and robot event audit data.
- The Edge Function independently rejects callers without `ADMIN` or `OPERATOR`.

## 6. Delivery lifecycle

The complete delivery status type is:

```text
REQUESTED
APPROVED
ASSIGNED
DISPATCHED
TO_SOURCE
AT_SOURCE
PACKAGE_LOADED
TO_DESTINATION
AT_DESTINATION
DELIVERED
RETURNING
COMPLETED
PAUSED
FAILED
CANCELLED
```

The normal mission path is:

```text
REQUESTED
   |
   v
APPROVED
   |
   v
ASSIGNED
   |
   v
DISPATCHED
   |
   v
TO_SOURCE
   |
   v
AT_SOURCE
   |
   v
PACKAGE_LOADED
   |
   v
TO_DESTINATION
   |
   v
AT_DESTINATION
   |
   v
DELIVERED
   |
   v
RETURNING
   |
   v
COMPLETED
```

`CANCELLED` and `FAILED` are terminal alternatives. `PAUSED` exists in the shared status model, while robot pause behavior is primarily represented through `Robot.mode`.

In demo mode, the Dispatch page advances the normal path with predefined progress and ETA values. In cloud mode, successful EMQX publication changes `ASSIGNED` to `DISPATCHED`. The rover must then acknowledge the command and publish `MISSION_STARTED` before the database changes the delivery to `TO_SOURCE`. Later checkpoints are applied only from robot events.

## 7. Frontend architecture

### 7.1 Application composition

`src/main.tsx` mounts the application in React Strict Mode.

`src/App.tsx` composes the runtime in this order:

```text
BrowserRouter
  CloudAuthGate
    AppProvider
      AppShell
        Routes
```

The ordering is important:

- Routing is available to the shell and pages.
- Cloud authentication blocks the protected application before state is loaded.
- `AppProvider` makes shared deliveries, robots, roles, notifications, and actions available.
- `AppShell` supplies navigation and global UI.
- Page components focus on specific workflows.

### 7.2 Routes

| Route | Component | Purpose |
|---|---|---|
| `/` | `Dashboard` | Operational summary, fleet readiness, current mission, and queue |
| `/new-delivery` | `NewDelivery` | Delivery request form and route preview |
| `/deliveries` | `Deliveries` | Searchable records and delivery detail drawer |
| `/dispatch` | `Dispatch` | Approval, robot assignment, dispatch, and checkpoint advancement |
| `/fleet` | `Fleet` | Robot telemetry, current assignment, and safe commands |
| `/settings` | `Settings` | Integration status, architecture, environment guidance, and demo reset |
| Any other path | `Navigate` | Redirects to `/` |

### 7.3 Shared state and operations

`src/context/AppContext.tsx` is the frontend orchestration layer. It owns:

- Current role.
- Delivery collection.
- Robot collection.
- Notifications.
- Toast messages.
- Demo persistence.
- Cloud refresh and Realtime subscription.
- Delivery actions.
- Robot command actions.

The public context operations are:

```text
createDelivery
approveDelivery
assignDelivery
dispatchDelivery
cancelDelivery
advanceDelivery
sendRobotCommand
markNotificationsRead
resetDemo
```

Cloud rows use snake_case database fields. `mapCloudDelivery`, `mapCloudRobot`, and `mapCloudNotification` convert them to the camelCase frontend models.

### 7.4 Authentication gate

`src/components/CloudAuthGate.tsx`:

- Bypasses authentication in demo mode.
- Loads the existing Supabase session in cloud mode.
- Subscribes to authentication state changes.
- Supports email/password sign-in.
- Supports account creation with a full name.
- Displays confirmation guidance when email confirmation prevents immediate session creation.
- Supports sign-out through the profile menu.

The application uses Supabase's persisted session and automatic token refresh behavior.

### 7.5 Application shell

`src/components/AppShell.tsx` provides:

- Desktop sidebar navigation.
- Mobile navigation drawer and overlay.
- Page titles and subtitles.
- Backend mode indicator.
- Notification popover.
- Demo role switcher.
- Cloud role display and sign-out.
- Global transient toast messages.
- Basic frontend route redirection when a `USER` opens a staff-only page.

### 7.6 Campus map

`src/components/CampusMap.tsx` renders an SVG schematic rather than a geographic map service.

It:

- Draws the predefined campus road network.
- Places registered campus locations using percentage-like `x` and `y` coordinates.
- Draws a direct visual line between source and destination.
- Estimates a robot marker position from mission status and progress.
- Shows source, destination, robot, mission, and battery indicators.

It does not currently consume GPS, SLAM, or map-server coordinates.

### 7.7 Delivery timeline and status styling

`DeliveryTimeline` maps detailed statuses onto a smaller set of user-facing milestones:

```text
Requested
Assigned
To Source
Package Loaded
To Destination
Delivered
Completed
```

`StatusPill` maps delivery, robot, sensor, and priority values to semantic color tones.

### 7.8 Page behavior

#### Dashboard

The dashboard derives:

- Active mission count.
- Primary active mission.
- Available robot count.
- Completed delivery count.
- Dispatch queue.

It displays the main campus map, mission metadata, robot health, safety summary, and role-specific primary action.

#### New Delivery

The request form captures:

- Pickup point.
- Destination point.
- Item description.
- Category.
- Weight.
- Priority.
- Optional handling notes.
- Recipient name.
- Recipient phone.
- Package safety confirmation.

Client validation enforces:

- Different source and destination.
- Item and recipient names.
- Payload greater than zero and no more than 10 kg.
- Explicit package safety confirmation.

The sidebar shows a route preview and summary before submission.

#### Deliveries

The records page provides:

- Status filters.
- Search by tracking code, item, or recipient.
- Responsive table output.
- Detail drawer.
- Route and package metadata.
- Timeline.
- Optional cargo unlock code.
- Cancellation for waiting requests.

#### Dispatch

The dispatch center:

- Shows non-terminal deliveries.
- Selects a delivery.
- Approves `REQUESTED` deliveries.
- Assigns a robot to `APPROVED` deliveries.
- Dispatches `ASSIGNED` deliveries.
- Advances later checkpoints only in demo mode.
- Shows a waiting state in cloud mode after a command is `DISPATCHED`.
- Allows rejection/cancellation before dispatch.
- Displays package, recipient, route, robot, and safety details.

#### Fleet

The fleet page provides:

- Robot cards.
- Battery, signal, and speed summaries.
- Live schematic position.
- LiDAR, camera, ESP32, temperature, signal, and velocity indicators.
- Current delivery assignment.
- PAUSE or RESUME.
- RETURN_HOME.
- ESTOP with a separate confirmation modal.

The UI explicitly states that continuous web motor control is disabled.

#### Settings

The settings page explains:

- Cloud connection status.
- System architecture.
- Required public and server-only environment variables.
- Integration checklist.
- Demo reset.

This page reports configuration state; it is not a secret-management interface.

## 8. Frontend data models

The primary TypeScript models live in `src/types.ts`.

### 8.1 Delivery

Important fields include:

- Internal ID.
- Human-facing tracking code.
- Requester identity.
- Recipient identity.
- Source and destination IDs.
- Item name and category.
- Weight and priority.
- Status and assigned robot.
- Creation and update timestamps.
- ETA and progress.
- Notes.
- Optional unlock code.

### 8.2 Robot

Important fields include:

- Stable robot ID and display name.
- Model.
- Operational status and control mode.
- Battery and signal percentages.
- Last known location.
- Current delivery.
- Speed.
- LiDAR, camera, and ESP32 health.
- Motor temperature.

### 8.3 Campus location

Locations contain:

- Stable ID and short code.
- Full and short display names.
- Description.
- Schematic map coordinates.
- Physical marker ID.
- Location type.

## 9. Demo data

`src/data/demo.ts` contains:

- Seven campus locations.
- Five representative deliveries across requested, assigned, active, and completed states.
- Three robots with different readiness states.
- Three notifications used only when the application is running in demo mode.
- Status ordering and formatting helpers.

The locations are:

```text
Robot Station
Faculty of Computer Science
Faculty of Computer Systems & Technologies
MIIT Library
Data Center
Rector Office
Campus Canteen
```

The same stable location IDs are seeded into PostgreSQL by the initial migration, keeping demo and cloud route references compatible.

## 10. Browser compatibility

`src/lib/id.ts` creates client-side identifiers using three levels:

1. Native `crypto.randomUUID()` when supported.
2. A standards-compatible UUID built from `crypto.getRandomValues()`.
3. A timestamp and random local identifier when Web Crypto is unavailable.

This prevents delivery submission from failing on older mobile browsers or restricted webviews that expose `crypto` without `randomUUID`.

These client-generated IDs are used for demo deliveries, notifications, and toast messages. PostgreSQL generates authoritative cloud delivery IDs.

## 11. Supabase database

The initial schema is defined in:

```text
supabase/migrations/202607160001_initial_schema.sql
```

Server-generated tracking codes are added by:

```text
supabase/migrations/202607160002_server_tracking_codes.sql
```

Later cloud workflow and robot-ingestion changes are added by:

```text
supabase/migrations/202607170003_delivery_dispatched_status.sql
supabase/migrations/202607170004_robot_ingestion.sql
supabase/migrations/202607190005_expire_stale_robot_commands.sql
supabase/migrations/202607190006_authenticated_delivery_requester.sql
supabase/migrations/202607200007_database_notifications.sql
supabase/migrations/202607200008_require_dispatched_mission_start.sql
supabase/migrations/202607200009_serialize_mission_start.sql
supabase/migrations/202607210010_robot_connectivity_and_event_order.sql
supabase/migrations/202607210011_robot_ingestion_safety_followup.sql
```

### 11.1 Extensions and enum types

The schema enables `pgcrypto` for UUID generation.

Enum types:

- `app_role`
- `delivery_status`
- `robot_status`
- `robot_mode`

### 11.2 `profiles`

Purpose:

- Application identity record corresponding to `auth.users`.
- Stores full name, email, and application role.

Behavior:

- `handle_new_user()` inserts a profile after an Auth user is created.
- New users receive `USER`.
- Deleting an Auth user cascades to the profile.
- Delivery creation reloads the caller's profile at submission time and rejects a missing or incomplete profile.
- Authenticated users may edit only their own full name and email; role changes require a trusted administrative context.

### 11.3 `locations`

Purpose:

- Registry of valid pickup, destination, home, and service points.

Notable fields:

- Stable text ID.
- Unique location code.
- Map version.
- Coordinates and yaw.
- Optional unique physical marker ID.
- Active flag.

### 11.4 `robots`

Purpose:

- Current fleet state.

Notable constraints:

- Battery must be between 0 and 100.
- Signal must be between 0 and 100.
- Sensor states are limited to `OK`, `WARNING`, or `OFFLINE`.
- `current_delivery_id` references a delivery and is cleared if that delivery is deleted.
- `telemetry_at` stores the robot-provided observation time and rejects an older
  state snapshot after a newer snapshot has already been applied.
- `telemetry_received_at` stores trusted server receipt time and determines
  operational freshness; a robot clock that is ahead cannot keep telemetry
  fresh after state publication stops.
- `firmware_version` records the active Pi agent version reported by the robot.

### 11.5 `deliveries`

Purpose:

- Delivery request and mission lifecycle record.

Important constraints:

- Tracking codes are unique.
- Source and destination must be different.
- Weight must be greater than zero and no more than 10 kg.
- Priority must be `NORMAL`, `HIGH`, or `URGENT`.
- Progress must remain between 0 and 100.
- Source, destination, requester, approver, and robot references are relationally constrained.
- A before-insert trigger overwrites requester ID, name, and email from the authenticated profile so browser-supplied labels cannot be spoofed.

The table also reserves fields for:

- Hashed cargo unlock code.
- Approval actor and timestamp.
- Dispatch timestamp.
- Completion timestamp.

### 11.6 `robot_commands`

Purpose:

- Durable command audit log.

It records:

- Command UUID.
- Robot and optional delivery.
- Command type.
- Monotonic identity sequence number.
- Full JSON command envelope.
- Lifecycle status.
- Issuing staff user.
- Issue, expiration, publication, and acknowledgement timestamps.
- Result metadata.
- Automatic expiration of overdue `PENDING` and `PUBLISHED` records.
- A `COMMAND_EXPIRED` warning event for every automatically expired command.

Allowed command record states:

```text
PENDING
PUBLISH_UNKNOWN
PUBLISHED
ACKNOWLEDGED
REJECTED
COMPLETED
FAILED
EXPIRED
```

`PUBLISH_UNKNOWN` is a conservative reconciliation barrier used before the
external EMQX call. It is retained after a timeout or broker/proxy 5xx because
the broker may already have accepted the command; staff or later robot evidence
must resolve it before a different command ID is issued.

### 11.7 `robot_events`

Purpose:

- Durable robot and mission event history.
- Idempotent QoS 1 processing through a unique `message_id`.
- Optional linkage to the cloud command that caused the event.

Severity values:

```text
INFO
WARNING
ERROR
CRITICAL
```

Migration `202607200008_require_dispatched_mission_start.sql` adds a database
guard for the first mission checkpoint. A new `MISSION_STARTED` event must
reference a delivery that is already `DISPATCHED` and assigned to the same
robot. Replaying an existing MQTT `message_id` remains idempotent, while a new
event for an `ASSIGNED` or otherwise invalid delivery is rejected before the
event row can advance the workflow.

Migration `202607200009_serialize_mission_start.sql` locks the assigned delivery
row while that checkpoint is validated. This ensures that two different
mission-start events received concurrently cannot both observe `DISPATCHED`
and advance the same delivery. An advisory transaction lock preserves
idempotent behavior for simultaneous retries with the same MQTT event ID.

Migration `202607210010_robot_connectivity_and_event_order.sql` separates MQTT
bridge connectivity (`bridge_online`, `bridge_last_seen`) from operational
telemetry (`last_seen`, `telemetry_at`). Presence alone cannot keep stale
BUSY/speed/sensor values alive. The migration also serializes robot control
events and rejects an older PAUSED/RESUMED/ESTOP or terminal mission event when
a newer control state is already recorded.

Migration `202607210011_robot_ingestion_safety_followup.sql` is an append-only
hardening layer because `010` was already published in Git. It orders bridge
state by the EMQX broker timestamp, records trusted telemetry receipt time,
tracks the actual safety-latch time, and serializes mission and ordinary control
command reservations through robot/delivery row locks and partial unique
indexes. It adds `PUBLISH_UNKNOWN` for ambiguous external publish outcomes and
`finalize_robot_command_publish()` so command publication and delivery dispatch
are finalized in one database transaction even when robot ACK/event evidence
races the HTTP response. It blocks mission progress while ESTOP/FAULT is
latched and permits reset only through a single-use `RESUME` command issued by
an ADMIN/OPERATOR after that latch, followed by fresh safe telemetry and a
linked `RESUMED` event carrying local-safety confirmation. It accepts only an
identical event retry under an existing event ID, rejects conflicting event
content and terminal ACK transitions, requires mission events to reference a
valid time-ordered START_MISSION command, and prevents more than one active
mission command per robot or delivery.

Migration `011` also narrows the earlier `DISPATCHED`-only rule: an
`ASSIGNED` delivery may accept `MISSION_STARTED` only when the event is linked
to its valid reserved `START_MISSION` command. This covers the race in which the
robot proves receipt before the Edge Function receives or finalizes the EMQX
HTTP response; every unrelated `ASSIGNED` event remains rejected.

### 11.8 `notifications`

Purpose:

- Persistent, per-user in-app notifications.
- Personal delivery updates for the requester.
- Staff operational alerts for administrators and robot operators.
- Durable unread state through `read_at`.

Delivery insert and status-change triggers create notifications automatically. Selected robot safety and fault events, including offline robots, expired commands, E-stop activation, low battery, ESP32 disconnects, and bridge faults, fan out to current staff. Each recipient and source event has a unique `event_key`, so retries cannot create duplicate alerts.

The browser can read only its own visible notifications. It cannot insert, edit, or delete notification content. The `mark_notifications_read()` RPC marks only the authenticated caller's visible alerts. Staff alerts remain staff-only after a user is demoted.

Notifications are generated only for events that happen after this migration is applied; historical deliveries and robot events are intentionally not backfilled as new unread alerts.

### 11.9 Triggers and indexes

The schema includes:

- Automatic `updated_at` triggers for deliveries and robots.
- Automatic notification triggers for delivery changes and selected robot events.
- Indexes for requester history, status queues, assigned robot lookups, pending commands, delivery events, notification history, and unread notifications.
- Realtime publication for `deliveries`, `robots`, and `notifications`.

### 11.10 Server-generated tracking codes

The second migration creates `delivery_tracking_sequence`.

It:

- Starts from at least 1050.
- Preserves a higher existing numeric tracking suffix.
- Sets the delivery column default to `MIIT-` plus a zero-padded sequence value.

Cloud clients no longer generate authoritative tracking codes. This prevents two users, whose RLS views may contain different deliveries, from independently choosing the same number.

## 12. Row Level Security

All application tables have RLS enabled.

| Table | Read policy | Write policy |
|---|---|---|
| `profiles` | Own profile or staff | User can update only own full name and email |
| `locations` | Authenticated users can read active locations | Admin can manage |
| `robots` | Authenticated users can read | Admin or operator can update |
| `deliveries` | Requester reads own; staff reads all | User creates/cancels own; staff updates |
| `robot_commands` | Staff only | Created and updated through trusted service context |
| `robot_events` | Staff only | Intended for trusted robot/backend ingestion |
| `notifications` | Recipient only; staff alerts additionally require a current staff role | Content is trigger-managed; caller can mark visible rows read only through an RPC |

`current_user_role()` is a stable security-definer function used by policies to resolve the caller's role.

RLS is essential because the frontend publishable key is intentionally present in the browser bundle. Service-role and EMQX credentials must never be placed in `VITE_*` variables.

## 13. Edge Function

The command and ingestion functions live at:

```text
supabase/functions/dispatch-delivery/index.ts
supabase/functions/dispatch-delivery/publish-response.js
supabase/functions/ingest-robot-message/index.ts
```

The Edge Functions run in the Supabase Deno environment. The small publish
response classifier is dependency-free so its EMQX status semantics can also
be exercised directly by Node in CI.

### 13.1 Request processing

The function:

1. Handles CORS preflight.
2. Accepts only POST requests.
3. Requires an Authorization header.
4. Validates the Supabase user session.
5. Loads the caller's application role.
6. Requires `ADMIN` or `OPERATOR`.
7. Accepts either a delivery dispatch or a robot command.
8. Validates command type plus fresh bridge/telemetry, idle state, battery, and sensor readiness before mission dispatch.
9. Creates an expiring command envelope.
10. Inserts a `PUBLISH_UNKNOWN` command audit row before making the external
    call, reserving the robot/delivery against concurrent mission or control
    commands.
11. Calls the EMQX v5 publish REST API with a ten-second timeout.
12. Treats only HTTP 200 as delivered. HTTP 202 with
    `no_matching_subscribers` is a known non-delivery:
    the command becomes `FAILED` and the delivery remains `ASSIGNED`.
13. Changes an unambiguous broker 4xx rejection to `FAILED`, but retains
    `PUBLISH_UNKNOWN` after a timeout, 5xx, or any other unrecognized response
    because publication may already have happened.
14. Reconciles ACK/event evidence that races the HTTP response and calls
    `finalize_robot_command_publish()` only after accepted publication.
15. Atomically finalizes the command status/publication time and changes an
    assigned delivery to `DISPATCHED`; physical movement state is not changed
    until a robot event arrives.

### 13.2 Ingestion processing

`ingest-robot-message`:

1. Accepts only POST requests.
2. Requires the `x-emqx-secret` shared secret.
3. Validates the complete EMQX envelope, including `mqttMessageId`, topic, MQTT
   username, MQTT client ID, QoS, broker timestamp, payload `robotId`, and
   schema version.
4. Accepts only the acknowledgement, state, event, and presence topic suffixes.
5. Enforces exact topic-specific payload fields, strict timezone-bearing timestamps, UUIDs, enums, numeric ranges, sensor states, and a streamed 64 KiB request limit.
6. Calls `apply_robot_ack` so valid ACK state transitions are serialized,
   including ACK/event evidence received while publication is still
   `PUBLISH_UNKNOWN`; repeated current status is a no-op and conflicting
   terminal transitions are rejected.
7. Calls `apply_robot_state_observed` so device telemetry is ordered by its observation time and bridge connectivity by the EMQX broker timestamp.
8. Calls `apply_robot_event` so event insertion, command validation, safety-latch checks, and delivery transitions are atomic.
9. Treats only an identical repeated event ID as an idempotent QoS-1 duplicate; the same ID with changed content is rejected.
10. Updates `last_seen` only with accepted fresh state telemetry. ACKs, events, and presence cannot keep operational telemetry alive.
11. Uses broker-observed time for retained presence/Last Will ordering and rejects stale online presence.
12. Rejects a topic/payload identity mismatch before using the service role.

### 13.3 Supported command requests

Start an assigned delivery:

```json
{
  "deliveryId": "delivery-uuid"
}
```

Send an operational command:

```json
{
  "robotId": "robot-01",
  "command": "PAUSE"
}
```

Allowed direct robot commands:

```text
PAUSE
RESUME
RETURN_HOME
ESTOP
```

### 13.4 Command expiration

- ESTOP requests expire after 60 seconds.
- Other commands expire after five minutes.

Expiration limits stale command execution. The Pi validates `expiresAt` again before acting.

### 13.5 Required Edge Function environment

Supabase provides:

```text
SUPABASE_URL
SUPABASE_SERVICE_ROLE_KEY
```

The function also expects:

```text
SUPABASE_ANON_KEY
EMQX_API_URL
EMQX_API_KEY
EMQX_API_SECRET
ROBOT_INGEST_SECRET
```

The EMQX account should be limited to the publish API and the robot command topic namespace.
`ROBOT_INGEST_SECRET` is server-only and must also be configured as the EMQX HTTP action header. It must never be exposed through a `VITE_*` variable.

## 14. MQTT contract

### 14.1 Topic namespace

```text
miit/robots/{robotId}/commands
miit/robots/{robotId}/acks
miit/robots/{robotId}/state
miit/robots/{robotId}/events
miit/robots/{robotId}/presence
```

Recommended per-robot ACL:

- Subscribe only to its own command topic.
- Publish only to its own acknowledgement, state, event, and presence topics.

### 14.2 Command envelope

Representative structure:

```json
{
  "schemaVersion": 1,
  "commandId": "uuid",
  "robotId": "robot-01",
  "command": "START_MISSION",
  "payload": {
    "sourceLocationId": "loc-fcs",
    "destinationLocationId": "loc-data",
    "mapVersion": "miit-campus-v1",
    "deliveryId": "delivery-uuid"
  },
  "issuedAt": "ISO-8601 timestamp",
  "expiresAt": "ISO-8601 timestamp"
}
```

### 14.3 Acknowledgement envelope

```json
{
  "schemaVersion": 1,
  "commandId": "uuid",
  "robotId": "robot-01",
  "status": "ACKNOWLEDGED",
  "reason": "",
  "at": "ISO-8601 timestamp"
}
```

MQTT uses QoS 1 for command and acknowledgement publication. Duplicate delivery is therefore possible and is handled through command IDs and persistent idempotency state.

The system must not transport video frames or continuous motor commands over these topics.

### 14.4 State envelope

```json
{
  "schemaVersion": 1,
  "robotId": "robot-01",
  "at": "ISO-8601 timestamp",
  "status": "BUSY",
  "mode": "AUTO",
  "battery": 82,
  "signal": 91,
  "speedMps": 0.45,
  "locationId": "loc-fcs",
  "currentDeliveryId": "delivery-uuid",
  "lidar": "OK",
  "camera": "OK",
  "esp32": "OK",
  "motorTempC": 37.2,
  "firmwareVersion": "pi-agent-1.3.0"
}
```

The Pi publishes this snapshot only when the local mission manager has written a complete `robot_state.json`. State is intended for operator visibility at a low rate, normally every five seconds.

### 14.5 Event envelope

```json
{
  "schemaVersion": 1,
  "eventId": "uuid",
  "robotId": "robot-01",
  "deliveryId": "delivery-uuid",
  "commandId": "command-uuid",
  "type": "MISSION_STARTED",
  "severity": "INFO",
  "at": "ISO-8601 timestamp",
  "payload": {}
}
```

Supported event types:

```text
MISSION_STARTED
ARRIVED_SOURCE
PACKAGE_LOADED
DEPARTED_SOURCE
ARRIVED_DESTINATION
PACKAGE_RELEASED
RETURNING_HOME
MISSION_COMPLETED
MISSION_FAILED
PAUSED
RESUMED
ESTOP_TRIGGERED
OBSTACLE_DETECTED
LOW_BATTERY
ESP32_DISCONNECTED
BRIDGE_FAULT
```

The local mission manager must reuse the same `eventId` when retrying an event. The database unique index makes duplicate QoS 1 delivery safe.

### 14.6 Presence envelope

```json
{
  "schemaVersion": 1,
  "robotId": "robot-01",
  "online": true,
  "at": "ISO-8601 timestamp",
  "firmwareVersion": "pi-agent-1.3.0"
}
```

Presence is retained. The Pi publishes online presence after connection and
every 15 seconds. Its MQTT Last Will publishes the same schema with
`online: false`. Online presence updates connectivity time and firmware only;
it does not claim operational readiness. Only a fresh validated state snapshot
may restore ordinary ONLINE/BUSY telemetry. `ESTOP`/`FAULT` is stricter: state
snapshots and mission progress cannot clear it; the robot must emit a newer
`RESUMED` event tied to one unconsumed, post-latch staff `RESUME` command.

## 15. Raspberry Pi bridge

The bridge lives at:

```text
robot-pi/agent.py
```

Dependencies:

```text
paho-mqtt
pyserial
```

### 15.1 Startup

At startup the process:

- Reads robot, broker, serial, and state configuration from environment variables.
- Fails closed until `timedatectl` reports a synchronized clock.
- Creates command, event, and acknowledgement inbox/outbox/archive directories and recovers complete interrupted atomic writes.
- Opens a SQLite database for processed command IDs.
- Opens the ESP32 serial port when available and reports an event if the link is unavailable.
- Configures MQTT username/password and TLS certificate validation.
- Configures a retained offline Last Will message.
- Connects with a stable MQTT 3.1.1 persistent session so broker-queued QoS 1
  commands survive a temporary Pi disconnect.
- Subscribes to the robot's command topic and verifies the SUBACK.
- Publishes retained online presence only after that subscription is accepted.
- Starts a guarded background presence, state, ACK-outbox, and event-outbox publisher.

### 15.2 Validation

For each command, the bridge verifies:

- JSON is parseable.
- The packet arrived on the exact command topic with QoS 1 and is not retained.
- The encoded packet is no larger than 32 KiB and has only the command-specific fields.
- `commandId` is a UUID.
- `schemaVersion` is 1.
- The envelope robot ID matches the configured robot.
- The issued/expiry interval is positive and within the command-specific maximum TTL.
- A matching processed command ID reuses its original durable outcome even after expiry; the same ID with different content is treated as a security fault.
- The command type is supported.

Malformed commands cause a best-effort STOP, a rejected acknowledgement when a trustworthy command ID is available, and a `BRIDGE_FAULT` event. The bridge writes the result/ACK durably before manually acknowledging an inbound QoS-1 packet; if durable persistence fails, broker redelivery remains enabled.

### 15.3 Idempotency

Processed command IDs are stored in:

```text
${ROBOT_STATE_DIR}/commands.db
```

When an identical duplicate command arrives, the Pi does not execute it again.
It reuses the original result and outcome timestamp. Reusing a command ID with
changed content never creates a contradictory REJECTED ACK; it records a fault
and retains the original result.

### 15.4 Local mission handoff

Every accepted command writes a separate file under:

```text
${ROBOT_STATE_DIR}/command-inbox/{commandId}.json
```

A separate local navigation or mission-management process consumes the files
in `requestedAt` order, gives ESTOP/PAUSE immediate safety priority, and moves a
request to `${ROBOT_STATE_DIR}/command-archive/` only after durably recording
its result. One-file-per-command prevents a later PAUSE or mission request from
overwriting an unconsumed command.

Each file is written, fsynced, and atomically renamed. It contains:

```json
{
  "type": "START_MISSION",
  "commandId": "uuid",
  "requestedAt": "ISO-8601 timestamp",
  "sourceLocationId": "loc-fcs",
  "destinationLocationId": "loc-data",
  "mapVersion": "miit-campus-v1",
  "deliveryId": "delivery-uuid"
}
```

`PAUSE`, `RESUME`, `RETURN_HOME`, and `ESTOP` use the same inbox. Receiving
`RESUME` does not itself restart motion; the mission manager must check local
safety state and publish `RESUMED` only after accepting the request.

### 15.5 ESP32 handoff

`PAUSE` sends a short-lived, non-latching JSON-line STOP frame over UART:

```json
{
  "v": 1,
  "cmd": "STOP",
  "ttlMs": 300
}
```

`ESTOP` takes physical priority over disk-backed audit work and sends the
firmware's distinct latching frame without a TTL:

```json
{
  "v": 1,
  "cmd": "ESTOP"
}
```

The bridge waits for `ESP32_READY_DELAY_SECONDS` after opening a serial device
that may reset on open. A successful OS write is still not a physical motor
acknowledgement. The bridge does not publish `PAUSED`; the mission manager must
do that only after locally confirming the stopped state. `ESTOP_TRIGGERED`
immediately fails the cloud state safe and includes
`physicalConfirmation: false` until the ESP32 protocol provides a verified
acknowledgement. The v0.2 commissioning firmware cannot clear this latch over
UART: a nearby operator must remove hazards, release hard-stop inputs, perform
the local ESP32 reset, and verify that clearing leaves motion disarmed before a
cloud `RESUME` is requested.

The ESP32 must still enforce:

- Command framing and integrity.
- Heartbeat timeout.
- STOP after boot.
- Hardware E-stop.
- Local fault behavior.
- Safe motor output.

### 15.6 State, event, and acknowledgement handoff

The mission manager writes a complete state snapshot atomically to:

```text
${ROBOT_STATE_DIR}/robot_state.json
```

Example:

```json
{
  "status": "BUSY",
  "mode": "AUTO",
  "battery": 82,
  "signal": 91,
  "speedMps": 0.45,
  "locationId": "loc-fcs",
  "currentDeliveryId": "delivery-uuid",
  "lidar": "OK",
  "camera": "OK",
  "esp32": "OK",
  "motorTempC": 37.2,
  "at": "2026-07-21T02:30:00+00:00"
}
```

`at` is the time at which the mission manager observed the snapshot, not the
time the MQTT bridge happens to publish it. The bridge preserves this value,
rejects timestamps more than five minutes in the future, and skips snapshots
older than `ROBOT_STATE_MAX_AGE_SECONDS`. This prevents a stopped mission
manager from making stale telemetry appear current.

The mission manager writes each important mission event as a separate JSON file into:

```text
${ROBOT_STATE_DIR}/event-outbox/
```

Mission events require both `deliveryId` and the originating `commandId`. When
several files accumulated while offline, the bridge publishes them by `at`
occurrence time (with filesystem write time as the tie-breaker), not by random
UUID filename. Robot state and event MQTT payloads are limited to 32 KiB so the
complete EMQX webhook request remains below the ingestion endpoint's 64 KiB
limit.

It must write to a temporary filename and rename the completed file to `*.json`.
The agent adds an `eventId` and timestamp if they are absent, validates UUIDs,
timestamps, event type, severity, mission linkage, and the detail object, then
persists the normalized file before publishing. After the MQTT QoS 1 broker
acknowledgement, it moves the file to `event-archive` instead of deleting it.
Broker acknowledgement does not prove that the EMQX HTTP action reached
Supabase; the archive permits reconciliation and, when still valid for the
current lifecycle order, replay with the same identical `eventId`. An older
control/lifecycle event is intentionally rejected after a newer
`control_event_at`; do not bypass that safety check. Invalid files are renamed
to `*.bad`.

The bridge similarly writes deterministic command/status ACK files under
`ack-outbox` before acknowledging the inbound command. It publishes ACK files
in outcome-time order and moves them to `ack-archive` only after broker PUBACK.
The original timestamp is retained across crash recovery and expired QoS
duplicates. As with events, archive status must be reconciled with Supabase;
broker PUBACK is not an application-level receipt.

After repairing and positively testing the EMQX HTTP action, replay an archived
event without editing its IDs or timestamps:

```bash
cd /var/lib/miit-rover
sudo -u rover install -m 0600 event-archive/EVENT_ID.json event-outbox/EVENT_ID.tmp
sudo -u rover mv event-outbox/EVENT_ID.tmp event-outbox/EVENT_ID.json
```

Copy all related lifecycle events before replaying a backlog; the agent sorts
them by occurrence time. Verify Supabase accepted each event before deleting
any archive. Define disk alerts and an evidence-retention period for
`event-archive`, `ack-archive`, `command-archive`, and `commands.db`; the bridge deliberately
does not delete the only local recovery evidence automatically.

### 15.7 Pi environment variables

Required:

```text
MQTT_HOST
MQTT_USERNAME
MQTT_PASSWORD
```

Optional with defaults:

```text
ROBOT_ID=robot-01
MQTT_PORT=8883
ESP32_SERIAL_PORT=/dev/ttyUSB0
ESP32_READY_DELAY_SECONDS=2
ROBOT_STATE_DIR=/var/lib/miit-rover
MQTT_CA_FILE=/path/to/readable/ca.pem
ROBOT_AGENT_VERSION=pi-agent-1.3.0
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
ROBOT_ACK_OUTBOX=/var/lib/miit-rover/ack-outbox
ROBOT_ACK_ARCHIVE=/var/lib/miit-rover/ack-archive
ROBOT_LOG_LEVEL=INFO
```

Omit `MQTT_CA_FILE` entirely to use the operating-system trust store; do not
set it to a blank value in copied deployment templates.
Replace the development serial default with the persistent
`/dev/serial/by-id/...` path in every production environment.

Production values should be stored in a root-owned systemd environment file, not committed to the repository.
`MQTT_USERNAME` must equal `ROBOT_ID`; the ingestion endpoint uses this equality as one of its robot-identity checks.

## 16. Safety model

The repository follows several safety principles:

- Web commands are high-level and finite.
- Every cloud command has a UUID and expiration.
- Commands are written to an audit table before MQTT publication.
- Only authenticated staff can invoke the command function.
- The Pi validates the command independently.
- The Pi persists idempotency state.
- `PAUSE` forwards a short-lived, non-latching `STOP`; `ESTOP` forwards the
  separate latching `ESTOP` frame.
- Continuous motor control is absent from the public web application.
- TLS is required between Pi and MQTT broker.
- The ESP32 is expected to fail safe independently.

Important principle:

> Internet connectivity may authorize or coordinate a mission, but loss of Internet must never cause uncontrolled motion.

The MVP does not replace a formal hazard analysis, safety case, hardware validation program, or supervised campus trial.

## 17. Repository tree

Generated directories such as `.git/`, `node_modules/`, `dist/`, `supabase/.temp/`, and Python `__pycache__/` are omitted from the maintained-source tree below.

```text
EV/
├── .github/
│   └── workflows/
│       └── ci.yml
├── .openai/
│   └── hosting.json
├── public/
│   └── robot-mark.svg
├── MIIT_Rover_ESP32_Firmware_v0.2.0/
│   └── robot-esp32/
│       ├── MIIT_Rover_ESP32/
│       │   ├── MIIT_Rover_ESP32.ino
│       │   └── config.h
│       ├── tests/test_protocol.py
│       ├── tools/pi_serial_test.py
│       ├── CHANGELOG.md
│       └── README.md
├── robot-pi/
│   ├── agent.py
│   ├── local_store.py
│   ├── message_contract.py
│   ├── miit-rover-agent.service
│   ├── requirements.txt
│   ├── robot.env.example
│   ├── test_agent.py
│   ├── test_local_store.py
│   └── test_message_contract.py
├── scripts/
│   ├── prepare-hosting.mjs
│   └── run-supabase-integration.mjs
├── src/
│   ├── components/
│   │   ├── AppShell.tsx
│   │   ├── CampusMap.tsx
│   │   ├── CloudAuthGate.tsx
│   │   ├── DeliveryTimeline.tsx
│   │   └── StatusPill.tsx
│   ├── context/
│   │   └── AppContext.tsx
│   ├── data/
│   │   └── demo.ts
│   ├── lib/
│   │   ├── id.ts
│   │   └── supabase.ts
│   ├── pages/
│   │   ├── Dashboard.tsx
│   │   ├── Deliveries.tsx
│   │   ├── Dispatch.tsx
│   │   ├── Fleet.tsx
│   │   ├── NewDelivery.tsx
│   │   └── Settings.tsx
│   ├── test/
│   │   ├── app.test.tsx
│   │   ├── id.test.ts
│   │   └── setup.ts
│   ├── App.tsx
│   ├── main.tsx
│   ├── styles.css
│   └── types.ts
├── supabase/
│   ├── config.toml
│   ├── functions/
│   │   ├── dispatch-delivery/
│   │   │   ├── index.ts
│   │   │   └── publish-response.js
│   │   └── ingest-robot-message/
│   │       └── index.ts
│   ├── migrations/
│   │   ├── 202607160001_initial_schema.sql
│   │   ├── 202607160002_server_tracking_codes.sql
│   │   ├── 202607170003_delivery_dispatched_status.sql
│   │   ├── 202607170004_robot_ingestion.sql
│   │   ├── 202607190005_expire_stale_robot_commands.sql
│   │   ├── 202607190006_authenticated_delivery_requester.sql
│   │   ├── 202607200007_database_notifications.sql
│   │   ├── 202607200008_require_dispatched_mission_start.sql
│   │   ├── 202607200009_serialize_mission_start.sql
│   │   ├── 202607210010_robot_connectivity_and_event_order.sql
│   │   └── 202607210011_robot_ingestion_safety_followup.sql
│   └── tests/
│       ├── database/
│       │   ├── 001_schema_security.sql
│       │   ├── 002_notifications.sql
│       │   ├── 003_robot_state.sql
│       │   ├── 004_robot_events.sql
│       │   ├── 005_robot_maintenance.sql
│       │   ├── 006_robot_presence_order.sql
│       │   └── 007_command_publish.sql
│       ├── integration/
│       │   ├── emqx-publish-response.test.mjs
│       │   └── ingest-robot-message.test.mjs
│       └── robot-ingest.test.env
├── .env.example
├── .gitignore
├── DoneRaspberrypi.md
├── MIIT_Rover_Raspberry_Pi_Ubuntu_Setup_Guide.md
├── Remainding.md
├── RemaindingRaspberryPi.md
├── index.html
├── package-lock.json
├── package.json
├── project.md
├── README.md
├── tsconfig.app.json
├── tsconfig.json
├── tsconfig.node.json
├── vite.config.ts
├── vitest.config.ts
└── wrangler.jsonc
```

## 18. File-by-file responsibility

### Root

| File | Responsibility |
|---|---|
| `README.md` | Quick start, cloud connection, Edge Function, Pi, MQTT, hosting, and safety overview |
| `project.md` | Detailed architecture and repository documentation |
| `package.json` | Scripts and JavaScript dependencies |
| `package-lock.json` | Reproducible npm dependency resolution |
| `index.html` | Vite HTML entry document |
| `.env.example` | Safe frontend environment template |
| `.gitignore` | Excludes dependencies, builds, local secrets, Supabase temporary metadata, and generated files |
| `.github/workflows/ci.yml` | Runs Pi message-contract, frontend, and isolated Supabase/EMQX integration checks |
| `vite.config.ts` | React plugin and port 4173 development/preview server |
| `vitest.config.ts` | jsdom test environment and shared setup |
| `tsconfig*.json` | TypeScript project, browser, and Node configuration |
| `scripts/run-supabase-integration.mjs` | Starts and verifies the local integration environment without cloud credentials |

### `src/components`

| File | Responsibility |
|---|---|
| `AppShell.tsx` | Navigation, responsive shell, role menu, notifications, and toasts |
| `CampusMap.tsx` | SVG campus schematic, route, location, and robot marker |
| `CloudAuthGate.tsx` | Supabase session, sign-in, sign-up, and loading gate |
| `DeliveryTimeline.tsx` | Delivery milestone visualization |
| `StatusPill.tsx` | Shared status and priority label styling |

### `src/pages`

| File | Responsibility |
|---|---|
| `Dashboard.tsx` | Operational overview |
| `NewDelivery.tsx` | Request form, validation, and mission preview |
| `Deliveries.tsx` | Delivery records and detail drawer |
| `Dispatch.tsx` | Approval, assignment, dispatch, and mission advancement |
| `Fleet.tsx` | Fleet health, telemetry, and safe commands |
| `Settings.tsx` | Integration documentation and demo tools |

### `src/context`, `src/data`, and `src/lib`

| File | Responsibility |
|---|---|
| `AppContext.tsx` | Shared application state and demo/cloud operations |
| `demo.ts` | Seed data, campus locations, and formatting helpers |
| `supabase.ts` | Supabase client creation and mode selection |
| `id.ts` | Cross-browser identifier generation |
| `types.ts` | Shared TypeScript domain types |

### Backend and robot

| File | Responsibility |
|---|---|
| `supabase/functions/dispatch-delivery/index.ts` | Authorized command creation, audit, and EMQX publication |
| `supabase/functions/dispatch-delivery/publish-response.js` | Pure EMQX HTTP status classifier that prevents 202/no-subscriber responses from dispatching a delivery |
| `supabase/functions/ingest-robot-message/index.ts` | Authenticated EMQX webhook ingestion and message validation |
| `202607160001_initial_schema.sql` | Core schema, RLS, indexes, triggers, seed data, and Realtime |
| `202607160002_server_tracking_codes.sql` | Concurrency-safe tracking code generation |
| `202607170003_delivery_dispatched_status.sql` | Separates broker publication from physical mission start |
| `202607170004_robot_ingestion.sql` | Telemetry/event schema, atomic ingestion functions, and offline Cron job |
| `202607190005_expire_stale_robot_commands.sql` | Expires overdue unacknowledged commands and records warning events |
| `202607190006_authenticated_delivery_requester.sql` | Enforces authenticated requester identity on delivery insertion |
| `202607200007_database_notifications.sql` | Adds persistent per-user notifications, secure read state, event triggers, and Realtime |
| `202607200008_require_dispatched_mission_start.sql` | Rejects a new mission-start event until the linked delivery is dispatched to that robot |
| `202607200009_serialize_mission_start.sql` | Serializes mission-start validation so concurrent events cannot advance one delivery twice |
| `202607210010_robot_connectivity_and_event_order.sql` | Separates bridge presence from telemetry, fails stale robots safe, and rejects delayed control events |
| `202607210011_robot_ingestion_safety_followup.sql` | Adds receipt-time freshness, safe publish reconciliation, atomic dispatch finalization, command reservations, safety reset requirements, and stricter event ordering |
| `supabase/tests/database/*.sql` | pgTAP coverage for schema security, notifications, ingestion state transitions, expiration, and offline detection |
| `supabase/tests/database/007_command_publish.sql` | pgTAP coverage for `PUBLISH_UNKNOWN`, publication finalization, and command reservation races |
| `supabase/tests/integration/emqx-publish-response.test.mjs` | Fast Node coverage proving that only HTTP 200 is delivered and HTTP 202 means no matching subscriber |
| `supabase/tests/integration/ingest-robot-message.test.mjs` | Sends the exact EMQX HTTP action contract through the locally served ingestion function |
| `supabase/tests/robot-ingest.test.env` | Test-only local ingestion secret used by the integration runner |
| `robot-pi/agent.py` | Secure MQTT-to-local mission and ESP32 bridge |
| `robot-pi/local_store.py` | Fsynced atomic JSON writes, command inbox persistence, and interrupted-write recovery |
| `robot-pi/message_contract.py` | Pure Pi state/event validation aligned with the ingestion Edge Function |
| `robot-pi/test_agent.py` | Hardware-free tests for command processing, durable ACK ordering, replay handling, and STOP/ESTOP behavior |
| `robot-pi/test_local_store.py` | Tests one-file-per-command handoff and interrupted-write recovery |
| `robot-pi/test_message_contract.py` | Hardware-free tests for robot identity, telemetry freshness, and event schema rules |
| `robot-pi/robot.env.example` | Secret-free production environment template |
| `MIIT_Rover_Raspberry_Pi_Ubuntu_Setup_Guide.md` | Preserved historical guide used only as a phase/step reference; its old agent and ROS commands require current validation |
| `robot-pi/miit-rover-agent.service` | Hardened systemd unit with network/time ordering and automatic restart |
| `robot-pi/requirements.txt` | Pi Python dependencies |
| `DoneRaspberrypi.md` | Verified completed-work record for the deployed Raspberry Pi bridge |
| `Remainding.md` | Ordered remaining EV-folder, laptop, Supabase, EMQX, Cloudflare, and robot-source tasks |
| `RemaindingRaspberryPi.md` | Ordered remaining Pi deployment, ESP32, navigation, safety, and physical-test tasks |
| `MIIT_Rover_ESP32_Firmware_v0.2.0/robot-esp32/` | Unflashed and hardware-unverified v0.2 raised-wheel commissioning firmware, protocol tester, and host tests |

### Hosting assets

| File | Responsibility |
|---|---|
| `public/robot-mark.svg` | Application logo/favicon |
| `.openai/hosting.json` | Existing hosting project association |
| `scripts/prepare-hosting.mjs` | Produces client/server packaging and a GET fallback worker |
| `wrangler.jsonc` | Cloudflare static asset directory and SPA fallback configuration |

## 19. Development commands

Install:

```bash
npm install
```

Run development server:

```bash
npm run dev
```

Open:

```text
http://localhost:4173
```

Type-check:

```bash
npm run check
```

Run tests:

```bash
npm test
```

Run the local Supabase/EMQX integration suite:

```bash
npm run test:integration
```

Run the hardware-free Raspberry Pi message-contract tests:

```bash
npm run test:pi
```

Run the hardware-free ESP32 protocol/CRC tests:

```bash
npm run test:esp32
```

This host test does not compile the Arduino sketch, flash a controller, open a
serial port, or prove physical safety behavior.

Run the fast EMQX publish-response classifier test:

```bash
npm run test:emqx-publish
```

Build:

```bash
npm run build
```

Preview the production build:

```bash
npm run preview
```

## 20. Testing strategy

The test suite uses:

- Vitest.
- jsdom.
- React Testing Library.
- `user-event`.
- pgTAP against local PostgreSQL.
- Node's built-in test runner for the EMQX HTTP webhook contract and command
  publish-response classifier.
- Python `unittest` for Pi-side command processing, durable storage, robot
  identity, state freshness, ranges, timestamps, UUIDs, and event schemas.
- Python host tests for ESP32 JSON framing, CRC vectors, and the requirement for
  a matching positive motion acknowledgement; these do not compile or exercise
  the firmware or hardware.

`src/test/setup.ts`:

- Cleans rendered components after tests.
- Resets browser history.
- Installs a deterministic `localStorage` implementation.

The storage implementation works around Node versions that expose an incomplete `localStorage` global which can override jsdom.

Workflow tests cover:

- Dashboard rendering.
- Valid delivery creation.
- Approval, assignment, and dispatch.
- ESTOP confirmation.

ID compatibility tests cover:

- Browsers without `crypto.randomUUID`.
- Environments without Web Crypto.

Database tests in `supabase/tests/database/` cover:

- Notification Row Level Security and backend RPC grants.
- Prevention of profile role escalation.
- Database-backed notification visibility, read state, and staff demotion
  enforcement.
- Ordered robot state ingestion.
- Separate bridge/state ordering, stale Last Will/state races, and telemetry-only operational heartbeats.
- Idempotent event ingestion and valid delivery transitions.
- Conflicting event-ID rejection, full lifecycle ordering, safety-latch preservation, and single-use staff reset authorization.
- Rejection of `MISSION_STARTED` unless the delivery is `DISPATCHED`, or is
  `ASSIGNED` during the narrow valid-command publish race covered by migration
  `011`.
- Rejection of the second of two concurrent mission-start events.
- Receipt-time telemetry freshness, `PUBLISH_UNKNOWN` reconciliation, atomic
  publish/dispatch finalization, and serialized mission/control reservations.
- Blocking mission progress and `RETURN_HOME` while the robot is `PAUSED`
  until a validated, single-use `RESUMED` event clears the pause.
- Expiration of stale `PENDING` and `PUBLISHED` commands.
- Stale heartbeat handling and offline event creation.

`supabase/tests/integration/ingest-robot-message.test.mjs` exercises the running
local Edge Function through the same JSON body and `x-emqx-secret` header that
the EMQX HTTP action sends. It covers all four ingestion topics, success paths,
duplicate-message handling, identity mismatches, malformed payloads, invalid
transitions, and authentication failures.

The integration runner requires Node.js 20, npm, Docker, and a running Docker
daemon. It starts local Supabase if needed, resets only the local database
while skipping the optional seed file, lints the schema, runs pgTAP, serves the
ingestion function, and then runs the HTTP contract tests:

```bash
npm run test:integration
```

As of the 21 July 2026 working-tree audit, this Docker-backed pgTAP/Edge
Function suite had not yet been run with migration `011`; the migration and
changed functions remain deployment-blocked until it passes.

Never point this reset workflow at a linked or production project. The runner
checks that the Supabase API URL is a loopback address and uses a committed
test-only ingestion secret, so CI needs no Supabase or EMQX credentials.

The HTTP contract suite simulates the output of an EMQX rule; it does not start
or configure an EMQX broker. A separate smoke test through the deployed EMQX
rule, its connector credentials and ACLs, plus hardware-in-the-loop testing,
remains required.

GitHub Actions has three isolated jobs: Pi bridge/contract/storage plus ESP32
host-protocol tests, frontend type-check/unit/build checks, and the local
Supabase/EMQX webhook contract suite. They run on pull requests and pushes to
`main` without robot or hosted-project credentials.

## 21. Build and hosting

The build script performs:

```text
TypeScript project check
        |
        v
Vite production build
        |
        v
Hosting package preparation
```

Vite writes the browser build to `dist/`.

`prepare-hosting.mjs` adds:

- `dist/client/` containing browser assets.
- `dist/server/index.js` containing an asset worker with SPA fallback.
- `dist/.openai/hosting.json`.

The standard root build also retains:

- `dist/index.html`.
- `dist/assets/`.
- `dist/robot-mark.svg`.

For Cloudflare:

```text
Build command: npm run build:cloudflare
Output directory: dist
```

`wrangler.jsonc` points Cloudflare at `dist` and enables
`single-page-application` fallback behavior without a `_redirects` rule.

Production hosting must define the same three frontend variables used locally:

```text
VITE_SUPABASE_URL
VITE_SUPABASE_PUBLISHABLE_KEY
VITE_CLOUD_MODE=true
```

Because Vite replaces these values at build time, changing them requires a new frontend build and deployment.

The Supabase Auth Site URL and redirect allowlist must include both the production origin and any local or preview origins used for authentication.

## 22. Supabase deployment

Link:

```bash
npx supabase login
npx supabase link --project-ref YOUR_PROJECT_REF
```

Apply migrations:

```bash
npx supabase db push
```

Configure Edge Function secrets:

```bash
npx supabase secrets set \
  EMQX_API_URL=https://YOUR-EMQX-HOST:8443 \
  EMQX_API_KEY=YOUR_API_KEY \
  EMQX_API_SECRET=YOUR_API_SECRET
```

Generate and configure a separate ingestion secret. Keep the value available long enough to paste it into the EMQX HTTP action:

```powershell
$bytes = New-Object byte[] 32
[Security.Cryptography.RandomNumberGenerator]::Fill($bytes)
$ingestSecret = [Convert]::ToHexString($bytes).ToLower()
$ingestSecret | Set-Clipboard
npx supabase secrets set "ROBOT_INGEST_SECRET=$ingestSecret"
```

Deploy both functions:

```bash
npx supabase functions deploy dispatch-delivery
npx supabase functions deploy ingest-robot-message --no-verify-jwt
```

The ingestion function has JWT verification disabled because EMQX is not a Supabase user. The handler itself requires `x-emqx-secret` before it performs any service-role operation.

Configure an EMQX HTTP Server connector with:

```text
Base URL: https://YOUR_PROJECT_REF.supabase.co
Method: POST
Path: /functions/v1/ingest-robot-message
Header: Content-Type: application/json
Header: x-emqx-secret: the generated ROBOT_INGEST_SECRET
```

Use this rule:

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

Use this HTTP body:

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

The EMQX rule-engine `id` value is binary. `bin2hexstr(id)` converts it to the
stable hexadecimal string expected by the ingestion handler; EMQX examples
normally render it as 32 hexadecimal characters. Keep the `${mqttMessageId}`
template quoted in JSON. The handler treats the ID as opaque and accepts a
non-empty message-ID string up to 256 characters.

Enable HTTP action retry/buffering when the selected EMQX deployment exposes those settings. The endpoint returns success only after its database operation completes.

Promote an account only after verifying its identity:

```sql
update public.profiles
set role = 'ADMIN'
where email = 'verified-admin@example.com';
```

## 23. Raspberry Pi installation

```bash
cd robot-pi
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

The bridge should run under systemd with:

- A dedicated service account where practical.
- Root-owned environment configuration.
- Automatic restart.
- Appropriate access to the serial device.
- A writable, persistent state directory.
- Logs captured by journald.

The MQTT account should use per-robot credentials and topic ACLs.

## 24. Current implementation boundaries

The repository is honest about the boundary between an MVP and a production autonomous system.

### 24.1 Implemented

- Responsive role-oriented frontend.
- Demo delivery lifecycle.
- Browser persistence.
- Supabase authentication gate.
- PostgreSQL schema and RLS.
- Realtime refresh for deliveries and robots.
- Concurrency-safe cloud tracking codes.
- Staff-authorized Edge Function.
- Command audit before publish.
- Conservative `PUBLISH_UNKNOWN` handling for ambiguous broker outcomes.
- Atomic command-publication/delivery-dispatch finalization and serialized
  mission/control command reservations.
- Expiring MQTT envelope.
- Pi-side identity, expiry, and duplicate validation.
- TLS MQTT configuration.
- Timed STOP forwarding for PAUSE and distinct latching ESTOP forwarding.
- Shared-secret EMQX-to-Supabase ingestion endpoint.
- Broker/payload/client identity validation.
- Acknowledgement updates for `robot_commands`.
- Device-ordered state ingestion with server receipt-time freshness for `robots`.
- Idempotent and atomic robot-event ingestion.
- Event-driven cloud delivery checkpoints.
- Retained presence and Pi heartbeat publication.
- Automatic stale-robot detection through Supabase Cron.
- Automatic expiration and audit events for stale `PENDING` and `PUBLISHED` commands.
- Authenticated profile snapshots for cloud delivery requester identity.
- Database-backed delivery and operational notifications with persistent per-user read state.
- Realtime notification refresh and protected mark-as-read RPC.
- Column-restricted profile editing that prevents role self-promotion.
- Local pgTAP coverage for database security, ingestion transitions, notifications, command expiration, and offline detection.
- Automated EMQX HTTP webhook contract tests through the local ingestion Edge Function.
- GitHub Actions checks for the Pi/ESP32 host contracts, frontend, and isolated local integration suite.
- Durable Pi ACK and event outbox/archive interfaces.
- Pi-side schema and telemetry-freshness validation with broker-accepted event
  archives for idempotent recovery.
- Hardware-free Pi command-processing, message-contract, and local-storage recovery tests in CI.
- Hardware-free ESP32 framing, CRC-vector, and positive-ACK requirement tests.
- Production web packaging.

### 24.2 Not yet implemented in this repository

- Physical robot navigation.
- A mission manager that produces fresh state snapshots and mission events.
- ROS 2/Nav2 integration.
- Marker detection.
- Production ESP32 motor firmware. The local v0.2 commissioning source includes
  heartbeat/TTL/watchdog logic but has not been compiled, flashed, or
  hardware-verified and is not unattended-production firmware.
- A verified hardware heartbeat/watchdog installation.
- Hardware E-stop implementation.
- A single deployed serial-port owner that arbitrates both navigation and safety traffic.
- A mission-manager event producer that can emit the locally verified linked
  `RESUMED` event required by the cloud reset path.
- Cargo-lock firmware and secure unlock-code delivery.
- Fully transactional robot assignment (active mission commands are now unique per robot/delivery, but assignment itself still needs an atomic reservation workflow).
- SMS or other recipient notification delivery.
- Passing live EMQX-to-Supabase return-path and cloud-to-robot smoke tests.
- Server-enforced rate limiting and abuse protection for delivery/command calls.
- Automated operational alerts for failed or backlogged EMQX HTTP actions.
- Load, penetration, and hardware-in-the-loop testing.

### 24.3 Notable MVP details

- The schematic map uses static campus coordinates, not live SLAM coordinates.
- Dashboard copy and some metrics are representative rather than calculated from historical analytics.
- The frontend model can display an unlock code, while the database stores only `unlock_code_hash`. A secure reveal/verification flow is still required.
- `RESUME` is acknowledged by the bridge, but actual mission resumption is intentionally delegated to a local mission manager after safety checks.
- The bridge provides state and event handoff files, but the local mission manager that produces them is still a required Pi component.
- Online MQTT presence reports bridge connectivity only. Fresh state can
  restore ordinary operational status, but cannot clear `FAULT`/`ESTOP`; only
  a valid single-use post-latch staff RESUME followed by robot `RESUMED` can.

## 25. Recommended next engineering work

Suggested order:

1. Run the Docker-backed integration suite for migration `011`, then deploy the
   pending migrations and both changed Edge Functions only after it passes.
2. Repair and positively test the existing EMQX HTTP action and Deployment API
   credentials, including retries, response metrics, and least-privilege ACLs.
3. Implement server-enforced delivery/command rate limiting and abuse protection.
4. Add automated operational alerts for failed or backlogged EMQX HTTP actions.
5. Reconcile the already deployed Pi bridge with the pushed commit and verify
   an active one-owner ESP32 serial session.
6. Implement the Pi mission manager that consumes requests, produces state and
   linked events, and provides the verified `RESUMED` event path.
7. Select one serial-port owner, compile/flash the commissioning ESP32 source,
   and verify heartbeat, STOP, latching ESTOP, local reset, and the physical
   E-stop with motor power disconnected before raised-wheel testing.
8. Make initial robot assignment transactional and reject already-reserved robots.
9. Design the cargo-lock and one-time-code lifecycle.
10. Perform the live broker and supervised hardware-in-the-loop smoke suites.
11. Complete a hazard analysis before any unsupervised campus trial.

## 26. Security and privacy checklist

Before public operation:

- Require email confirmation.
- Review whether open self-registration is appropriate.
- Add bot and abuse protection.
- Limit delivery creation rates.
- Minimize recipient personal data.
- Publish a privacy notice.
- Define retention and deletion rules for phone numbers and delivery history.
- Verify all staff roles manually.
- Rotate EMQX credentials after testing.
- Use per-robot MQTT identities.
- Verify RLS using USER, ADMIN, and OPERATOR test accounts.
- Confirm service-role and EMQX credentials never enter the browser bundle.
- Restrict CORS origins where operationally appropriate.
- Monitor failed authentication and command attempts.
- Keep physical E-stop access available during every trial.

## 27. Operational definition of done

A cloud-to-robot deployment should not be considered complete until:

- A user can authenticate and create a delivery.
- RLS prevents that user from reading another user's delivery.
- Staff can approve and assign the request.
- The Edge Function writes and publishes a command.
- The robot rejects expired, duplicate, malformed, and wrong-robot commands.
- The robot acknowledges valid commands.
- The acknowledgement appears in the cloud audit record.
- Telemetry and presence become stale when the robot disconnects.
- Internet loss produces a tested safe local response.
- ESP32 heartbeat loss stops motor output.
- Hardware E-stop behavior is verified physically.
- Logs contain enough evidence to reconstruct the mission without exposing secrets.

## 28. MQTT-to-Supabase ingestion update

This section records the July 2026 cloud return-path implementation.

### 28.1 Implemented data path

```text
Pi publisher
  -> EMQX MQTT/TLS
  -> EMQX multi-topic rule
  -> HTTPS HTTP Server action
  -> ingest-robot-message
  -> apply_robot_state/apply_robot_event
  -> robots/robot_commands/robot_events/deliveries
  -> Supabase Realtime
  -> frontend
```

The endpoint intentionally does not trust `payload.robotId` by itself. The following values must all agree:

```text
Topic robot segment
MQTT username
MQTT client ID prefix
Payload robotId
Registered robots.id
Command/delivery ownership when those IDs are present
```

The EMQX-to-function request must also contain the server-only `x-emqx-secret`.

### 28.2 Delivery evidence rules

| Robot event | Required current state | Resulting state |
|---|---|---|
| `MISSION_STARTED` | `DISPATCHED`, or `ASSIGNED` only with its valid reserved in-flight command | `TO_SOURCE` |
| `ARRIVED_SOURCE` | `TO_SOURCE` | `AT_SOURCE` |
| `PACKAGE_LOADED` | `AT_SOURCE` | `PACKAGE_LOADED` |
| `DEPARTED_SOURCE` | `PACKAGE_LOADED` | `TO_DESTINATION` |
| `ARRIVED_DESTINATION` | `TO_DESTINATION` | `AT_DESTINATION` |
| `PACKAGE_RELEASED` | `AT_DESTINATION` | `DELIVERED` |
| `RETURNING_HOME` | `AT_DESTINATION` or `DELIVERED` | `RETURNING` |
| `MISSION_COMPLETED` | `DELIVERED` or `RETURNING` | `COMPLETED` |
| `MISSION_FAILED` | Any non-terminal mission state | `FAILED` |

An event that is impossible for the current delivery or safety state is rejected
and its database insert is rolled back. Repeating an `eventId` is idempotent
only when robot, delivery, command, type, severity, detail, and timestamp are
identical; changed content with the same ID is rejected.

### 28.3 Offline behavior

Only accepted fresh state telemetry updates `robots.last_seen`,
`telemetry_received_at`, and `telemetry_at`. The first two are trusted server
receipt times; `telemetry_at` is the device observation time used for ordering.
Acknowledgements and events do not refresh the operational heartbeat.
Presence updates `bridge_last_seen` and `bridge_online` using the EMQX broker
timestamp instead. The Pi sends retained presence every 15 seconds. An online presence
heartbeat proves MQTT bridge connectivity but intentionally does not restore
the operational robot status; fresh state telemetry must do that. The scheduled
`mark-stale-robots-offline` database function runs once per minute. It clears
`bridge_online` when `bridge_last_seen` is stale and separately marks any robot
without a fresh `telemetry_received_at` for 60 seconds as:

```text
status=OFFLINE
speed=0
signal=0
lidar=OFFLINE
camera=OFFLINE
esp32=OFFLINE
```

It also inserts a `ROBOT_OFFLINE` audit event. The physical robot must not depend on this cloud timeout for stopping; the ESP32 and local mission manager must stop independently and much faster.

### 28.4 Command expiration behavior

The `expire-stale-robot-commands` Supabase Cron job runs once per minute. It atomically finds commands whose `expires_at` time has passed while their status is still `PENDING` or `PUBLISHED`, then:

1. Changes the command status to `EXPIRED`.
2. Preserves the existing `result` JSON and adds the reason, expiration time, and previous status.
3. Inserts one `COMMAND_EXPIRED` warning into `robot_events` for auditing.

Commands already in `ACKNOWLEDGED`, `COMPLETED`, `REJECTED`, `FAILED`, or `EXPIRED` are not changed. Expiration also does not retry the command or change its delivery or robot state: a missing acknowledgement does not prove that the robot never received the command. Any retry or delivery recovery must be an explicit, separately validated action.

`PUBLISH_UNKNOWN` is also intentionally excluded from automatic expiration.
Its external call may have succeeded, so it remains a command/reservation
barrier until identical robot ACK/event evidence reconciles it or staff confirms
through the explicit reconciliation path that it was not published.

### 28.5 Deployment and verification status

As of the 21 July 2026 audit, deployment checklist steps 1 through 8 had been
completed, from deploying the frontend through verifying the then-current
database. This is dated evidence, not a claim that the new local migrations or
functions are deployed.

Step 9, **Test the web workflow**, is blocked at dispatch. Clicking **Dispatch mission** creates the `START_MISSION` audit command, but the delivery does not change from `ASSIGNED` to `DISPATCHED`.

A read-only inspection of the linked Supabase database found:

- The latest commands for `MIIT-1051` and `MIIT-1052` have status `FAILED`.
- Both records have `published_at = null`.
- Both records contain `result.httpStatus = 403`.
- Their deliveries correctly remain `ASSIGNED`.

A live Pi/broker audit on 21 July 2026 added independent evidence:

- The deployed Pi agent maintained MQTT/TLS connectivity and published a
  retained online presence plus a new heartbeat every 15 seconds.
- A separate read-only MQTT client received those broker messages.
- Immediately afterward, Supabase recorded no fresh bridge-connectivity
  evidence. The null telemetry timestamp separately matched the absence of a
  mission-manager state file. The EMQX return action still requires repair and
  a response-metric verification.
- The robot identity received successful subscriptions outside its intended
  own-topic scope, proving that EMQX authorization is not yet default-deny.

This proves that the browser reached `dispatch-delivery`, authentication and staff authorization passed, and the command row was inserted. EMQX then rejected the Deployment API publish request with HTTP 403. The Edge Function intentionally changes a delivery to `DISPATCHED` only after EMQX confirms a publish to a matching subscriber. HTTP 202 with `no_matching_subscribers` is also a non-delivery and must leave the delivery `ASSIGNED`.

A separate operator-reported controlled test from the historical Pi guide did
reach the earlier bridge. Read-only Pi evidence confirms a valid legacy
`START_MISSION` request containing the required command, delivery, route, map,
and timestamp fields; the same command ID exists in the durable processed-command
database. This is evidence of one completed cloud-to-Pi dispatch/handoff test.
It does not change the HTTP 403 evidence for the other audit rows and does not
represent physical or autonomous delivery completion.

After the read-only audit, a user-authorized maintenance pass installed the
exact local Pi follow-up bundle as `pi-agent-1.3.0`. All 27 Pi tests passed on
the installed source as `rover`; systemd verification passed; the service
restarted with zero observed restarts; MQTT connected and accepted the command
subscription; and an `ESP32_DISCONNECTED` event moved through the durable
outbox to the archive after broker acceptance. The Pi checkout is intentionally
dirty because this exact follow-up bundle has not yet been committed and pushed.
Migration `202607210010` was not applied to the linked database, and the
append-only `202607210011` plus the current Edge changes remain local and
Docker-unverified. Run the full local integration suite before any
database/function deployment.

The current local dispatch function now inserts `PUBLISH_UNKNOWN` before its
EMQX call and atomically finalizes publication/dispatch. That behavior is not in
the stale deployed function and has not yet been verified by Docker-backed
pgTAP or a live broker smoke test.

To unblock step 9:

1. In the target EMQX deployment, create or verify a **Deployment API Key**.
2. Use its **App ID** and **App Secret**, not an MQTT client username/password or a platform-level API key.
3. Confirm that the configured API endpoint is the Broker Deployment API for this deployment. With the current function, the final publish URL must resolve to `https://EMQX_API_HOST/api/v5/publish`.
4. Replace `EMQX_API_URL`, `EMQX_API_KEY`, and `EMQX_API_SECRET` in Supabase Edge Function Secrets. Supabase makes updated secrets available without redeploying the function.
5. Retry dispatch and verify that the command is `PUBLISHED` (or already
   `ACKNOWLEDGED`/`COMPLETED` from racing robot evidence), its `published_at` is
   populated, and the delivery becomes `DISPATCHED`. A timeout/5xx command that
   remains `PUBLISH_UNKNOWN` must be reconciled, not blindly retried. An HTTP
   202 response with `no_matching_subscribers` must instead record the command
   as `FAILED`, leave `published_at` null, and keep the delivery `ASSIGNED`.
6. Inspect the EMQX rule action metrics and latest failure. Repair its URL,
   `x-emqx-secret`, body template, or action binding until a Pi presence
   heartbeat changes `robots.bridge_last_seen` within 15 seconds. Then publish
   a valid fresh state snapshot and verify that `robots.last_seen`,
   `telemetry_received_at`, and `telemetry_at` also advance.
7. Replace permissive authorization with explicit per-robot rules: subscribe
   only to `miit/robots/{robotId}/commands`, publish only to that robot's
   `acks`, `state`, `events`, and `presence`, then default deny unmatched
   operations. Repeat both positive and forbidden-topic tests.

## 29. Raspberry Pi deployment status and work remaining

The updated `robot-pi/agent.py` is a transport and handoff process. An initial
read-only audit on 21 July 2026 found the old repository bridge. A subsequent
authorized deployment installed and restarted `pi-agent-1.3.0`, verified all
27 tests on the Pi, confirmed the MQTT/TLS command subscription, observed zero
service restarts, enabled the clock-wait service, and hardened configuration
ownership. The device was absent at bridge startup; a later read-only check
found the persistent USB serial link and permissions present, but no successful
post-start ESP32 handshake was recorded. The active one-owner serial session
must therefore be re-verified before relying on that transport. No secret
credential or private infrastructure value is recorded here.

That deployment does **not** verify a complete robot. The updated agent
successfully archived one broker-accepted disconnect safety event, but the
EMQX-to-Supabase HTTP action was not reverified; recent web commands had also
failed at the EMQX Deployment API with HTTP 403, and the robot MQTT account was
able to subscribe beyond its required topic scope. The Pi still has no
`robot_state.json` or mission-event producer, so the mission manager, telemetry,
event-driven delivery progression, ESP32 watchdog, and physical safety remain
unverified. The local v0.2 ESP32 source has not been compiled, flashed, or
hardware-tested, and the final single serial-port owner is unresolved. The Pi
Git checkout is intentionally dirty because the exact deployed working-tree
bundle is not yet committed. The steps below separate the installed base bridge
from the work still needed for a complete robot.

### 29.1 Provision the operating system

1. Install a supported 64-bit Raspberry Pi OS or Ubuntu image.
2. Create a non-default administrator account and disable unused password-based remote access.
3. Set the hostname to the stable robot identity, for example `robot-01`.
4. Enable automatic time synchronization and its boot wait service. Command
   expiration and telemetry ordering require a correct UTC clock. On the
   audited Ubuntu/Chrony image, enable `chrony-wait.service`; with
   `systemd-timesyncd`, use `systemd-time-wait-sync.service` when available.
5. Install security updates.
6. Install Python, virtual-environment support, Git, and serial-device tools.
7. Add the service account to the group that owns `/dev/ttyUSB0`, normally `dialout`.
8. Disable or firewall unnecessary listening services.
9. Confirm DNS and outbound TLS connectivity to the EMQX broker.
10. Download the EMQX CA file when the deployment requires an explicit CA path.

### 29.2 Install the bridge

Recommended filesystem layout:

```text
/opt/miit-rover/source/                  application checkout
/opt/miit-rover/source/robot-pi/.venv/  Python environment
/etc/miit-rover/robot.env        root-owned secrets
/var/lib/miit-rover/             persistent runtime state
/var/lib/miit-rover/command-inbox one-file-per-command handoff
/var/lib/miit-rover/command-archive consumed command records
/var/lib/miit-rover/event-outbox mission event queue
/var/lib/miit-rover/event-archive broker-accepted event audit/replay archive
/var/lib/miit-rover/ack-outbox durable command acknowledgement queue
/var/lib/miit-rover/ack-archive broker-accepted acknowledgement archive
```

Installation:

```bash
sudo install -d -o root -g root -m 0755 /opt/miit-rover
sudo install -d -o root -g rover -m 0750 /etc/miit-rover
sudo install -d -o rover -g rover -m 0750 /var/lib/miit-rover
sudo install -d -o rover -g rover -m 0750 /var/lib/miit-rover/command-inbox
sudo install -d -o rover -g rover -m 0750 /var/lib/miit-rover/command-archive
sudo install -d -o rover -g rover -m 0750 /var/lib/miit-rover/event-outbox
sudo install -d -o rover -g rover -m 0750 /var/lib/miit-rover/event-archive
sudo install -d -o rover -g rover -m 0750 /var/lib/miit-rover/ack-outbox
sudo install -d -o rover -g rover -m 0750 /var/lib/miit-rover/ack-archive
cd /opt/miit-rover/source/robot-pi
sudo python3 -m venv .venv
sudo .venv/bin/pip install -r requirements.txt
sudo chown -R root:root /opt/miit-rover/source
```

Keep `robot.env` root-owned and mode `0600`. The Python process opens the CA
file itself, so either use Ubuntu's system CA bundle or install the dedicated
CA as `root:rover` mode `0640` inside the group-traversable directory. A
`root:root` mode `0750` directory with a mode `0644` CA file still blocks the
`rover` service at directory traversal.

Example `/etc/miit-rover/robot.env`:

```text
ROBOT_ID=robot-01
MQTT_HOST=YOUR_EMQX_BROKER_HOST
MQTT_PORT=8883
MQTT_USERNAME=robot-01
MQTT_PASSWORD=UNIQUE_PER_ROBOT_PASSWORD
MQTT_CA_FILE=/etc/ssl/certs/ca-certificates.crt
ESP32_SERIAL_PORT=/dev/serial/by-id/YOUR_ESP32_DEVICE
ROBOT_STATE_DIR=/var/lib/miit-rover
ROBOT_AGENT_VERSION=pi-agent-1.3.0
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
ROBOT_ACK_OUTBOX=/var/lib/miit-rover/ack-outbox
ROBOT_ACK_ARCHIVE=/var/lib/miit-rover/ack-archive
```

Protect it:

```bash
sudo chown root:root /etc/miit-rover/robot.env
sudo chmod 600 /etc/miit-rover/robot.env
```

### 29.3 Run the bridge with systemd

Install the maintained template rather than copying a second unit definition
from documentation. This keeps its path guards, timeout, restrictive umask, and
hardening settings synchronized with the source:

```bash
sudo install -o root -g root -m 0644 \
  /opt/miit-rover/source/robot-pi/miit-rover-agent.service \
  /etc/systemd/system/miit-rover-agent.service
sudo systemd-analyze verify /etc/systemd/system/miit-rover-agent.service
```

Verify
`timedatectl show -p NTPSynchronized` before accepting commands and enable the
OS time-wait service; the live audit showed that the system clock was corrected
after the bridge initially started, so network availability and ordering after
`time-sync.target` are insufficient unless a real wait provider is enabled.

Enable and inspect it:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now miit-rover-agent
sudo systemctl status miit-rover-agent
sudo journalctl -u miit-rover-agent -f
```

Success criteria:

- EMQX shows client ID `robot-01-pi`.
- The client subscribes only to its command topic.
- Retained presence is online.
- Supabase updates `bridge_last_seen` from presence; a valid fresh state
  snapshot updates `last_seen`, `telemetry_received_at`, and `telemetry_at`.
- Stopping the service produces offline presence or the database timeout marks it offline.

### 29.4 Implement the mission manager

Create a second local process rather than adding navigation logic to `agent.py`. It should:

1. Watch `command-inbox/*.json`, process requests by `requestedAt` while giving
   ESTOP/PAUSE immediate priority, and never assume only one request is pending.
2. Store the active mission and last processed request in persistent local
   storage, then atomically move consumed requests to `command-archive`.
3. Validate map version, source, destination, robot readiness, battery, sensors, cargo state, and E-stop state.
4. Reject unsafe requests locally and write a durable failure event.
5. Plan and execute movement using the selected navigation stack.
6. Write `robot_state.json` atomically at least every five seconds, including
   a timezone-aware UTC `at` value captured with that sensor snapshot.
7. Write each mission transition to a separate event-outbox file and monitor
   the broker-accepted archive until EMQX HTTP-action health is confirmed.
8. Preserve the cloud `commandId` and `deliveryId` in all mission events.
9. For `RESUME`, keep motion disarmed, require the nearby operator's local
   ESP32 reset and fresh safe telemetry, then publish one linked `RESUMED` event
   containing `payload.localSafetyChecksPassed=true`. The current repository has
   no deployed process that performs this step, so the reset path is incomplete.
10. Resume safely after a Pi reboot without repeating cargo or motion actions.
11. Stop locally if the ESP32 heartbeat, LiDAR, localization, motor feedback, or physical E-stop becomes unhealthy.

Minimum state machine:

```text
BOOT_SAFE
  -> IDLE
  -> VALIDATING_MISSION
  -> NAVIGATING_TO_SOURCE
  -> WAITING_FOR_LOAD
  -> NAVIGATING_TO_DESTINATION
  -> WAITING_FOR_RELEASE
  -> RETURNING_HOME
  -> IDLE

Any moving state
  -> PAUSED
  -> FAULT or ESTOP
```

The manager, not elapsed time, decides when to emit:

```text
MISSION_STARTED
ARRIVED_SOURCE
PACKAGE_LOADED
DEPARTED_SOURCE
ARRIVED_DESTINATION
PACKAGE_RELEASED
RETURNING_HOME
MISSION_COMPLETED
MISSION_FAILED
```

Example atomic event creation in Python:

```python
from local_store import write_json_atomic

event = {
    "eventId": str(uuid.uuid4()),
    "deliveryId": active_delivery_id,
    "commandId": active_command_id,
    "type": "ARRIVED_SOURCE",
    "severity": "INFO",
    "at": datetime.now(timezone.utc).isoformat(),
    "payload": {"locationId": source_location_id},
}
final = outbox / f"{event['eventId']}.json"
write_json_atomic(final, event)
```

Never create a new `eventId` merely because publication is being retried.

### 29.5 Integrate navigation

Choose one navigation architecture and keep the MQTT interface unchanged:

- ROS 2 plus Nav2 for map-based autonomous navigation.
- A smaller waypoint/marker state machine for a constrained test route.

Required navigation outputs:

- Current registered location or route segment.
- Linear speed.
- Localization health.
- Obstacle-stop state.
- Arrival confirmation based on position tolerance and stopped velocity.
- A definitive success or failure result for every requested route.

Before emitting an arrival event, require:

```text
Position inside configured tolerance
Velocity near zero
Obstacle stop clear
Localization healthy
Correct target ID
Stable result for a short confirmation interval
```

### 29.6 Complete the Pi-to-ESP32 protocol

The current Pi bridge sends only the newline-delimited timed `STOP` and latching
`ESTOP` safety frames. The local v0.2 commissioning firmware under
`MIIT_Rover_ESP32_Firmware_v0.2.0/robot-esp32/` additionally defines protected
`ARM`, `HEARTBEAT`, and `DRIVE` frames with a boot session, increasing sequence,
short TTL, and CRC-16, plus ESP32 ACK/NACK and state output. That source has not
been compiled, flashed, or hardware-verified.

The final base-controller/mission-manager integration must still provide:

```text
exactly one serial-port owner
protected ARM/HEARTBEAT/DRIVE publication
ESP32 acknowledgement/state parsing
ESP32 measured wheel speeds
encoder/odometry feedback
fault flags
hardware E-stop state
battery and motor measurements
```

Do not let both `agent.py` and a navigation process open the port. Select a
single base-controller/gateway first, then route MQTT safety requests and local
navigation targets through it. The Pi must send a local heartbeat much faster
than the cloud heartbeat, for example every 50-100 ms, and measured testing must
prove that loss of this stream stops and disarms the ESP32 within the intended
timeout.

### 29.7 ESP32 safety requirements

The v0.2 source contains fail-safe boot, heartbeat/drive expiry, sequence/CRC
checks, latching ESTOP, and local-reset logic. None is a verified hardware
control until the sketch compiles, is flashed, an active one-owner USB serial
session is verified, and the following tests pass:

- Motor outputs disabled after boot.
- Physical E-stop wired independently of cloud software.
- Watchdog reset and safe output state.
- Pi heartbeat timeout.
- Serial frame CRC and sequence checks.
- Command TTL enforcement.
- Encoder feedback and stalled-wheel detection.
- Maximum velocity and acceleration limits.
- Motor overcurrent and overtemperature response.
- Invalid direction/steering combination rejection.
- Explicit local reset procedure after ESTOP.

An MQTT `ESTOP` is an additional remote stop request. It is not a substitute for the physical E-stop circuit.

### 29.8 Hardware test order

Use this order and record every result:

1. Run the Pi and ESP32 with motor power physically disconnected.
2. Verify MQTT identity, ACLs, heartbeat, acknowledgements, and duplicate handling.
3. Verify `robot_state.json` reaches Supabase and older telemetry is ignored.
4. Verify every event transition with an MQTT simulator.
5. Verify invalid transition, wrong robot, wrong username, stale timestamp, and bad secret rejection.
6. Lift wheels off the ground and test STOP-after-boot.
7. Test serial disconnection and Pi-process termination while wheels are commanded.
8. Test physical E-stop and confirm it works without the Pi or Internet.
9. Test encoder direction and low-speed PID with wheels raised.
10. Run a tethered, low-speed straight-line floor test with a nearby operator.
11. Add obstacle stopping and localization.
12. Test one route with no cargo.
13. Test pickup and destination dwell behavior.
14. Test Wi-Fi and Internet loss during every mission state.
15. Test Pi reboot and ESP32 reboot during a paused mission.
16. Only then test a supervised cargo mission in an isolated area.

Do not begin unsupervised campus trials until the physical E-stop, local watchdogs, stopping distance, obstacle detection, and recovery behavior have documented pass results.

## 30. Database-backed notification update

Migration `202607200007_database_notifications.sql` replaces frontend-only cloud alerts with a durable Supabase notification path:

```text
Delivery insert/status change or safety/fault robot event
  -> PostgreSQL notification trigger
  -> one notification row per intended recipient
  -> notification RLS
  -> Supabase Realtime
  -> AppProvider
  -> notification bell
```

Cloud behavior:

1. A requester receives a notification when a delivery is created and whenever its status changes.
2. Current administrators and operators receive new-request, status-change, and selected warning/error/critical robot alerts.
3. Deterministic per-recipient event keys suppress duplicate notification rows.
4. The frontend loads the latest 50 visible records and refreshes when Realtime reports a change.
5. Opening the bell optimistically clears the unread badge and calls `mark_notifications_read()`.
6. If the RPC fails, the frontend reloads authoritative database state and shows a warning.
7. A cloud session starts with an empty notification collection, so demo alerts cannot flash before the database query completes.
8. Demo notification records remain available only in local demo mode.

Security behavior:

- RLS requires `recipient_id = auth.uid()`.
- A `STAFF` alert also requires the recipient's current role to be `ADMIN` or `OPERATOR`.
- Authenticated browser clients have no direct insert, update, or delete grant on notification content.
- The security-definer mark-read RPC is authenticated, caller-scoped, and idempotent.
- Notification metadata contains safe identifiers and does not copy arbitrary robot event payloads.
- Profile update privileges are limited to `full_name` and `email`, preventing users from assigning themselves a staff role.

The migration intentionally does not turn old deliveries or old robot events into unread alerts. External SMS, email, or push delivery is still a separate future feature.

## 31. Automated Supabase/EMQX integration testing update

Item 7 adds database, webhook, and command-publish response test layers without
using production credentials or production data.

### 31.1 PostgreSQL and RLS tests

The pgTAP suites under `supabase/tests/database/` build deterministic Auth,
profile, delivery, robot, command, event, and notification fixtures inside
transactions. Every file ends with a rollback. Together they verify grants,
notification RLS boundaries, profile role restrictions, notification
visibility after staff demotion, telemetry ordering, event idempotency,
lifecycle transitions, command expiration, and stale heartbeat behavior.

Migrations `202607200008_require_dispatched_mission_start.sql`,
`202607200009_serialize_mission_start.sql`, and
`202607210011_robot_ingestion_safety_followup.sql` are part of this test
boundary. The current guard normally requires `DISPATCHED`, with only the
documented `ASSIGNED`/valid-reserved-command exception when robot evidence wins
the publish-response race. Robot/delivery locks prevent concurrent events or
commands from advancing/reserving the same work twice. Only a replay whose
robot, delivery, command, type, severity, payload, and occurrence time are all
identical is accepted as an idempotent duplicate; conflicting content under the
same `message_id` is rejected. `007_command_publish.sql` covers
`PUBLISH_UNKNOWN`, publish reconciliation, atomic finalization, and reservation
races.

### 31.2 EMQX HTTP action contract

The Node integration test serves `ingest-robot-message` in the local Supabase
Edge Runtime and sends the same request that the configured EMQX action sends:

```text
POST /functions/v1/ingest-robot-message
content-type: application/json
x-emqx-secret: local integration secret

{
  "mqttMessageId": "...",
  "topic": "miit/robots/{robotId}/{acks|state|events|presence}",
  "payload": { "...": "topic-specific MQTT payload" },
  "clientid": "{robotId}-pi",
  "username": "{robotId}",
  "qos": 1,
  "timestamp": 1784550000000
}
```

This checks the deployed rule's body and header contract against the real
ingestion handler and local PostgreSQL behavior. It is not a broker emulator:
EMQX rule matching, connector TLS, broker ACLs, retry queues, and the Pi MQTT
client still require a live deployment smoke test.

The live rule SQL must derive `mqttMessageId` with `bin2hexstr(id)` because the
EMQX source field is binary, and the HTTP body must keep the resulting template
quoted. Separately, a dispatch smoke test must prove that EMQX HTTP 202
`no_matching_subscribers` is handled as a failed non-delivery and never advances
the delivery to `DISPATCHED`.

`supabase/tests/integration/emqx-publish-response.test.mjs` exercises the pure
status classifier without Docker. It proves that only HTTP 200 is delivered,
HTTP 202 is `NO_MATCHING_SUBSCRIBERS`, known request-rejection statuses are
definitive failures, and ambiguous/unrecognized statuses stay conservative.

### 31.3 Running the suite

Prerequisites:

- Node.js 20 and npm.
- Docker with the daemon running.
- Dependencies installed with `npm ci` or `npm install`.

Run all local database and webhook integration checks:

```bash
npm run test:emqx-publish
npm run test:integration
```

Current status: the Docker-backed pgTAP and Edge Function run has not yet been
completed with the local `011` migration. Fast tests are not a substitute for
this deployment gate.

The runner:

1. Copies the committed `supabase/` project to a temporary directory while
   excluding linked-project metadata from `supabase/.temp`.
2. Starts a reduced local Supabase stack if one is not already available.
3. Resets the local database with migrations while skipping the optional seed
   file. Baseline rows created by committed migrations still exist.
4. Runs database lint and all pgTAP suites.
5. Starts the local ingestion Edge Function with JWT verification disabled,
   matching the deployed EMQX endpoint.
6. Supplies a test-only `ROBOT_INGEST_SECRET`.
7. Runs the HTTP webhook contract tests.
8. Stops the function and any Supabase stack that it started.

This command intentionally destroys data in the local Supabase database. Never
adapt it to use `--linked`, a hosted API URL, or production credentials. The
runner refuses a non-loopback API URL as an additional safety check.

The Docker project ID is fixed as `miit-rover-integration`. If a stack with
that local ID is already running, the runner reuses it and resets its database.
The temporary work directory isolates hosted-project metadata, but it does not
create a new Docker namespace for every run.

`.github/workflows/ci.yml` repeats the frontend checks and this integration
suite on Ubuntu with Node.js 20. It has read-only repository permissions and
does not receive Supabase or EMQX secrets.

### 31.4 Raspberry Pi and ESP32 host tests

`robot-pi/test_message_contract.py` uses only the Python standard library and
tests the pure helpers in `message_contract.py`. It verifies the same robot-ID
shape, timestamp horizon, UUID format, telemetry enums/ranges, mission-event
linkage, and JSON-object requirements enforced by `ingest-robot-message`.
`test_local_store.py` covers atomic command/outbox storage and recovery, while
`test_agent.py` covers command execution ordering, durable ACK handling,
conflicting replay behavior, and distinct STOP/ESTOP frames. The CI Pi job also
byte-compiles the bridge sources.

`npm run test:esp32` runs the firmware package's host-side CRC and JSON framing
vectors and verifies that motion requires a matching positive acknowledgement.
Neither Pi nor ESP32 tests connect to MQTT or serial hardware, compile or flash
the Arduino sketch, or receive robot credentials. The commissioning source
therefore remains uncompiled, unflashed, and hardware-unverified.
