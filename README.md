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
  EMQX_API_SECRET=YOUR_API_SECRET

npx supabase functions deploy dispatch-delivery
```

Restrict the EMQX credentials to the publish API and the robot command topic namespace. A command is first recorded in `robot_commands`, published with QoS 1, and then acknowledged by the Pi.

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
ESP32_SERIAL_PORT=/dev/ttyUSB0
ROBOT_STATE_DIR=/var/lib/miit-rover
```

The MQTT account for each robot should be allowed to subscribe only to its command topic and publish only to its state, event, acknowledgement and presence topics.

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
3. Build command: `npm run build`.
4. Output directory: `dist`.
5. Add the three `VITE_*` variables in the Pages project settings.

The included `public/_redirects` file enables React Router refreshes on Pages.

## Safety boundary

The public application sends only mission-level commands plus PAUSE, RESUME, RETURN_HOME and ESTOP requests. The Raspberry Pi performs local navigation. The ESP32 performs wheel control and must independently enforce its heartbeat timeout, hardware E-stop and STOP-after-boot behavior. Internet loss must cause a safe local response, not uncontrolled motion.
