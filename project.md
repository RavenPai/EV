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

This repository is an MVP and integration foundation, not a complete autonomous navigation stack. Local mapping, route planning, obstacle avoidance, motor PID control, hardware watchdogs, cargo-lock control, and physical E-stop behavior remain responsibilities of the Raspberry Pi, ESP32, and associated firmware.

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
11. The Pi passes a mission request to the local mission manager or forwards a STOP frame to the ESP32.
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
                              +----> Local mission request file
                              |
                              +----> ESP32 UART STOP frame
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
- `telemetry_at` rejects an older state snapshot after a newer snapshot has already been applied.
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
PUBLISHED
ACKNOWLEDGED
REJECTED
COMPLETED
FAILED
EXPIRED
```

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
supabase/functions/ingest-robot-message/index.ts
```

It runs in the Supabase Deno environment.

### 13.1 Request processing

The function:

1. Handles CORS preflight.
2. Accepts only POST requests.
3. Requires an Authorization header.
4. Validates the Supabase user session.
5. Loads the caller's application role.
6. Requires `ADMIN` or `OPERATOR`.
7. Accepts either a delivery dispatch or a robot command.
8. Validates command type and delivery readiness.
9. Creates an expiring command envelope.
10. Inserts a `PENDING` command audit row.
11. Calls the EMQX v5 publish REST API.
12. Marks publish success or failure.
13. Marks the delivery `DISPATCHED`; physical movement state is not changed until a robot event arrives.

### 13.2 Ingestion processing

`ingest-robot-message`:

1. Accepts only POST requests.
2. Requires the `x-emqx-secret` shared secret.
3. Validates the EMQX topic, MQTT username, MQTT client ID, payload `robotId`, and schema version.
4. Accepts only the acknowledgement, state, event, and presence topic suffixes.
5. Validates UUIDs, timestamps, enums, numeric ranges, sensor states, and message size.
6. Updates `robot_commands` from acknowledgements.
7. Calls `apply_robot_state` so out-of-order telemetry cannot replace newer state.
8. Calls `apply_robot_event` so event insertion and delivery transitions are atomic.
9. Treats repeated event IDs as successful QoS 1 duplicates.
10. Uses server receipt time for `last_seen`.
11. Rejects a topic/payload identity mismatch before using the service role.

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
  "firmwareVersion": "pi-agent-1.1.0"
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
  "firmwareVersion": "pi-agent-1.1.0"
}
```

Presence is retained. The Pi publishes online presence after connection and every 15 seconds. Its MQTT Last Will publishes the same schema with `online: false`.

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
- Creates its persistent state directory.
- Opens a SQLite database for processed command IDs.
- Opens the ESP32 serial port when available and reports an event if the link is unavailable.
- Configures MQTT username/password and TLS certificate validation.
- Configures a retained offline Last Will message.
- Connects and subscribes to the robot's command topic.
- Publishes retained online presence after connection.
- Starts a background presence, state, and event-outbox publisher.

### 15.2 Validation

For each command, the bridge verifies:

- JSON is parseable.
- `commandId` exists.
- `schemaVersion` is 1.
- The envelope robot ID matches the configured robot.
- The command has not expired.
- The command ID has not already been processed.
- The command type is supported.

Malformed commands cause a best-effort STOP, a rejected acknowledgement when a command ID is available, and a `BRIDGE_FAULT` event.

### 15.3 Idempotency

Processed command IDs are stored in:

```text
${ROBOT_STATE_DIR}/commands.db
```

When a duplicate command arrives, the Pi does not execute it again. It publishes an acknowledgement explaining that the previous result is retained.

### 15.4 Local mission handoff

`RETURN_HOME` and `START_MISSION` write:

```text
${ROBOT_STATE_DIR}/mission_request.json
```

A separate local navigation or mission-management process is expected to consume that file.

The file is written atomically and contains:

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

`PAUSE`, `RESUME`, `RETURN_HOME`, and `ESTOP` are also written to this file. Receiving `RESUME` does not itself restart motion; the mission manager must check local safety state and publish `RESUMED` only after accepting the request.

### 15.5 ESP32 handoff

`PAUSE` and `ESTOP` send a JSON-line STOP frame over UART:

```json
{
  "v": 1,
  "cmd": "STOP",
  "ttlMs": 300
}
```

The ESP32 must still enforce:

- Command framing and integrity.
- Heartbeat timeout.
- STOP after boot.
- Hardware E-stop.
- Local fault behavior.
- Safe motor output.

### 15.6 State and event handoff

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
  "motorTempC": 37.2
}
```

The mission manager writes each important mission event as a separate JSON file into:

```text
${ROBOT_STATE_DIR}/event-outbox/
```

It must write to a temporary filename and rename the completed file to `*.json`. The agent adds an `eventId` and timestamp if they are absent, persists those fields before publishing, waits for the MQTT QoS 1 publish acknowledgement, and deletes the file only after broker acceptance. Invalid files are renamed to `*.bad`.

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
ROBOT_STATE_DIR=/var/lib/miit-rover
MQTT_CA_FILE=
ROBOT_AGENT_VERSION=pi-agent-1.1.0
PRESENCE_INTERVAL_SECONDS=15
STATE_INTERVAL_SECONDS=5
ROBOT_STATE_FILE=/var/lib/miit-rover/robot_state.json
ROBOT_EVENT_OUTBOX=/var/lib/miit-rover/event-outbox
```

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
- STOP is forwarded locally for pause and E-stop.
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
├── .openai/
│   └── hosting.json
├── public/
│   └── robot-mark.svg
├── robot-pi/
│   ├── agent.py
│   └── requirements.txt
├── scripts/
│   └── prepare-hosting.mjs
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
│   ├── functions/
│   │   └── dispatch-delivery/
│   │       └── index.ts
│   └── migrations/
│       ├── 202607160001_initial_schema.sql
│       └── 202607160002_server_tracking_codes.sql
├── .env.example
├── .gitignore
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
| `vite.config.ts` | React plugin and port 4173 development/preview server |
| `vitest.config.ts` | jsdom test environment and shared setup |
| `tsconfig*.json` | TypeScript project, browser, and Node configuration |

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
| `supabase/functions/ingest-robot-message/index.ts` | Authenticated EMQX webhook ingestion and message validation |
| `202607160001_initial_schema.sql` | Core schema, RLS, indexes, triggers, seed data, and Realtime |
| `202607160002_server_tracking_codes.sql` | Concurrency-safe tracking code generation |
| `202607170003_delivery_dispatched_status.sql` | Separates broker publication from physical mission start |
| `202607170004_robot_ingestion.sql` | Telemetry/event schema, atomic ingestion functions, and offline Cron job |
| `202607190005_expire_stale_robot_commands.sql` | Expires overdue unacknowledged commands and records warning events |
| `202607190006_authenticated_delivery_requester.sql` | Enforces authenticated requester identity on delivery insertion |
| `202607200007_database_notifications.sql` | Adds persistent per-user notifications, secure read state, event triggers, and Realtime |
| `robot-pi/agent.py` | Secure MQTT-to-local mission and ESP32 bridge |
| `robot-pi/requirements.txt` | Pi Python dependencies |

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

The current tests primarily exercise deterministic demo mode. Separate integration tests with a disposable Supabase project and MQTT broker would be needed for full cloud end-to-end verification.

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
  id AS mqttMessageId,
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
- Expiring MQTT envelope.
- Pi-side identity, expiry, and duplicate validation.
- TLS MQTT configuration.
- STOP forwarding for PAUSE and ESTOP.
- Shared-secret EMQX-to-Supabase ingestion endpoint.
- Broker/payload/client identity validation.
- Acknowledgement updates for `robot_commands`.
- Ordered state ingestion for `robots`.
- Idempotent and atomic robot-event ingestion.
- Event-driven cloud delivery checkpoints.
- Retained presence and Pi heartbeat publication.
- Automatic stale-robot detection through Supabase Cron.
- Automatic expiration and audit events for stale `PENDING` and `PUBLISHED` commands.
- Authenticated profile snapshots for cloud delivery requester identity.
- Database-backed delivery and operational notifications with persistent per-user read state.
- Realtime notification refresh and protected mark-as-read RPC.
- Column-restricted profile editing that prevents role self-promotion.
- Durable Pi event outbox interface for the local mission manager.
- Production web packaging.

### 24.2 Not yet implemented in this repository

- Physical robot navigation.
- ROS 2/Nav2 integration.
- Marker detection.
- ESP32 motor firmware.
- Hardware heartbeat/watchdog implementation.
- Hardware E-stop implementation.
- Cargo-lock firmware and secure unlock-code delivery.
- Transactional robot assignment that prevents double-booking.
- SMS or other recipient notification delivery.
- Full cloud integration tests.
- Load, penetration, and hardware-in-the-loop testing.

### 24.3 Notable MVP details

- The schematic map uses static campus coordinates, not live SLAM coordinates.
- Dashboard copy and some metrics are representative rather than calculated from historical analytics.
- The frontend model can display an unlock code, while the database stores only `unlock_code_hash`. A secure reveal/verification flow is still required.
- `RESUME` is acknowledged by the bridge, but actual mission resumption is intentionally delegated to a local mission manager after safety checks.
- The bridge provides state and event handoff files, but the local mission manager that produces them is still a required Pi component.

## 25. Recommended next engineering work

Suggested order:

1. Configure the EMQX HTTP Server connector and rule for the new ingestion endpoint.
2. Implement the Pi mission manager that consumes requests and produces state/events.
3. Implement the ESP32 motor, encoder, heartbeat, and physical E-stop firmware.
4. Make robot assignment transactional and reject already-occupied robots.
5. Design the cargo-lock and one-time-code lifecycle.
6. Add broker ACL tests and credential rotation procedures.
7. Add Supabase integration tests and an MQTT test broker.
8. Perform supervised hardware-in-the-loop testing with wheels raised.
9. Complete a hazard analysis before any unsupervised campus trial.

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
| `MISSION_STARTED` | `DISPATCHED` | `TO_SOURCE` |
| `ARRIVED_SOURCE` | `TO_SOURCE` | `AT_SOURCE` |
| `PACKAGE_LOADED` | `AT_SOURCE` | `PACKAGE_LOADED` |
| `DEPARTED_SOURCE` | `PACKAGE_LOADED` | `TO_DESTINATION` |
| `ARRIVED_DESTINATION` | `TO_DESTINATION` | `AT_DESTINATION` |
| `PACKAGE_RELEASED` | `AT_DESTINATION` | `DELIVERED` |
| `RETURNING_HOME` | `AT_DESTINATION` or `DELIVERED` | `RETURNING` |
| `MISSION_COMPLETED` | `DELIVERED` or `RETURNING` | `COMPLETED` |
| `MISSION_FAILED` | Any non-terminal mission state | `FAILED` |

An event that is impossible for the current delivery state is rejected and its database insert is rolled back. A repeated `eventId` returns success as an idempotent duplicate.

### 28.3 Offline behavior

Valid robot messages update `robots.last_seen` using server time. The Pi sends retained presence every 15 seconds. The scheduled `mark-stale-robots-offline` database function runs once per minute and marks any robot without a valid message for 60 seconds as:

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

### 28.5 Deployment and verification status

As of 2026-07-19, deployment checklist steps 1 through 8 have been completed, from deploying the updated frontend through verifying the database.

Step 9, **Test the web workflow**, is blocked at dispatch. Clicking **Dispatch mission** creates the `START_MISSION` audit command, but the delivery does not change from `ASSIGNED` to `DISPATCHED`.

A read-only inspection of the linked Supabase database found:

- The latest commands for `MIIT-1051` and `MIIT-1052` have status `FAILED`.
- Both records have `published_at = null`.
- Both records contain `result.httpStatus = 403`.
- Their deliveries correctly remain `ASSIGNED`.

This proves that the browser reached `dispatch-delivery`, authentication and staff authorization passed, and the command row was inserted. EMQX then rejected the Deployment API publish request with HTTP 403. The Edge Function intentionally changes a delivery to `DISPATCHED` only after EMQX accepts the publish.

To unblock step 9:

1. In the target EMQX deployment, create or verify a **Deployment API Key**.
2. Use its **App ID** and **App Secret**, not an MQTT client username/password or a platform-level API key.
3. Confirm that the configured API endpoint is the Broker Deployment API for this deployment. With the current function, the final publish URL must resolve to `https://EMQX_API_HOST/api/v5/publish`.
4. Replace `EMQX_API_URL`, `EMQX_API_KEY`, and `EMQX_API_SECRET` in Supabase Edge Function Secrets. Supabase makes updated secrets available without redeploying the function.
5. Retry dispatch and verify that the new command is `PUBLISHED`, its `published_at` is populated, and the delivery becomes `DISPATCHED`.

## 29. Detailed Raspberry Pi work remaining

The updated `robot-pi/agent.py` is a transport and handoff process. The steps below are still required to turn it into a complete robot.

### 29.1 Provision the operating system

1. Install a supported 64-bit Raspberry Pi OS or Ubuntu image.
2. Create a non-default administrator account and disable unused password-based remote access.
3. Set the hostname to the stable robot identity, for example `robot-01`.
4. Enable automatic time synchronization. Command expiration and telemetry ordering require a correct UTC clock.
5. Install security updates.
6. Install Python, virtual-environment support, Git, and serial-device tools.
7. Add the service account to the group that owns `/dev/ttyUSB0`, normally `dialout`.
8. Disable or firewall unnecessary listening services.
9. Confirm DNS and outbound TLS connectivity to the EMQX broker.
10. Download the EMQX CA file when the deployment requires an explicit CA path.

### 29.2 Install the bridge

Recommended filesystem layout:

```text
/opt/miit-rover/                  application checkout
/opt/miit-rover/robot-pi/.venv/  Python environment
/etc/miit-rover/robot.env        root-owned secrets
/var/lib/miit-rover/             persistent runtime state
/var/lib/miit-rover/event-outbox mission event queue
```

Installation:

```bash
sudo mkdir -p /opt/miit-rover /etc/miit-rover /var/lib/miit-rover/event-outbox
sudo chown -R rover:rover /opt/miit-rover /var/lib/miit-rover
cd /opt/miit-rover/robot-pi
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

Example `/etc/miit-rover/robot.env`:

```text
ROBOT_ID=robot-01
MQTT_HOST=YOUR_EMQX_BROKER_HOST
MQTT_PORT=8883
MQTT_USERNAME=robot-01
MQTT_PASSWORD=UNIQUE_PER_ROBOT_PASSWORD
MQTT_CA_FILE=/etc/ssl/certs/ca-certificates.crt
ESP32_SERIAL_PORT=/dev/ttyUSB0
ROBOT_STATE_DIR=/var/lib/miit-rover
ROBOT_AGENT_VERSION=pi-agent-1.1.0
PRESENCE_INTERVAL_SECONDS=15
STATE_INTERVAL_SECONDS=5
```

Protect it:

```bash
sudo chown root:root /etc/miit-rover/robot.env
sudo chmod 600 /etc/miit-rover/robot.env
```

### 29.3 Run the bridge with systemd

Create `/etc/systemd/system/miit-rover-agent.service`:

```ini
[Unit]
Description=MIIT Rover MQTT bridge
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=rover
Group=rover
SupplementaryGroups=dialout
WorkingDirectory=/opt/miit-rover/robot-pi
EnvironmentFile=/etc/miit-rover/robot.env
ExecStart=/opt/miit-rover/robot-pi/.venv/bin/python /opt/miit-rover/robot-pi/agent.py
Restart=always
RestartSec=3
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=true
ReadWritePaths=/var/lib/miit-rover

[Install]
WantedBy=multi-user.target
```

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
- Supabase updates `last_seen`.
- Stopping the service produces offline presence or the database timeout marks it offline.

### 29.4 Implement the mission manager

Create a second local process rather than adding navigation logic to `agent.py`. It should:

1. Watch `mission_request.json` for a new `commandId`.
2. Store the active mission and last processed request in persistent local storage.
3. Validate map version, source, destination, robot readiness, battery, sensors, cargo state, and E-stop state.
4. Reject unsafe requests locally and write a durable failure event.
5. Plan and execute movement using the selected navigation stack.
6. Write `robot_state.json` atomically at least every five seconds.
7. Write each mission transition to a separate event-outbox file.
8. Preserve the cloud `commandId` and `deliveryId` in all mission events.
9. Resume safely after a Pi reboot without repeating cargo or motion actions.
10. Stop locally if the ESP32 heartbeat, LiDAR, localization, motor feedback, or physical E-stop becomes unhealthy.

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
event = {
    "eventId": str(uuid.uuid4()),
    "deliveryId": active_delivery_id,
    "commandId": active_command_id,
    "type": "ARRIVED_SOURCE",
    "severity": "INFO",
    "at": datetime.now(timezone.utc).isoformat(),
    "payload": {"locationId": source_location_id},
}
temporary = outbox / f"{event['eventId']}.tmp"
final = outbox / f"{event['eventId']}.json"
temporary.write_text(json.dumps(event))
temporary.replace(final)
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

The current bridge sends a newline-delimited JSON STOP frame. Replace or extend this with a versioned protocol containing:

```text
protocol version
sequence number
command type
left/right target velocity or steering target
short expiry/TTL
CRC or equivalent frame-integrity check
ESP32 acknowledgement sequence
ESP32 measured wheel speeds
fault flags
hardware E-stop state
battery and motor measurements
```

The Pi must send a local heartbeat much faster than the cloud heartbeat, for example every 50-100 ms. The ESP32 should stop motor output if that heartbeat is absent for a tested short timeout.

### 29.7 ESP32 safety requirements

Before enabling motors, implement and test:

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
