# MIIT Rover Campus Delivery Project

## 1. Project summary

MIIT Rover is a full-stack minimum viable product for coordinating autonomous electric-vehicle deliveries across the MIIT campus. It combines:

- A responsive React and TypeScript web application.
- A safe browser-only demo mode.
- Supabase Authentication, PostgreSQL, Row Level Security, and Realtime support.
- A Supabase Edge Function that turns authorized web actions into auditable MQTT commands.
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

The user-facing product also provides:

- Delivery history, filtering, search, and details.
- A visual delivery timeline.
- A schematic campus map with route and robot position.
- Robot battery, signal, speed, mode, and sensor health.
- PAUSE, RESUME, RETURN_HOME, and ESTOP requests.
- Role-specific navigation and views.
- Local notifications and toast feedback.

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
- Deliveries and robots are read from PostgreSQL.
- Realtime subscriptions refresh the frontend after delivery or robot changes.
- RLS controls which records each authenticated user can read or modify.
- Dispatch and robot control requests invoke the Edge Function.

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

In demo mode, the Dispatch page advances the normal path with predefined progress and ETA values. In cloud mode, the initial dispatch transition to `TO_SOURCE` is performed by the Edge Function after MQTT publication succeeds.

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

Cloud rows use snake_case database fields. `mapCloudDelivery` and `mapCloudRobot` convert them to the camelCase frontend models.

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
- Advances later checkpoints in the MVP workflow.
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
- Three notifications.
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

Severity values:

```text
INFO
WARNING
ERROR
CRITICAL
```

### 11.8 Triggers and indexes

The schema includes:

- Automatic `updated_at` triggers for deliveries and robots.
- Indexes for requester history, status queues, assigned robot lookups, pending commands, and delivery events.
- Realtime publication for `deliveries` and `robots`.

### 11.9 Server-generated tracking codes

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
| `profiles` | Own profile or staff | User can update own profile |
| `locations` | Authenticated users can read active locations | Admin can manage |
| `robots` | Authenticated users can read | Admin or operator can update |
| `deliveries` | Requester reads own; staff reads all | User creates/cancels own; staff updates |
| `robot_commands` | Staff only | Created and updated through trusted service context |
| `robot_events` | Staff only | Intended for trusted robot/backend ingestion |

`current_user_role()` is a stable security-definer function used by policies to resolve the caller's role.

RLS is essential because the frontend publishable key is intentionally present in the browser bundle. Service-role and EMQX credentials must never be placed in `VITE_*` variables.

## 13. Edge Function

The command function lives at:

```text
supabase/functions/dispatch-delivery/index.ts
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
13. Updates delivery and robot state after successful mission publication.

### 13.2 Supported requests

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

### 13.3 Command expiration

- ESTOP requests expire after 60 seconds.
- Other commands expire after five minutes.

Expiration limits stale command execution. The Pi validates `expiresAt` again before acting.

### 13.4 Required Edge Function environment

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
```

The EMQX account should be limited to the publish API and the robot command topic namespace.

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
- Opens the ESP32 serial port.
- Configures MQTT username/password and TLS certificate validation.
- Configures a retained offline Last Will message.
- Connects and subscribes to the robot's command topic.
- Publishes retained online presence after connection.

### 15.2 Validation

For each command, the bridge verifies:

- JSON is parseable.
- `commandId` exists.
- `schemaVersion` is 1.
- The envelope robot ID matches the configured robot.
- The command has not expired.
- The command ID has not already been processed.
- The command type is supported.

Malformed commands cause a STOP attempt and a FAULT state publication.

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

### 15.6 Pi environment variables

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
```

Production values should be stored in a root-owned systemd environment file, not committed to the repository.

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
│   ├── _redirects
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
└── vitest.config.ts
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
| `202607160001_initial_schema.sql` | Core schema, RLS, indexes, triggers, seed data, and Realtime |
| `202607160002_server_tracking_codes.sql` | Concurrency-safe tracking code generation |
| `robot-pi/agent.py` | Secure MQTT-to-local mission and ESP32 bridge |
| `robot-pi/requirements.txt` | Pi Python dependencies |

### Hosting assets

| File | Responsibility |
|---|---|
| `public/_redirects` | SPA fallback rule |
| `public/robot-mark.svg` | Application logo/favicon |
| `.openai/hosting.json` | Existing hosting project association |
| `scripts/prepare-hosting.mjs` | Produces client/server packaging and a GET fallback worker |

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
- `dist/_redirects`.

For a Git-based static host:

```text
Build command: npm run build
Output directory: dist
```

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

Deploy:

```bash
npx supabase functions deploy dispatch-delivery
```

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
- Production web packaging.

### 24.2 Not yet implemented in this repository

- Physical robot navigation.
- ROS 2/Nav2 integration.
- Marker detection.
- ESP32 motor firmware.
- Hardware heartbeat/watchdog implementation.
- Hardware E-stop implementation.
- Cargo-lock firmware and secure unlock-code delivery.
- Broker-side acknowledgement consumer that updates `robot_commands`.
- General robot event ingestion into `robot_events`.
- Continuous telemetry ingestion from the Pi into `robots`.
- Automatic command expiration worker.
- Formal mission state-transition enforcement in PostgreSQL.
- Transactional robot assignment that prevents double-booking.
- SMS or other recipient notification delivery.
- Full cloud integration tests.
- Load, penetration, and hardware-in-the-loop testing.

### 24.3 Notable MVP details

- The schematic map uses static campus coordinates, not live SLAM coordinates.
- Dashboard copy and some metrics are representative rather than calculated from historical analytics.
- In cloud delivery creation, requester display fields currently use demo labels while `requester_id` stores the authenticated identity. A production revision should populate name and email from the verified profile.
- The frontend model can display an unlock code, while the database stores only `unlock_code_hash`. A secure reveal/verification flow is still required.
- The Pi publishes acknowledgements, but this repository does not include the service that consumes them and updates the command audit row.
- `RESUME` is acknowledged by the bridge, but actual mission resumption is intentionally delegated to a local mission manager after safety checks.
- The Pi state fault message currently includes exception text. Production logging should separate operator-safe status from sensitive diagnostic details.

## 25. Recommended next engineering work

Suggested order:

1. Build the acknowledgement and telemetry ingestion service.
2. Make robot assignment transactional and reject already-occupied robots.
3. Add explicit database transition functions for delivery state changes.
4. Populate requester fields from the authenticated profile.
5. Design the cargo-lock and one-time-code lifecycle.
6. Implement the local mission-manager interface instead of a shared JSON file.
7. Add structured Pi logging, reconnect handling, and service supervision.
8. Add broker ACL tests and credential rotation procedures.
9. Add Supabase integration tests and an MQTT test broker.
10. Perform supervised hardware-in-the-loop testing with wheels raised.
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

