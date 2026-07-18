"""MQTT bridge between EMQX, the Pi mission manager, and the ESP32.

The bridge accepts only expiring mission-level cloud commands. It publishes
presence, state snapshots, acknowledgements, and durable mission events using
the schema consumed by the Supabase ingest-robot-message Edge Function.

Navigation remains a separate local process. That process reads
mission_request.json, writes robot_state.json atomically, and places durable
event JSON files into the event-outbox directory.
"""

from __future__ import annotations

import json
import os
import signal
import sqlite3
import ssl
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import paho.mqtt.client as mqtt
import serial


ROBOT_ID = os.environ.get("ROBOT_ID", "robot-01")
MQTT_HOST = os.environ["MQTT_HOST"]
MQTT_PORT = int(os.environ.get("MQTT_PORT", "8883"))
MQTT_USERNAME = os.environ["MQTT_USERNAME"]
MQTT_PASSWORD = os.environ["MQTT_PASSWORD"]
MQTT_CA_FILE = os.environ.get("MQTT_CA_FILE")
SERIAL_PORT = os.environ.get("ESP32_SERIAL_PORT", "/dev/ttyUSB0")
STATE_DIR = Path(os.environ.get("ROBOT_STATE_DIR", "/var/lib/miit-rover"))
FIRMWARE_VERSION = os.environ.get("ROBOT_AGENT_VERSION", "pi-agent-1.1.0")
PRESENCE_INTERVAL_SECONDS = float(os.environ.get("PRESENCE_INTERVAL_SECONDS", "15"))
STATE_INTERVAL_SECONDS = float(os.environ.get("STATE_INTERVAL_SECONDS", "5"))

STATE_DIR.mkdir(parents=True, exist_ok=True)
MISSION_REQUEST_FILE = STATE_DIR / "mission_request.json"
ROBOT_STATE_FILE = Path(os.environ.get("ROBOT_STATE_FILE", str(STATE_DIR / "robot_state.json")))
EVENT_OUTBOX_DIR = Path(os.environ.get("ROBOT_EVENT_OUTBOX", str(STATE_DIR / "event-outbox")))
EVENT_OUTBOX_DIR.mkdir(parents=True, exist_ok=True)

COMMAND_TOPIC = f"miit/robots/{ROBOT_ID}/commands"
ACK_TOPIC = f"miit/robots/{ROBOT_ID}/acks"
STATE_TOPIC = f"miit/robots/{ROBOT_ID}/state"
EVENT_TOPIC = f"miit/robots/{ROBOT_ID}/events"
PRESENCE_TOPIC = f"miit/robots/{ROBOT_ID}/presence"
ALLOWED_EVENT_TYPES = {
    "MISSION_STARTED",
    "ARRIVED_SOURCE",
    "PACKAGE_LOADED",
    "DEPARTED_SOURCE",
    "ARRIVED_DESTINATION",
    "PACKAGE_RELEASED",
    "RETURNING_HOME",
    "MISSION_COMPLETED",
    "MISSION_FAILED",
    "PAUSED",
    "RESUMED",
    "ESTOP_TRIGGERED",
    "OBSTACLE_DETECTED",
    "LOW_BATTERY",
    "ESP32_DISCONNECTED",
    "BRIDGE_FAULT",
}
ALLOWED_SEVERITIES = {"INFO", "WARNING", "ERROR", "CRITICAL"}

if MQTT_USERNAME != ROBOT_ID:
    raise RuntimeError("MQTT_USERNAME must equal ROBOT_ID for broker-to-database identity validation")

db = sqlite3.connect(STATE_DIR / "commands.db", check_same_thread=False)
db.execute(
    "create table if not exists processed_commands "
    "(id text primary key, processed_at text not null)"
)
db.commit()

serial_lock = threading.Lock()
esp32: serial.Serial | None = None
stop_event = threading.Event()


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    temporary = path.with_suffix(f"{path.suffix}.tmp")
    temporary.write_text(json.dumps(payload, separators=(",", ":")), encoding="utf-8")
    temporary.replace(path)


def presence_payload(online: bool) -> dict[str, Any]:
    return {
        "schemaVersion": 1,
        "robotId": ROBOT_ID,
        "online": online,
        "at": utc_now(),
        "firmwareVersion": FIRMWARE_VERSION,
    }


def publish_presence(client: mqtt.Client, online: bool) -> None:
    client.publish(
        PRESENCE_TOPIC,
        json.dumps(presence_payload(online), separators=(",", ":")),
        qos=1,
        retain=True,
    )


def acknowledge(
    client: mqtt.Client,
    command_id: str,
    status: str,
    reason: str = "",
) -> None:
    client.publish(
        ACK_TOPIC,
        json.dumps(
            {
                "schemaVersion": 1,
                "commandId": command_id,
                "robotId": ROBOT_ID,
                "status": status,
                "reason": reason[:240],
                "at": utc_now(),
            },
            separators=(",", ":"),
        ),
        qos=1,
        retain=False,
    )


def publish_event(
    client: mqtt.Client,
    event_type: str,
    severity: str,
    detail: dict[str, Any] | None = None,
    *,
    delivery_id: str | None = None,
    command_id: str | None = None,
    event_id: str | None = None,
) -> mqtt.MQTTMessageInfo:
    payload: dict[str, Any] = {
        "schemaVersion": 1,
        "eventId": event_id or str(uuid.uuid4()),
        "robotId": ROBOT_ID,
        "type": event_type,
        "severity": severity,
        "at": utc_now(),
        "payload": detail or {},
    }
    if delivery_id:
        payload["deliveryId"] = delivery_id
    if command_id:
        payload["commandId"] = command_id

    return client.publish(
        EVENT_TOPIC,
        json.dumps(payload, separators=(",", ":")),
        qos=1,
        retain=False,
    )


def already_processed(command_id: str) -> bool:
    return (
        db.execute(
            "select 1 from processed_commands where id = ?",
            (command_id,),
        ).fetchone()
        is not None
    )


def mark_processed(command_id: str) -> None:
    db.execute(
        "insert or ignore into processed_commands values (?, ?)",
        (command_id, utc_now()),
    )
    db.commit()


def ensure_esp32() -> serial.Serial:
    global esp32
    with serial_lock:
        if esp32 is not None and esp32.is_open:
            return esp32
        esp32 = serial.Serial(SERIAL_PORT, 115200, timeout=1, write_timeout=1)
        return esp32


def close_esp32() -> None:
    global esp32
    with serial_lock:
        if esp32 is not None:
            try:
                esp32.close()
            finally:
                esp32 = None


def send_to_esp32(command: str) -> None:
    frame = {"v": 1, "cmd": command, "ttlMs": 300 if command == "STOP" else 2000}
    try:
        link = ensure_esp32()
        with serial_lock:
            link.write((json.dumps(frame, separators=(",", ":")) + "\n").encode())
            link.flush()
    except Exception:
        close_esp32()
        raise


def write_mission_request(
    request_type: str,
    command_id: str,
    payload: dict[str, Any] | None = None,
) -> None:
    request = {
        **(payload or {}),
        "type": request_type,
        "commandId": command_id,
        "requestedAt": utc_now(),
    }
    write_json_atomic(MISSION_REQUEST_FILE, request)


def publish_state_file(client: mqtt.Client) -> None:
    if not ROBOT_STATE_FILE.exists():
        return

    state = json.loads(ROBOT_STATE_FILE.read_text(encoding="utf-8"))
    if not isinstance(state, dict):
        raise ValueError("robot_state.json must contain a JSON object")

    required = {
        "status",
        "mode",
        "battery",
        "signal",
        "speedMps",
        "currentDeliveryId",
        "lidar",
        "camera",
        "esp32",
        "motorTempC",
    }
    missing = sorted(required.difference(state))
    if missing:
        raise ValueError(f"robot_state.json is missing: {', '.join(missing)}")

    payload = {
        **state,
        "schemaVersion": 1,
        "robotId": ROBOT_ID,
        "at": utc_now(),
        "firmwareVersion": FIRMWARE_VERSION,
    }
    client.publish(
        STATE_TOPIC,
        json.dumps(payload, separators=(",", ":")),
        qos=1,
        retain=False,
    )


def prepare_outbox_event(path: Path) -> dict[str, Any]:
    event = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(event, dict):
        raise ValueError("event outbox file must contain a JSON object")
    if not isinstance(event.get("type"), str) or not isinstance(event.get("severity"), str):
        raise ValueError("event outbox file requires type and severity")
    if event["type"] not in ALLOWED_EVENT_TYPES:
        raise ValueError("event outbox file contains an unsupported type")
    if event["severity"] not in ALLOWED_SEVERITIES:
        raise ValueError("event outbox file contains an unsupported severity")

    changed = False
    if not event.get("eventId"):
        event["eventId"] = str(uuid.uuid4())
        changed = True
    if not event.get("at"):
        event["at"] = utc_now()
        changed = True
    event["schemaVersion"] = 1
    event["robotId"] = ROBOT_ID
    event.setdefault("payload", {})
    if changed:
        write_json_atomic(path, event)
    return event


def flush_event_outbox(client: mqtt.Client) -> None:
    for path in sorted(EVENT_OUTBOX_DIR.glob("*.json")):
        try:
            event = prepare_outbox_event(path)
            info = client.publish(
                EVENT_TOPIC,
                json.dumps(event, separators=(",", ":")),
                qos=1,
                retain=False,
            )
            info.wait_for_publish(timeout=5)
            if info.is_published():
                path.unlink()
        except (json.JSONDecodeError, ValueError) as exc:
            print(f"Rejecting invalid event outbox file {path.name}: {exc}", flush=True)
            path.replace(path.with_suffix(".bad"))
        except Exception as exc:
            print(f"Event outbox publish deferred: {type(exc).__name__}", flush=True)
            return


def background_publisher(client: mqtt.Client) -> None:
    last_presence = 0.0
    last_state = 0.0
    while not stop_event.wait(1):
        if not client.is_connected():
            continue

        now = time.monotonic()
        if now - last_presence >= PRESENCE_INTERVAL_SECONDS:
            publish_presence(client, True)
            last_presence = now
        if now - last_state >= STATE_INTERVAL_SECONDS:
            try:
                publish_state_file(client)
            except Exception as exc:
                print(f"State publish skipped: {type(exc).__name__}: {exc}", flush=True)
            last_state = now
        flush_event_outbox(client)


def on_connect(
    client: mqtt.Client,
    _userdata: object,
    _flags: mqtt.ConnectFlags,
    reason_code: mqtt.ReasonCode,
    _properties: mqtt.Properties | None,
) -> None:
    if reason_code != 0:
        raise RuntimeError(f"MQTT connection failed: {reason_code}")
    client.subscribe(COMMAND_TOPIC, qos=1)
    publish_presence(client, True)
    try:
        ensure_esp32()
    except Exception as exc:
        publish_event(
            client,
            "ESP32_DISCONNECTED",
            "ERROR",
            {"reason": type(exc).__name__},
        )


def on_message(
    client: mqtt.Client,
    _userdata: object,
    message: mqtt.MQTTMessage,
) -> None:
    command_id: str | None = None
    try:
        envelope = json.loads(message.payload)
        if not isinstance(envelope, dict):
            raise ValueError("command envelope must be a JSON object")

        command_id = str(uuid.UUID(str(envelope["commandId"])))
        if envelope.get("schemaVersion") != 1 or envelope.get("robotId") != ROBOT_ID:
            acknowledge(client, command_id, "REJECTED", "wrong schema or robot")
            return

        expires_at = datetime.fromisoformat(
            str(envelope["expiresAt"]).replace("Z", "+00:00")
        )
        if expires_at <= datetime.now(timezone.utc):
            acknowledge(client, command_id, "REJECTED", "expired")
            return
        if already_processed(command_id):
            acknowledge(
                client,
                command_id,
                "ACKNOWLEDGED",
                "duplicate; previous result retained",
            )
            return

        command = envelope.get("command")
        raw_payload = envelope.get("payload", {})
        if not isinstance(raw_payload, dict):
            raise ValueError("command payload must be a JSON object")

        if command == "ESTOP":
            send_to_esp32("STOP")
            write_mission_request("ESTOP", command_id)
            publish_event(client, "ESTOP_TRIGGERED", "CRITICAL", command_id=command_id)
        elif command == "PAUSE":
            send_to_esp32("STOP")
            write_mission_request("PAUSE", command_id)
            publish_event(client, "PAUSED", "WARNING", command_id=command_id)
        elif command == "RESUME":
            # The mission manager must perform local safety checks before it
            # publishes RESUMED and allows motion.
            write_mission_request("RESUME", command_id)
        elif command == "RETURN_HOME":
            write_mission_request("RETURN_HOME", command_id)
        elif command == "START_MISSION":
            required = {
                "sourceLocationId",
                "destinationLocationId",
                "mapVersion",
                "deliveryId",
            }
            if not required.issubset(raw_payload):
                raise ValueError("START_MISSION payload is incomplete")
            write_mission_request("START_MISSION", command_id, raw_payload)
        else:
            acknowledge(client, command_id, "REJECTED", "unsupported command")
            return

        mark_processed(command_id)
        acknowledge(client, command_id, "ACKNOWLEDGED")
    except Exception as exc:
        try:
            send_to_esp32("STOP")
        except Exception:
            pass
        if command_id:
            acknowledge(client, command_id, "REJECTED", type(exc).__name__)
        publish_event(
            client,
            "BRIDGE_FAULT",
            "ERROR",
            {"reason": type(exc).__name__},
            command_id=command_id,
        )


client = mqtt.Client(
    mqtt.CallbackAPIVersion.VERSION2,
    client_id=f"{ROBOT_ID}-pi",
)
client.username_pw_set(MQTT_USERNAME, MQTT_PASSWORD)
client.tls_set(
    ca_certs=MQTT_CA_FILE,
    cert_reqs=ssl.CERT_REQUIRED,
    tls_version=ssl.PROTOCOL_TLS_CLIENT,
)
client.reconnect_delay_set(min_delay=1, max_delay=30)
client.will_set(
    PRESENCE_TOPIC,
    json.dumps(presence_payload(False), separators=(",", ":")),
    qos=1,
    retain=True,
)
client.on_connect = on_connect
client.on_message = on_message


def request_shutdown(_signum: int, _frame: object) -> None:
    stop_event.set()
    if client.is_connected():
        publish_presence(client, False)
        client.disconnect()


signal.signal(signal.SIGTERM, request_shutdown)
signal.signal(signal.SIGINT, request_shutdown)

publisher_thread = threading.Thread(
    target=background_publisher,
    args=(client,),
    name="robot-state-publisher",
    daemon=True,
)
publisher_thread.start()

try:
    client.connect(MQTT_HOST, MQTT_PORT, keepalive=30)
    client.loop_forever(retry_first_connection=True)
finally:
    stop_event.set()
    publisher_thread.join(timeout=3)
    close_esp32()
    db.close()
