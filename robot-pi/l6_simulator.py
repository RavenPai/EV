"""Guarded non-physical MQTT simulator for the isolated L6 cloud test.

This utility is intentionally locked to ``robot-test-l6``. It publishes only
fresh presence/state snapshots, subscribes to that test robot's command topic,
and acknowledges a valid START_MISSION command without producing mission
events or controlling hardware.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import signal
import ssl
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from message_contract import (
    prepare_ack_payload,
    prepare_command_envelope,
    prepare_state_payload,
    validate_command_transport,
)


TEST_ROBOT_ID = "robot-test-l6"
FIRMWARE_VERSION = "l6-nonphysical-simulator-1.0.0"
COMMAND_TOPIC = f"miit/robots/{TEST_ROBOT_ID}/commands"
ACK_TOPIC = f"miit/robots/{TEST_ROBOT_ID}/acks"
STATE_TOPIC = f"miit/robots/{TEST_ROBOT_ID}/state"
PRESENCE_TOPIC = f"miit/robots/{TEST_ROBOT_ID}/presence"

logger = logging.getLogger("miit-l6-simulator")


@dataclass(frozen=True)
class SimulatorConfig:
    host: str
    port: int
    username: str
    password: str
    client_id: str
    ca_file: str | None
    duration_seconds: int


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def assert_test_identity(
    robot_id: str,
    username: str,
    client_id: str,
) -> None:
    if robot_id != TEST_ROBOT_ID:
        raise ValueError("L6 simulator is locked to robot-test-l6")
    if username != TEST_ROBOT_ID:
        raise ValueError("L6 MQTT username must equal robot-test-l6")
    if not client_id.startswith(f"{TEST_ROBOT_ID}-"):
        raise ValueError("L6 MQTT client ID must start with robot-test-l6-")


def build_presence(online: bool, *, at: str | None = None) -> dict[str, Any]:
    return {
        "schemaVersion": 1,
        "robotId": TEST_ROBOT_ID,
        "online": online,
        "at": at or utc_now(),
        "firmwareVersion": FIRMWARE_VERSION,
    }


def build_state(*, at: str | None = None) -> dict[str, Any]:
    observed_at = at or utc_now()
    return prepare_state_payload(
        {
            "status": "ONLINE",
            "mode": "IDLE",
            "battery": 100,
            "signal": 100,
            "speedMps": 0,
            "currentDeliveryId": None,
            "locationId": "loc-home",
            "lidar": "OK",
            "camera": "OK",
            "esp32": "OK",
            "motorTempC": 25,
            "at": observed_at,
        },
        robot_id=TEST_ROBOT_ID,
        firmware_version=FIRMWARE_VERSION,
        max_age_seconds=30,
    )


def validate_test_command(
    payload: bytes,
    *,
    topic: str,
    qos: int,
    retain: bool,
) -> dict[str, Any]:
    validate_command_transport(
        topic=topic,
        expected_topic=COMMAND_TOPIC,
        qos=qos,
        retain=retain,
    )
    if len(payload) > 32 * 1024:
        raise ValueError("command exceeds the 32 KiB test limit")
    try:
        decoded = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("command is not valid UTF-8 JSON") from exc
    envelope, _issued_at, expires_at = prepare_command_envelope(
        decoded,
        robot_id=TEST_ROBOT_ID,
    )
    if envelope["command"] != "START_MISSION":
        raise ValueError("L6 simulator accepts START_MISSION only")
    if expires_at <= datetime.now(timezone.utc):
        raise ValueError("command has expired")
    return envelope


def load_config(duration_seconds: int) -> SimulatorConfig:
    host = os.environ.get("L6_MQTT_HOST", "").strip()
    username = os.environ.get("L6_MQTT_USERNAME", "").strip()
    password = os.environ.get("L6_MQTT_PASSWORD", "")
    client_id = os.environ.get(
        "L6_MQTT_CLIENT_ID",
        f"{TEST_ROBOT_ID}-subscriber",
    ).strip()
    assert_test_identity(TEST_ROBOT_ID, username, client_id)
    if not host:
        raise ValueError("L6_MQTT_HOST is required")
    if not password:
        raise ValueError("L6_MQTT_PASSWORD is required")
    port = int(os.environ.get("L6_MQTT_PORT", "8883"))
    if not 1 <= port <= 65535:
        raise ValueError("L6_MQTT_PORT is invalid")
    ca_file = os.environ.get("L6_MQTT_CA_FILE", "").strip() or None
    if duration_seconds < 30 or duration_seconds > 1800:
        raise ValueError("duration must be between 30 and 1800 seconds")
    return SimulatorConfig(
        host=host,
        port=port,
        username=username,
        password=password,
        client_id=client_id,
        ca_file=ca_file,
        duration_seconds=duration_seconds,
    )


def run_simulator(config: SimulatorConfig) -> int:
    import paho.mqtt.client as mqtt

    assert_test_identity(TEST_ROBOT_ID, config.username, config.client_id)
    stop_event = threading.Event()
    subscription_ready = threading.Event()
    processed_commands: set[str] = set()

    def request_stop(_signum: int, _frame: object) -> None:
        stop_event.set()

    def publish_json(
        client: mqtt.Client,
        topic: str,
        payload: dict[str, Any],
        *,
        retain: bool = False,
    ) -> None:
        info = client.publish(
            topic,
            json.dumps(payload, separators=(",", ":")),
            qos=1,
            retain=retain,
        )
        if info.rc != mqtt.MQTT_ERR_SUCCESS:
            raise RuntimeError(f"MQTT publish request failed with code {info.rc}")

    def on_connect(
        client: mqtt.Client,
        _userdata: object,
        _flags: mqtt.ConnectFlags,
        reason_code: mqtt.ReasonCode,
        _properties: mqtt.Properties | None,
    ) -> None:
        if reason_code != 0:
            logger.error("mqtt_connect_rejected reason=%s", reason_code)
            stop_event.set()
            return
        result, _message_id = client.subscribe(COMMAND_TOPIC, qos=1)
        if result != mqtt.MQTT_ERR_SUCCESS:
            logger.error("mqtt_subscribe_request_failed result=%s", result)
            stop_event.set()

    def on_subscribe(
        client: mqtt.Client,
        _userdata: object,
        _message_id: int,
        reason_codes: list[mqtt.ReasonCode],
        _properties: mqtt.Properties | None,
    ) -> None:
        if any(code.is_failure for code in reason_codes):
            logger.error(
                "mqtt_subscription_rejected reasons=%s",
                ",".join(str(code) for code in reason_codes),
            )
            stop_event.set()
            return
        subscription_ready.set()
        publish_json(client, PRESENCE_TOPIC, build_presence(True), retain=True)
        publish_json(client, STATE_TOPIC, build_state())
        logger.info("simulator_ready robot_id=%s", TEST_ROBOT_ID)

    def on_message(
        client: mqtt.Client,
        _userdata: object,
        message: mqtt.MQTTMessage,
    ) -> None:
        try:
            envelope = validate_test_command(
                message.payload,
                topic=message.topic,
                qos=message.qos,
                retain=message.retain,
            )
            command_id = envelope["commandId"]
            if command_id in processed_commands:
                logger.info("duplicate_command_ignored command_id=%s", command_id)
                return
            processed_commands.add(command_id)
            acknowledgement = prepare_ack_payload(
                robot_id=TEST_ROBOT_ID,
                command_id=command_id,
                status="ACKNOWLEDGED",
                reason="Non-physical L6 simulator received command",
            )
            publish_json(client, ACK_TOPIC, acknowledgement)
            logger.info(
                "test_command_acknowledged command_id=%s delivery_id=%s",
                command_id,
                envelope["payload"]["deliveryId"],
            )
        except Exception as exc:
            logger.warning(
                "test_command_rejected reason=%s detail=%s",
                type(exc).__name__,
                str(exc),
            )

    signal.signal(signal.SIGINT, request_stop)
    signal.signal(signal.SIGTERM, request_stop)

    client = mqtt.Client(
        mqtt.CallbackAPIVersion.VERSION2,
        client_id=config.client_id,
        protocol=mqtt.MQTTv5,
    )
    client.username_pw_set(config.username, config.password)
    client.tls_set(
        ca_certs=config.ca_file,
        cert_reqs=ssl.CERT_REQUIRED,
    )
    client.will_set(
        PRESENCE_TOPIC,
        json.dumps(build_presence(False), separators=(",", ":")),
        qos=1,
        retain=True,
    )
    client.on_connect = on_connect
    client.on_subscribe = on_subscribe
    client.on_message = on_message
    client.reconnect_delay_set(min_delay=1, max_delay=15)

    deadline = time.monotonic() + config.duration_seconds
    last_presence = 0.0
    last_state = 0.0
    exit_code = 0
    try:
        client.connect(config.host, config.port, keepalive=30)
        client.loop_start()
        if not subscription_ready.wait(15):
            raise RuntimeError("command subscription was not accepted")
        while not stop_event.wait(1):
            now = time.monotonic()
            if now >= deadline:
                break
            if not client.is_connected() or not subscription_ready.is_set():
                continue
            if now - last_presence >= 15:
                publish_json(
                    client,
                    PRESENCE_TOPIC,
                    build_presence(True),
                    retain=True,
                )
                last_presence = now
            if now - last_state >= 5:
                publish_json(client, STATE_TOPIC, build_state())
                last_state = now
    except Exception as exc:
        logger.error(
            "simulator_failed reason=%s detail=%s",
            type(exc).__name__,
            str(exc),
        )
        exit_code = 1
    finally:
        if client.is_connected():
            try:
                offline = client.publish(
                    PRESENCE_TOPIC,
                    json.dumps(build_presence(False), separators=(",", ":")),
                    qos=1,
                    retain=True,
                )
                offline.wait_for_publish(timeout=5)
            except Exception:
                logger.warning("offline_presence_publish_failed")
        client.disconnect()
        client.loop_stop()
        logger.info("simulator_stopped robot_id=%s", TEST_ROBOT_ID)
    return exit_code


def run_self_test() -> int:
    assert_test_identity(
        TEST_ROBOT_ID,
        TEST_ROBOT_ID,
        f"{TEST_ROBOT_ID}-subscriber",
    )
    state = build_state()
    assert state["robotId"] == TEST_ROBOT_ID
    assert state["status"] == "ONLINE"
    assert state["mode"] == "IDLE"
    assert all(state[field] == "OK" for field in ("lidar", "camera", "esp32"))
    presence = build_presence(True)
    assert presence["online"] is True

    issued_at = datetime.now(timezone.utc)
    envelope = {
        "schemaVersion": 1,
        "commandId": "22222222-2222-4222-8222-222222222222",
        "robotId": TEST_ROBOT_ID,
        "command": "START_MISSION",
        "payload": {
            "sourceLocationId": "loc-fcs",
            "destinationLocationId": "loc-data",
            "mapVersion": "miit-campus-v1",
            "deliveryId": "11111111-1111-4111-8111-111111111111",
        },
        "issuedAt": issued_at.isoformat(),
        "expiresAt": (issued_at + timedelta(minutes=5)).isoformat(),
    }
    validated = validate_test_command(
        json.dumps(envelope).encode(),
        topic=COMMAND_TOPIC,
        qos=1,
        retain=False,
    )
    assert validated["commandId"] == envelope["commandId"]
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run the guarded non-physical robot-test-l6 MQTT simulator",
    )
    parser.add_argument(
        "--duration",
        type=int,
        default=900,
        help="Automatic shutdown time in seconds (30-1800, default: 900)",
    )
    parser.add_argument(
        "--self-test",
        action="store_true",
        help="Validate payload/identity guards without connecting to MQTT",
    )
    args = parser.parse_args()
    logging.basicConfig(
        level=os.environ.get("L6_LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s %(message)s",
    )
    if args.self_test:
        return run_self_test()
    return run_simulator(load_config(args.duration))


if __name__ == "__main__":
    raise SystemExit(main())
