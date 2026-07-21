"""MQTT bridge between EMQX, the Pi mission manager, and the ESP32.

The bridge accepts only expiring mission-level cloud commands. It publishes
presence, state snapshots, acknowledgements, and durable mission events using
the schema consumed by the Supabase ingest-robot-message Edge Function.

Navigation remains a separate local process. That process consumes each JSON
request in the command-inbox directory, writes robot_state.json atomically, and
places durable event JSON files into the event-outbox directory.
"""

from __future__ import annotations

import json
import logging
import math
import os
import signal
import sqlite3
import ssl
import subprocess
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import paho.mqtt.client as mqtt
import serial

from local_store import (
    enqueue_command_request,
    move_file_durable,
    recover_atomic_json_files,
    write_json_atomic,
)
from message_contract import (
    as_uuid,
    event_order_key,
    prepare_event_payload,
    prepare_state_payload,
    validate_robot_id,
)


ROBOT_ID = os.environ.get("ROBOT_ID", "robot-01")
MQTT_HOST = os.environ["MQTT_HOST"]
MQTT_PORT = int(os.environ.get("MQTT_PORT", "8883"))
MQTT_USERNAME = os.environ["MQTT_USERNAME"]
MQTT_PASSWORD = os.environ["MQTT_PASSWORD"]
MQTT_CA_FILE = os.environ.get("MQTT_CA_FILE", "").strip() or None
SERIAL_PORT = os.environ.get("ESP32_SERIAL_PORT", "/dev/ttyUSB0")
ESP32_READY_DELAY_SECONDS = float(
    os.environ.get("ESP32_READY_DELAY_SECONDS", "2")
)
STATE_DIR = Path(os.environ.get("ROBOT_STATE_DIR", "/var/lib/miit-rover"))
FIRMWARE_VERSION = os.environ.get("ROBOT_AGENT_VERSION", "pi-agent-1.2.0")
PRESENCE_INTERVAL_SECONDS = float(os.environ.get("PRESENCE_INTERVAL_SECONDS", "15"))
STATE_INTERVAL_SECONDS = float(os.environ.get("STATE_INTERVAL_SECONDS", "5"))
REQUIRE_TIME_SYNC = os.environ.get("ROBOT_REQUIRE_TIME_SYNC", "true").lower() not in {
    "0",
    "false",
    "no",
}
TIME_SYNC_RETRY_SECONDS = float(os.environ.get("TIME_SYNC_RETRY_SECONDS", "5"))

STATE_DIR.mkdir(parents=True, exist_ok=True)
COMMAND_INBOX_DIR = Path(
    os.environ.get("ROBOT_COMMAND_INBOX", str(STATE_DIR / "command-inbox"))
)
COMMAND_ARCHIVE_DIR = Path(
    os.environ.get("ROBOT_COMMAND_ARCHIVE", str(STATE_DIR / "command-archive"))
)
ROBOT_STATE_FILE = Path(os.environ.get("ROBOT_STATE_FILE", str(STATE_DIR / "robot_state.json")))
EVENT_OUTBOX_DIR = Path(os.environ.get("ROBOT_EVENT_OUTBOX", str(STATE_DIR / "event-outbox")))
EVENT_ARCHIVE_DIR = Path(
    os.environ.get("ROBOT_EVENT_ARCHIVE", str(STATE_DIR / "event-archive"))
)
for directory in (
    COMMAND_INBOX_DIR,
    COMMAND_ARCHIVE_DIR,
    EVENT_OUTBOX_DIR,
    EVENT_ARCHIVE_DIR,
):
    directory.mkdir(parents=True, exist_ok=True)
STATE_MAX_AGE_SECONDS = float(
    os.environ.get(
        "ROBOT_STATE_MAX_AGE_SECONDS",
        str(max(15.0, STATE_INTERVAL_SECONDS * 3)),
    )
)

COMMAND_TOPIC = f"miit/robots/{ROBOT_ID}/commands"
ACK_TOPIC = f"miit/robots/{ROBOT_ID}/acks"
STATE_TOPIC = f"miit/robots/{ROBOT_ID}/state"
EVENT_TOPIC = f"miit/robots/{ROBOT_ID}/events"
PRESENCE_TOPIC = f"miit/robots/{ROBOT_ID}/presence"

logging.basicConfig(
    level=os.environ.get("ROBOT_LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s %(levelname)s %(message)s",
)
logger = logging.getLogger("miit-rover-agent")

validate_robot_id(ROBOT_ID)
if MQTT_USERNAME != ROBOT_ID:
    raise RuntimeError("MQTT_USERNAME must equal ROBOT_ID for broker-to-database identity validation")
if any(
    not math.isfinite(value) or value <= 0
    for value in (
        PRESENCE_INTERVAL_SECONDS,
        STATE_INTERVAL_SECONDS,
        STATE_MAX_AGE_SECONDS,
        TIME_SYNC_RETRY_SECONDS,
        ESP32_READY_DELAY_SECONDS,
    )
):
    raise RuntimeError("publisher intervals must be greater than zero")

recover_atomic_json_files(COMMAND_INBOX_DIR)
recover_atomic_json_files(EVENT_OUTBOX_DIR)

db = sqlite3.connect(STATE_DIR / "commands.db", check_same_thread=False)
db.execute(
    "create table if not exists processed_commands "
    "(id text primary key, processed_at text not null)"
)
db.commit()

serial_lock = threading.Lock()
outbox_lock = threading.Lock()
esp32: serial.Serial | None = None
esp32_ready_at = 0.0
stop_event = threading.Event()
subscription_ready = threading.Event()


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def wait_for_system_time() -> bool:
    if not REQUIRE_TIME_SYNC:
        logger.warning("time_sync_check_disabled")
        return True

    logger.info("time_sync_waiting")
    while not stop_event.is_set():
        try:
            result = subprocess.run(
                ["timedatectl", "show", "-p", "NTPSynchronized", "--value"],
                capture_output=True,
                check=False,
                text=True,
                timeout=3,
            )
            if result.returncode == 0 and result.stdout.strip().lower() == "yes":
                logger.info("time_sync_ready")
                return True
        except (OSError, subprocess.SubprocessError) as exc:
            logger.warning("time_sync_check_failed reason=%s", type(exc).__name__)

        stop_event.wait(TIME_SYNC_RETRY_SECONDS)
    return False


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
    _client: mqtt.Client,
    event_type: str,
    severity: str,
    detail: dict[str, Any] | None = None,
    *,
    delivery_id: str | None = None,
    command_id: str | None = None,
    event_id: str | None = None,
) -> Path:
    payload: dict[str, Any] = {
        "eventId": event_id or str(uuid.uuid4()),
        "type": event_type,
        "severity": severity,
        "at": utc_now(),
        "payload": detail or {},
    }
    if delivery_id:
        payload["deliveryId"] = delivery_id
    if command_id:
        payload["commandId"] = command_id

    event, _changed = prepare_event_payload(payload, robot_id=ROBOT_ID)
    path = EVENT_OUTBOX_DIR / f"{event['eventId']}.json"
    write_json_atomic(path, event)
    logger.info(
        "event_queued type=%s event_id=%s command_id=%s delivery_id=%s",
        event_type,
        event["eventId"],
        command_id or "none",
        delivery_id or "none",
    )
    # The MQTT callbacks run on Paho's network-loop thread. The background
    # publisher performs the QoS wait so callback code never blocks the thread
    # that must receive the PUBACK.
    return path


def publish_event_safely(
    client: mqtt.Client,
    event_type: str,
    severity: str,
    detail: dict[str, Any] | None = None,
    **identifiers: str | None,
) -> Path | None:
    try:
        return publish_event(
            client,
            event_type,
            severity,
            detail,
            delivery_id=identifiers.get("delivery_id"),
            command_id=identifiers.get("command_id"),
            event_id=identifiers.get("event_id"),
        )
    except Exception as exc:
        logger.critical(
            "event_queue_failed type=%s reason=%s",
            event_type,
            type(exc).__name__,
        )
        return None


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
    global esp32, esp32_ready_at
    with serial_lock:
        if esp32 is not None and esp32.is_open:
            return esp32
        esp32 = serial.Serial(SERIAL_PORT, 115200, timeout=1, write_timeout=1)
        esp32_ready_at = time.monotonic() + ESP32_READY_DELAY_SECONDS
        return esp32


def close_esp32() -> None:
    global esp32, esp32_ready_at
    with serial_lock:
        if esp32 is not None:
            try:
                esp32.close()
            finally:
                esp32 = None
                esp32_ready_at = 0.0


def send_to_esp32(command: str) -> None:
    frame: dict[str, object] = {"v": 1, "cmd": command}
    if command != "ESTOP":
        frame["ttlMs"] = 300 if command == "STOP" else 2000
    try:
        link = ensure_esp32()
        ready_delay = max(0.0, esp32_ready_at - time.monotonic())
        if ready_delay and stop_event.wait(ready_delay):
            raise RuntimeError("agent is stopping")
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
    enqueue_command_request(
        COMMAND_INBOX_DIR,
        request_type,
        command_id,
        utc_now(),
        payload,
    )


def publish_state_file(client: mqtt.Client) -> None:
    if not ROBOT_STATE_FILE.exists():
        raise FileNotFoundError(f"state snapshot is missing: {ROBOT_STATE_FILE}")

    state = json.loads(ROBOT_STATE_FILE.read_text(encoding="utf-8"))
    payload = prepare_state_payload(
        state,
        robot_id=ROBOT_ID,
        firmware_version=FIRMWARE_VERSION,
        max_age_seconds=STATE_MAX_AGE_SECONDS,
    )
    client.publish(
        STATE_TOPIC,
        json.dumps(payload, separators=(",", ":")),
        qos=1,
        retain=False,
    )


def prepare_outbox_event(path: Path) -> dict[str, Any]:
    event = json.loads(path.read_text(encoding="utf-8"))
    event, changed = prepare_event_payload(event, robot_id=ROBOT_ID)
    if changed:
        write_json_atomic(path, event)
    return event


def flush_event_outbox(client: mqtt.Client) -> None:
    if not outbox_lock.acquire(blocking=False):
        return
    try:
        pending: list[
            tuple[tuple[datetime, int, str], Path, dict[str, Any]]
        ] = []
        for path in EVENT_OUTBOX_DIR.glob("*.json"):
            try:
                event = prepare_outbox_event(path)
                pending.append(
                    (
                        event_order_key(
                            event,
                            file_mtime_ns=path.stat().st_mtime_ns,
                        ),
                        path,
                        event,
                    )
                )
            except (json.JSONDecodeError, ValueError) as exc:
                logger.error("event_invalid file=%s reason=%s", path.name, exc)
                move_file_durable(path, path.with_suffix(".bad"))

        for _order_key, path, event in sorted(pending):
            try:
                info = client.publish(
                    EVENT_TOPIC,
                    json.dumps(event, separators=(",", ":")),
                    qos=1,
                    retain=False,
                )
                info.wait_for_publish(timeout=5)
                if not info.is_published():
                    logger.warning("event_publish_deferred file=%s", path.name)
                    return

                move_file_durable(path, EVENT_ARCHIVE_DIR / path.name)
                logger.info(
                    "event_broker_accepted type=%s event_id=%s archive=%s",
                    event["type"],
                    event["eventId"],
                    path.name,
                )
            except Exception as exc:
                logger.warning(
                    "event_publish_deferred file=%s reason=%s",
                    path.name,
                    type(exc).__name__,
                )
                return
    finally:
        outbox_lock.release()


def background_publisher(client: mqtt.Client) -> None:
    last_presence = 0.0
    last_state = 0.0
    last_state_error: str | None = None
    while not stop_event.wait(1):
        if not client.is_connected() or not subscription_ready.is_set():
            continue

        now = time.monotonic()
        if now - last_presence >= PRESENCE_INTERVAL_SECONDS:
            publish_presence(client, True)
            last_presence = now
        if now - last_state >= STATE_INTERVAL_SECONDS:
            try:
                publish_state_file(client)
                if last_state_error is not None:
                    logger.info("state_publish_recovered")
                    last_state_error = None
            except Exception as exc:
                error = f"{type(exc).__name__}: {exc}"
                if error != last_state_error:
                    logger.warning("state_publish_skipped reason=%s", error)
                    last_state_error = error
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
        logger.error("mqtt_connection_failed reason=%s", reason_code)
        return
    subscription_ready.clear()
    result, message_id = client.subscribe(COMMAND_TOPIC, qos=1)
    if result != mqtt.MQTT_ERR_SUCCESS:
        logger.error("mqtt_subscribe_request_failed result=%s", result)
        client.disconnect()
        return
    logger.info(
        "mqtt_connected robot_id=%s client_id=%s session_present=%s subscribe_mid=%s",
        ROBOT_ID,
        f"{ROBOT_ID}-pi",
        getattr(_flags, "session_present", False),
        message_id,
    )


def on_subscribe(
    client: mqtt.Client,
    _userdata: object,
    _message_id: int,
    reason_codes: list[mqtt.ReasonCode],
    _properties: mqtt.Properties | None,
) -> None:
    failures = [code for code in reason_codes if code.is_failure]
    if failures:
        logger.critical(
            "mqtt_command_subscription_rejected topic=%s reasons=%s",
            COMMAND_TOPIC,
            ",".join(str(code) for code in failures),
        )
        subscription_ready.clear()
        client.disconnect()
        return

    subscription_ready.set()
    publish_presence(client, True)
    logger.info("mqtt_command_subscription_ready topic=%s", COMMAND_TOPIC)
    try:
        ensure_esp32()
        logger.info("esp32_connected port=%s", SERIAL_PORT)
    except Exception as exc:
        logger.error("esp32_unavailable port=%s reason=%s", SERIAL_PORT, type(exc).__name__)
        publish_event_safely(
            client,
            "ESP32_DISCONNECTED",
            "ERROR",
            {"reason": type(exc).__name__},
        )


def on_disconnect(
    _client: mqtt.Client,
    _userdata: object,
    _disconnect_flags: mqtt.DisconnectFlags,
    reason_code: mqtt.ReasonCode,
    _properties: mqtt.Properties | None,
) -> None:
    subscription_ready.clear()
    if not stop_event.is_set():
        logger.warning("mqtt_disconnected reason=%s", reason_code)


def on_message(
    client: mqtt.Client,
    _userdata: object,
    message: mqtt.MQTTMessage,
) -> None:
    command_id: str | None = None
    delivery_id: str | None = None
    try:
        envelope = json.loads(message.payload)
        if not isinstance(envelope, dict):
            raise ValueError("command envelope must be a JSON object")

        command_id = as_uuid(envelope["commandId"], "commandId")
        if envelope.get("schemaVersion") != 1 or envelope.get("robotId") != ROBOT_ID:
            acknowledge(client, command_id, "REJECTED", "wrong schema or robot")
            return

        expires_at = datetime.fromisoformat(
            str(envelope["expiresAt"]).replace("Z", "+00:00")
        )
        if expires_at.tzinfo is None:
            raise ValueError("expiresAt must include a timezone")
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
            logger.info("command_duplicate command_id=%s", command_id)
            return

        command = envelope.get("command")
        raw_payload = envelope.get("payload", {})
        if not isinstance(raw_payload, dict):
            raise ValueError("command payload must be a JSON object")

        if command == "ESTOP":
            publish_event(
                client,
                "ESTOP_TRIGGERED",
                "CRITICAL",
                {"physicalConfirmation": False, "phase": "requested"},
                command_id=command_id,
            )
            send_to_esp32("ESTOP")
            write_mission_request("ESTOP", command_id)
        elif command == "PAUSE":
            send_to_esp32("STOP")
            write_mission_request("PAUSE", command_id)
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
            delivery_id = as_uuid(raw_payload["deliveryId"], "deliveryId")
            for field in ("sourceLocationId", "destinationLocationId", "mapVersion"):
                if not isinstance(raw_payload[field], str) or not raw_payload[field].strip():
                    raise ValueError(f"{field} must be a non-empty string")
            raw_payload = {**raw_payload, "deliveryId": delivery_id}
            write_mission_request("START_MISSION", command_id, raw_payload)
        else:
            acknowledge(client, command_id, "REJECTED", "unsupported command")
            return

        mark_processed(command_id)
        acknowledge(client, command_id, "ACKNOWLEDGED")
        logger.info(
            "command_acknowledged command=%s command_id=%s delivery_id=%s",
            command,
            command_id,
            delivery_id or "none",
        )
    except Exception as exc:
        try:
            send_to_esp32("STOP")
        except Exception:
            pass
        if command_id:
            acknowledge(client, command_id, "REJECTED", type(exc).__name__)
        logger.error(
            "command_rejected command_id=%s delivery_id=%s reason=%s",
            command_id or "unknown",
            delivery_id or "none",
            type(exc).__name__,
        )
        event_command_id = command_id if delivery_id is not None else None
        publish_event_safely(
            client,
            "BRIDGE_FAULT",
            "ERROR",
            {"reason": type(exc).__name__},
            delivery_id=delivery_id,
            command_id=event_command_id,
        )


client = mqtt.Client(
    mqtt.CallbackAPIVersion.VERSION2,
    client_id=f"{ROBOT_ID}-pi",
    clean_session=False,
    protocol=mqtt.MQTTv311,
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
client.on_subscribe = on_subscribe
client.on_disconnect = on_disconnect
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

try:
    logger.info(
        "agent_starting robot_id=%s version=%s state_file=%s command_inbox=%s event_outbox=%s event_archive=%s",
        ROBOT_ID,
        FIRMWARE_VERSION,
        ROBOT_STATE_FILE,
        COMMAND_INBOX_DIR,
        EVENT_OUTBOX_DIR,
        EVENT_ARCHIVE_DIR,
    )
    if not wait_for_system_time():
        raise RuntimeError("agent stopped before system time synchronized")
    publisher_thread.start()
    client.connect(MQTT_HOST, MQTT_PORT, keepalive=30)
    client.loop_forever(retry_first_connection=True)
finally:
    logger.info("agent_stopping robot_id=%s", ROBOT_ID)
    stop_event.set()
    if publisher_thread.is_alive():
        publisher_thread.join(timeout=3)
    close_esp32()
    db.close()
