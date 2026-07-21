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
import hashlib
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
    command_event_id,
    event_order_key,
    parse_timestamp,
    prepare_ack_payload,
    prepare_command_envelope,
    prepare_event_payload,
    prepare_state_payload,
    validate_command_transport,
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
FIRMWARE_VERSION = os.environ.get("ROBOT_AGENT_VERSION", "pi-agent-1.3.0")
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
ACK_OUTBOX_DIR = Path(
    os.environ.get("ROBOT_ACK_OUTBOX", str(STATE_DIR / "ack-outbox"))
)
ACK_ARCHIVE_DIR = Path(
    os.environ.get("ROBOT_ACK_ARCHIVE", str(STATE_DIR / "ack-archive"))
)
for directory in (
    COMMAND_INBOX_DIR,
    COMMAND_ARCHIVE_DIR,
    EVENT_OUTBOX_DIR,
    EVENT_ARCHIVE_DIR,
    ACK_OUTBOX_DIR,
    ACK_ARCHIVE_DIR,
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


class CommandReplayConflict(ValueError):
    """A previously handled command ID was reused with different content."""

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
recover_atomic_json_files(ACK_OUTBOX_DIR)

db = sqlite3.connect(STATE_DIR / "commands.db", check_same_thread=False)
db.execute(
    "create table if not exists processed_commands "
    "(id text primary key, processed_at text not null, "
    "outcome_status text not null default 'ACKNOWLEDGED', "
    "outcome_reason text not null default '', payload_hash text)"
)
processed_columns = {
    row[1] for row in db.execute("pragma table_info(processed_commands)").fetchall()
}
for column, definition in (
    ("outcome_status", "text not null default 'ACKNOWLEDGED'"),
    ("outcome_reason", "text not null default ''"),
    ("payload_hash", "text"),
):
    if column not in processed_columns:
        db.execute(f"alter table processed_commands add column {column} {definition}")
db.commit()

serial_lock = threading.Lock()
outbox_lock = threading.Lock()
ack_outbox_lock = threading.Lock()
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
    _client: mqtt.Client,
    command_id: str,
    status: str,
    reason: str = "",
    at: str | None = None,
) -> Path:
    payload = prepare_ack_payload(
        robot_id=ROBOT_ID,
        command_id=command_id,
        status=status,
        reason=reason,
        at=at,
    )
    filename = f"{payload['commandId']}-{payload['status'].lower()}.json"
    path = ACK_OUTBOX_DIR / filename
    archived_path = ACK_ARCHIVE_DIR / filename
    for existing_path in (path, archived_path):
        if existing_path.exists():
            existing = prepare_outbox_ack(existing_path)
            if existing != payload:
                differing_fields = sorted(
                    key
                    for key in set(existing) | set(payload)
                    if existing.get(key) != payload.get(key)
                )
                raise ValueError(
                    "acknowledgement content conflicts with existing file "
                    f"fields: {', '.join(differing_fields)}"
                )
            return existing_path
    write_json_atomic(path, payload)
    logger.info(
        "ack_queued command_id=%s status=%s file=%s",
        command_id,
        status,
        path.name,
    )
    return path


def acknowledge_incoming_message(
    client: mqtt.Client,
    message: mqtt.MQTTMessage,
) -> None:
    if message.qos == 0:
        return
    result = client.ack(message.mid, message.qos)
    if result != mqtt.MQTT_ERR_SUCCESS:
        raise RuntimeError(f"MQTT inbound acknowledgement failed: {result}")


def persist_outcome_and_acknowledge_incoming(
    client: mqtt.Client,
    message: mqtt.MQTTMessage,
    command_id: str,
    status: str,
    reason: str = "",
    occurred_at: str | None = None,
) -> None:
    # Queue the cloud acknowledgement before acknowledging the inbound QoS
    # message. A crash can then only cause a safe duplicate, not a lost result.
    acknowledge(client, command_id, status, reason, occurred_at)
    acknowledge_incoming_message(client, message)


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
    normalized_event_id = as_uuid(event_id, "eventId") if event_id else None
    expected_detail = detail or {}
    if normalized_event_id:
        filename = f"{normalized_event_id}.json"
        for existing_path in (
            EVENT_OUTBOX_DIR / filename,
            EVENT_ARCHIVE_DIR / filename,
        ):
            if existing_path.exists():
                existing = prepare_outbox_event(existing_path)
                expected_identity = {
                    "eventId": normalized_event_id,
                    "type": event_type,
                    "severity": severity,
                    "deliveryId": delivery_id,
                    "commandId": command_id,
                    "payload": expected_detail,
                }
                existing_identity = {
                    "eventId": existing["eventId"],
                    "type": existing["type"],
                    "severity": existing["severity"],
                    "deliveryId": existing.get("deliveryId"),
                    "commandId": existing.get("commandId"),
                    "payload": existing["payload"],
                }
                if existing_identity != expected_identity:
                    raise ValueError("eventId conflicts with existing event content")
                logger.info(
                    "event_already_durable type=%s event_id=%s file=%s",
                    event_type,
                    normalized_event_id,
                    existing_path.name,
                )
                return existing_path

    payload: dict[str, Any] = {
        "eventId": normalized_event_id or str(uuid.uuid4()),
        "type": event_type,
        "severity": severity,
        "at": utc_now(),
        "payload": expected_detail,
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


def command_fingerprint(envelope: dict[str, Any]) -> str:
    encoded = json.dumps(
        envelope,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def processed_outcome(
    command_id: str,
    payload_hash: str,
) -> tuple[str, str, str] | None:
    row = db.execute(
        "select outcome_status, outcome_reason, payload_hash, processed_at "
        "from processed_commands where id = ?",
        (command_id,),
    ).fetchone()
    if row is None:
        return None
    status, reason, stored_hash, processed_at = row
    if stored_hash is not None and stored_hash != payload_hash:
        raise CommandReplayConflict("commandId was replayed with different content")
    return (
        status or "ACKNOWLEDGED",
        reason or "",
        processed_at,
    )


def mark_processed(
    command_id: str,
    status: str,
    reason: str,
    payload_hash: str,
) -> str:
    processed_at = utc_now()
    cursor = db.execute(
        "insert or ignore into processed_commands "
        "(id, processed_at, outcome_status, outcome_reason, payload_hash) "
        "values (?, ?, ?, ?, ?)",
        (command_id, processed_at, status, reason[:240], payload_hash),
    )
    db.commit()
    if cursor.rowcount == 1:
        return processed_at

    existing = processed_outcome(command_id, payload_hash)
    if existing is None:
        raise RuntimeError("processed command outcome was not persisted")
    return existing[2]


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
    requested_at: str,
    payload: dict[str, Any] | None = None,
) -> None:
    enqueue_command_request(
        COMMAND_INBOX_DIR,
        request_type,
        command_id,
        requested_at,
        payload,
        COMMAND_ARCHIVE_DIR,
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


def prepare_outbox_ack(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("ack outbox file must contain a JSON object")
    normalized = prepare_ack_payload(
        robot_id=ROBOT_ID,
        command_id=payload.get("commandId"),
        status=payload.get("status"),
        reason=payload.get("reason", ""),
        at=payload.get("at"),
    )
    if normalized != payload:
        write_json_atomic(path, normalized)
    return normalized


def flush_ack_outbox(client: mqtt.Client) -> None:
    if not ack_outbox_lock.acquire(blocking=False):
        return
    try:
        pending: list[tuple[tuple[datetime, int, str], Path, dict[str, Any]]] = []
        for path in ACK_OUTBOX_DIR.glob("*.json"):
            try:
                payload = prepare_outbox_ack(path)
                pending.append(
                    (
                        (
                            parse_timestamp(payload["at"]),
                            path.stat().st_mtime_ns,
                            path.name,
                        ),
                        path,
                        payload,
                    )
                )
            except (UnicodeError, RecursionError, json.JSONDecodeError, ValueError) as exc:
                logger.error("ack_invalid file=%s reason=%s", path.name, exc)
                move_file_durable(path, path.with_suffix(".bad"))

        for _order_key, path, payload in sorted(pending):
            try:
                info = client.publish(
                    ACK_TOPIC,
                    json.dumps(payload, separators=(",", ":")),
                    qos=1,
                    retain=False,
                )
                info.wait_for_publish(timeout=5)
                if not info.is_published():
                    logger.warning("ack_publish_deferred file=%s", path.name)
                    return

                move_file_durable(path, ACK_ARCHIVE_DIR / path.name)
                logger.info(
                    "ack_broker_accepted command_id=%s status=%s archive=%s",
                    payload["commandId"],
                    payload["status"],
                    path.name,
                )
            except Exception as exc:
                logger.warning(
                    "ack_publish_deferred file=%s reason=%s",
                    path.name,
                    type(exc).__name__,
                )
                return
    finally:
        ack_outbox_lock.release()


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
            except (UnicodeError, RecursionError, json.JSONDecodeError, ValueError) as exc:
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
            try:
                publish_presence(client, True)
            except Exception as exc:
                logger.error(
                    "presence_publish_failed reason=%s",
                    type(exc).__name__,
                )
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
        for name, flush in (
            ("ack", flush_ack_outbox),
            ("event", flush_event_outbox),
        ):
            try:
                flush(client)
            except Exception as exc:
                # Keep the periodic publisher alive. A bad file or transient
                # filesystem error must not silently stop every heartbeat.
                logger.exception(
                    "%s_outbox_flush_failed reason=%s",
                    name,
                    type(exc).__name__,
                )


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
    payload_hash: str | None = None
    outcome_status: str | None = None
    outcome_reason = ""
    outcome_at: str | None = None
    command: str | None = None
    validated = False
    processing_started = False
    handoff_durable = False
    outcome_durable = False
    fault_persisted = False
    fault_only_ack_allowed = False
    try:
        if len(message.payload) > 32 * 1024:
            raise ValueError("command payload exceeds 32768 bytes")
        envelope = json.loads(message.payload)
        if not isinstance(envelope, dict):
            raise ValueError("command envelope must be a JSON object")

        if "commandId" in envelope:
            command_id = as_uuid(envelope["commandId"], "commandId")
        payload_hash = command_fingerprint(envelope)
        normalized, issued_at, expires_at = prepare_command_envelope(
            envelope,
            robot_id=ROBOT_ID,
        )
        command_id = normalized["commandId"]
        payload_hash = command_fingerprint(normalized)
        command = normalized["command"]
        raw_payload = normalized["payload"]
        if command == "START_MISSION":
            delivery_id = raw_payload["deliveryId"]
        validated = True

        previous = processed_outcome(command_id, payload_hash)
        if previous is not None:
            outcome_status, outcome_reason, outcome_at = previous
            outcome_durable = True
            logger.info("command_duplicate command_id=%s", command_id)
        elif (
            message.topic != COMMAND_TOPIC
            or message.qos != 1
            or bool(message.retain)
        ):
            try:
                validate_command_transport(
                    topic=message.topic,
                    expected_topic=COMMAND_TOPIC,
                    qos=message.qos,
                    retain=message.retain,
                )
            except ValueError as transport_error:
                outcome_status = "REJECTED"
                outcome_reason = str(transport_error)
                outcome_at = mark_processed(
                    command_id,
                    outcome_status,
                    outcome_reason,
                    payload_hash,
                )
                outcome_durable = True
        elif expires_at <= datetime.now(timezone.utc):
            outcome_status = "REJECTED"
            outcome_reason = "expired"
            outcome_at = mark_processed(
                command_id,
                outcome_status,
                outcome_reason,
                payload_hash,
            )
            outcome_durable = True
        else:
            processing_started = True
            requested_at = issued_at.isoformat()
            if command == "ESTOP":
                # Physical stopping takes priority over disk-backed audit work.
                send_to_esp32("ESTOP")
                publish_event(
                    client,
                    "ESTOP_TRIGGERED",
                    "CRITICAL",
                    {"physicalConfirmation": False, "phase": "requested"},
                    command_id=command_id,
                    event_id=command_event_id(command_id, "ESTOP_TRIGGERED"),
                )
                write_mission_request(
                    "ESTOP", command_id, requested_at, raw_payload
                )
            elif command == "PAUSE":
                send_to_esp32("STOP")
                write_mission_request(
                    "PAUSE", command_id, requested_at, raw_payload
                )
            elif command == "RESUME":
                # The mission manager must perform local safety checks before
                # publishing RESUMED and allowing the database latch to clear.
                write_mission_request(
                    "RESUME", command_id, requested_at, raw_payload
                )
            elif command == "RETURN_HOME":
                write_mission_request(
                    "RETURN_HOME", command_id, requested_at, raw_payload
                )
            elif command == "START_MISSION":
                write_mission_request(
                    "START_MISSION",
                    command_id,
                    requested_at,
                    raw_payload,
                )
            else:  # prepare_command_envelope rejects this before side effects.
                raise ValueError("unsupported command")

            handoff_durable = True
            outcome_status = "ACKNOWLEDGED"
            outcome_at = mark_processed(
                command_id,
                outcome_status,
                outcome_reason,
                payload_hash,
            )
            outcome_durable = True
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

        if isinstance(exc, CommandReplayConflict):
            # Never replace an earlier accepted result with REJECTED merely
            # because the same identifier was reused for different content.
            # A durable fault event is sufficient to stop broker redelivery.
            outcome_status = None
            outcome_reason = str(exc)
            outcome_at = None
            fault_only_ack_allowed = True
        elif handoff_durable:
            outcome_status = "ACKNOWLEDGED"
            outcome_reason = "accepted; acknowledgement recovered"
        elif processing_started:
            outcome_status = "FAILED"
            outcome_reason = type(exc).__name__
        else:
            outcome_status = "REJECTED"
            outcome_reason = type(exc).__name__
            fault_only_ack_allowed = not validated

        if command_id and payload_hash and not isinstance(exc, CommandReplayConflict):
            try:
                previous = processed_outcome(command_id, payload_hash)
                if previous is not None:
                    outcome_status, outcome_reason, outcome_at = previous
                    outcome_durable = True
                else:
                    outcome_at = mark_processed(
                        command_id,
                        outcome_status,
                        outcome_reason,
                        payload_hash,
                    )
                    outcome_durable = True
            except Exception as persistence_error:
                logger.critical(
                    "command_outcome_store_failed command_id=%s reason=%s",
                    command_id,
                    type(persistence_error).__name__,
                )
        logger.error(
            "command_rejected command_id=%s delivery_id=%s reason=%s",
            command_id or "unknown",
            delivery_id or "none",
            type(exc).__name__,
        )
        fault_persisted = publish_event_safely(
            client,
            "BRIDGE_FAULT",
            "ERROR",
            {"reason": type(exc).__name__},
            delivery_id=delivery_id,
            command_id=command_id if validated else None,
        ) is not None

    try:
        if command_id and outcome_status and outcome_durable:
            persist_outcome_and_acknowledge_incoming(
                client,
                message,
                command_id,
                outcome_status,
                outcome_reason,
                outcome_at,
            )
        elif fault_persisted and fault_only_ack_allowed:
            acknowledge_incoming_message(client, message)
        else:
            logger.critical(
                "command_outcome_not_durable mqtt_mid=%s; broker redelivery retained",
                message.mid,
            )
    except Exception as exc:
        logger.critical(
            "command_ack_queue_failed command_id=%s mqtt_mid=%s reason=%s",
            command_id or "unknown",
            message.mid,
            type(exc).__name__,
        )


client: mqtt.Client | None = None


def build_mqtt_client() -> mqtt.Client:
    mqtt_client = mqtt.Client(
        mqtt.CallbackAPIVersion.VERSION2,
        client_id=f"{ROBOT_ID}-pi",
        clean_session=False,
        protocol=mqtt.MQTTv311,
    )
    mqtt_client.manual_ack_set(True)
    mqtt_client.username_pw_set(MQTT_USERNAME, MQTT_PASSWORD)
    mqtt_client.tls_set(
        ca_certs=MQTT_CA_FILE,
        cert_reqs=ssl.CERT_REQUIRED,
        tls_version=ssl.PROTOCOL_TLS_CLIENT,
    )
    mqtt_client.reconnect_delay_set(min_delay=1, max_delay=30)
    mqtt_client.will_set(
        PRESENCE_TOPIC,
        json.dumps(presence_payload(False), separators=(",", ":")),
        qos=1,
        retain=True,
    )
    mqtt_client.on_connect = on_connect
    mqtt_client.on_subscribe = on_subscribe
    mqtt_client.on_disconnect = on_disconnect
    mqtt_client.on_message = on_message
    return mqtt_client


def request_shutdown(_signum: int, _frame: object) -> None:
    stop_event.set()
    active_client = client
    if active_client is not None and active_client.is_connected():
        publish_presence(active_client, False)
        active_client.disconnect()


def main() -> None:
    global client

    client = build_mqtt_client()
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
            "agent_starting robot_id=%s version=%s state_file=%s command_inbox=%s ack_outbox=%s event_outbox=%s event_archive=%s",
            ROBOT_ID,
            FIRMWARE_VERSION,
            ROBOT_STATE_FILE,
            COMMAND_INBOX_DIR,
            ACK_OUTBOX_DIR,
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


if __name__ == "__main__":
    main()
