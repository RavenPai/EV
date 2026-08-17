# Raspberry Pi Delivery Display Prototype

This is the Raspberry Pi mode for the current prototype. It deliberately does
only three things:

1. receives an assigned delivery from EMQX;
2. displays the delivery in a local web page; and
3. publishes a terminal command receipt after an operator presses
   **Acknowledge**.

It does not use ROS, navigation, the ESP32, motors, a mission manager, or
physical delivery events. An acknowledgement means only that the Pi operator
saw and accepted the delivery information. The MQTT receipt uses the command
status `COMPLETED` so the database releases that command reservation and
permits the next prototype delivery. The frontend labels this result
**Acknowledged by Raspberry Pi**; it does not mean a physical delivery was
completed.

## Data path

```text
Frontend -> Supabase dispatch-delivery -> EMQX command topic
         -> Raspberry Pi delivery display
         -> operator presses Acknowledge
         -> EMQX acknowledgement topic -> Supabase ingestion
         -> robot command status/realtime update -> Frontend
```

The frontend does not connect directly to EMQX. Supabase remains the source of
the status shown by the frontend.

## Files

- `delivery_display.py` is the MQTT subscriber and standard-library HTTP UI.
- `message_contract.py` validates the delivery snapshot and acknowledgement.
- `miit-delivery-display.service` runs the display as the existing `rover`
  account.
- `install_delivery_display.sh` installs and verifies the display on the Pi.
- `test_delivery_display.py` covers persistence, rendering, duplicate handling,
  expiry, and operator acknowledgement.

The service reuses `/etc/miit-rover/robot.env`. The installer never prints,
copies, replaces, or commits that file.

## Important single-client rule

`miit-rover-agent.service` and `miit-delivery-display.service` use the same
robot MQTT identity. Do not run them together. The installer disables and
stops the rover agent before it enables the display service. This prevents
EMQX client-ID disconnect loops and prevents two processes from consuming the
same delivery.

## 1. Test on the laptop

From the EV repository:

```powershell
Push-Location robot-pi
python -m unittest test_delivery_display.py test_message_contract.py
Pop-Location
```

Also run the normal project tests before deploying the corresponding frontend
and Supabase changes.

## 2. Copy the display bundle to the Pi

Use a temporary directory owned by the normal SSH user. Replace `<PI_HOST>`
with the Pi address; do not put an MQTT password in any command.

```powershell
ssh evdelivery@<PI_HOST> "mkdir -p ~/miit-delivery-display-stage"

scp `
  robot-pi/delivery_display.py `
  robot-pi/message_contract.py `
  robot-pi/miit-delivery-display.service `
  robot-pi/install_delivery_display.sh `
  evdelivery@<PI_HOST>:~/miit-delivery-display-stage/
```

The existing root-owned `/etc/miit-rover/robot.env` must already contain valid
`ROBOT_ID`, `MQTT_HOST`, `MQTT_PASSWORD`, and `MQTT_CA_FILE` entries. Do not
display that file in a terminal recording or screenshot.

## 3. Install on the Pi

The `-t` option permits `sudo` to request the Pi user's password interactively.
The password is not part of the command or repository.

```powershell
ssh -t evdelivery@<PI_HOST> `
  "sudo bash /home/evdelivery/miit-delivery-display-stage/install_delivery_display.sh"
```

The installer performs these guarded steps:

- validates the staged Python with the installed rover virtual environment;
- keeps the existing MQTT environment file unchanged;
- creates a timestamped root-only backup under `/opt/miit-rover/backups`;
- installs only the display, shared message contract, and display unit;
- disables/stops `miit-rover-agent.service`;
- enables/starts `miit-delivery-display.service`;
- waits for both the local HTTP health endpoint and EMQX subscription; and
- automatically restores the previous files and service states if installation
  fails.

## 4. Open the display

On the Raspberry Pi desktop, open Firefox and visit:

```text
http://127.0.0.1:8080/
```

The page refreshes automatically. No inbound firewall rule is required when
the page is opened on the Pi itself.

Optional non-secret settings can be added to the existing environment file:

```dotenv
DELIVERY_DISPLAY_HOST=0.0.0.0
DELIVERY_DISPLAY_PORT=8080
DELIVERY_DISPLAY_DATABASE=/var/lib/miit-rover/delivery-display.db
DELIVERY_DISPLAY_VERSION=pi-delivery-display-1.0.0
```

These values have the shown defaults, so adding them is not required.

## 5. Verify the service

Run on the Pi:

```bash
systemctl is-enabled miit-delivery-display.service
systemctl is-active miit-delivery-display.service
systemctl is-enabled miit-rover-agent.service || true
systemctl is-active miit-rover-agent.service || true
curl --fail --silent http://127.0.0.1:8080/health
sudo journalctl -u miit-delivery-display.service -n 50 --no-pager
```

Expected results:

- the display service is `enabled` and `active`;
- the old rover agent is `disabled` and `inactive`;
- health reports `ok`, `mqttConnected`, and `mqttSubscribed` as `true`; and
- logs contain `mqtt_command_subscription_ready` without a restart loop.

## 6. Run the end-to-end prototype test

1. Keep the Pi page open.
2. In the frontend, create and approve a new test delivery.
3. Assign it to the same robot ID configured on the Pi.
4. Press **Send to Raspberry Pi** once.
5. Confirm the Pi displays the correct delivery reference, pickup,
   destination, package, recipient, requester, and notes.
6. Confirm the frontend shows that it is waiting for robot acknowledgement.
7. Press **Acknowledge** on the Pi exactly once.
8. Confirm the Pi card shows its acknowledged time.
9. Confirm the frontend changes to acknowledged without pressing another
   frontend workflow button.
10. Refresh both pages and confirm the acknowledged state remains.

The delivery becomes `DISPATCHED` when its information is published, then
remains there. Do not expect arrival, cargo, return-home, or physical mission
completion events from this prototype. The Pi performs no physical mission.

## Troubleshooting

### The Pi page is empty

Check that the frontend delivery is assigned to the same `ROBOT_ID`, the
display health reports an EMQX subscription, and the command has not expired.

```bash
curl --fail --silent http://127.0.0.1:8080/health
sudo journalctl -u miit-delivery-display.service -n 100 --no-pager
```

### The button reports a conflict

The card may be expired, already acknowledged, or MQTT may be disconnected.
Check `/health` and the service log. Repeated clicks are safe and must not
create a second acknowledgement.

### The frontend does not update

Confirm that EMQX received the acknowledgement and that its existing HTTP rule
forwarded the `miit/robots/+/acks` message to `ingest-robot-message`. Then check
the matching `robot_commands` row in Supabase. Do not make the browser an MQTT
subscriber as a workaround.

### Another MQTT client repeatedly disconnects

Confirm only the display service is active:

```bash
systemctl is-active miit-delivery-display.service
systemctl is-active miit-rover-agent.service || true
```

## Return to the original bridge

Use this only when intentionally leaving display-only prototype mode:

```bash
sudo systemctl disable --now miit-delivery-display.service
sudo systemctl enable --now miit-rover-agent.service
```

Never enable both services simultaneously.
