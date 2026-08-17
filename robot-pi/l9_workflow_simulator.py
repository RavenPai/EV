"""One-run non-physical MQTT workflow simulator for the L9 cloud check.

The simulator is hard-locked to ``robot-test-l6``. It publishes fresh
readiness, waits for exactly one non-retained QoS 1 ``START_MISSION`` command,
publishes its ACK, and then publishes exactly one linked ``MISSION_STARTED``
event. It never addresses the physical robot, a motor, or a serial device.
"""

from __future__ import annotations

import argparse
import json
import logging
import queue
import signal
import ssl
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from l6_simulator import (
    ACK_TOPIC,
    COMMAND_TOPIC,
    FIRMWARE_VERSION,
    PRESENCE_TOPIC,
    STATE_TOPIC,
    TEST_ROBOT_ID,
    build_presence,
    build_state,
    validate_test_command,
)
from l9_lifecycle_probe import (
    EVENT_TOPIC,
    ProbeConfig,
    assert_test_identity,
    build_lifecycle_event,
    load_config,
)
from message_contract import (
    command_event_id,
    parse_timestamp,
    prepare_ack_payload,
    prepare_state_payload,
)


logger = logging.getLogger("miit-l9-workflow-simulator")
DEFAULT_DURATION_SECONDS = 900
MAX_COMMAND_AGE_SECONDS = 30
MIN_COMMAND_REMAINING_SECONDS = 30
WORKFLOW_CLIENT_ID = f"{TEST_ROBOT_ID}-subscriber"


def assert_workflow_identity(config: ProbeConfig) -> None:
    """Require the exact Client-ID ACL already reserved for the test runner."""

    assert_test_identity(config.username, config.client_id)
    if config.client_id != WORKFLOW_CLIENT_ID:
        raise ValueError(
            f"L9 workflow MQTT client ID must equal {WORKFLOW_CLIENT_ID}"
        )


def build_linked_messages(
    envelope: dict[str, Any],
    *,
    observed_at: datetime | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Build an ACK and MISSION_STARTED event from one validated command."""

    command_id = envelope["commandId"]
    delivery_id = envelope["payload"]["deliveryId"]
    reference = observed_at or datetime.now(timezone.utc)
    acknowledgement = prepare_ack_payload(
        robot_id=TEST_ROBOT_ID,
        command_id=command_id,
        status="ACKNOWLEDGED",
        reason="Non-physical L9 workflow simulator received command",
        now=reference,
    )
    event = build_lifecycle_event(
        event_type="MISSION_STARTED",
        delivery_id=delivery_id,
        command_id=command_id,
        event_id=command_event_id(command_id, "MISSION_STARTED"),
        at=reference,
        detail={"runner": "l9-workflow-simulator"},
    )
    return acknowledgement, event


def validate_expected_command(
    envelope: dict[str, Any],
    *,
    expected_delivery_id: str,
    now: datetime | None = None,
) -> None:
    """Reject an unintended, old, or nearly expired isolated dispatch."""

    reference = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    expected = str(uuid.UUID(expected_delivery_id))
    if envelope["payload"]["deliveryId"] != expected:
        raise ValueError("START_MISSION does not match the expected test delivery")
    issued_at = parse_timestamp(envelope["issuedAt"], now=reference)
    expires_at = parse_timestamp(envelope["expiresAt"], now=reference)
    command_age = (reference - issued_at).total_seconds()
    remaining = (expires_at - reference).total_seconds()
    if command_age < -5 or command_age > MAX_COMMAND_AGE_SECONDS:
        raise ValueError("START_MISSION is not a fresh test command")
    if remaining < MIN_COMMAND_REMAINING_SECONDS:
        raise ValueError("START_MISSION is too close to expiration")


def build_active_state(
    *,
    delivery_id: str,
    observed_at: datetime,
) -> dict[str, Any]:
    """Build a post-event state newer than every readiness snapshot."""

    return prepare_state_payload(
        {
            "status": "BUSY",
            "mode": "AUTO",
            "battery": 100,
            "signal": 100,
            "speedMps": 0,
            "currentDeliveryId": delivery_id,
            "locationId": "loc-home",
            "lidar": "OK",
            "camera": "OK",
            "esp32": "OK",
            "motorTempC": 25,
            "at": observed_at.isoformat(),
        },
        robot_id=TEST_ROBOT_ID,
        firmware_version=FIRMWARE_VERSION,
        now=observed_at,
        max_age_seconds=30,
    )


def run_workflow(
    config: ProbeConfig,
    duration_seconds: int,
    expected_delivery_id: str,
) -> int:
    """Run one isolated readiness -> command -> ACK -> event workflow."""

    import paho.mqtt.client as mqtt

    assert_workflow_identity(config)
    if duration_seconds < 30 or duration_seconds > 1800:
        raise ValueError("duration must be between 30 and 1800 seconds")

    subscription_ready = threading.Event()
    stop_requested = threading.Event()
    command_received = threading.Event()
    failures: list[str] = []
    readiness_queue: queue.Queue[list[Any]] = queue.Queue(maxsize=1)
    command_queue: queue.Queue[tuple[dict[str, Any], datetime]] = queue.Queue(
        maxsize=1
    )
    accepted_command_id: list[str] = []

    def publish_json(
        client: mqtt.Client,
        topic: str,
        payload: dict[str, Any],
        *,
        retain: bool = False,
    ) -> Any:
        info = client.publish(
            topic,
            json.dumps(payload, separators=(",", ":")),
            qos=1,
            retain=retain,
        )
        if info.rc != mqtt.MQTT_ERR_SUCCESS:
            raise RuntimeError(
                f"MQTT publish request failed for {topic} with code {info.rc}"
            )
        return info

    def request_stop(_signum: int, _frame: object) -> None:
        stop_requested.set()

    def on_connect(
        client: mqtt.Client,
        _userdata: object,
        _flags: mqtt.ConnectFlags,
        reason_code: mqtt.ReasonCode,
        _properties: mqtt.Properties | None,
    ) -> None:
        if reason_code.is_failure:
            failures.append(f"MQTT connection rejected: {reason_code}")
            stop_requested.set()
            return
        result, _message_id = client.subscribe(COMMAND_TOPIC, qos=1)
        if result != mqtt.MQTT_ERR_SUCCESS:
            failures.append(
                f"MQTT subscription request failed with code {result}"
            )
            stop_requested.set()

    def on_subscribe(
        client: mqtt.Client,
        _userdata: object,
        _message_id: int,
        reason_codes: list[mqtt.ReasonCode],
        _properties: mqtt.Properties | None,
    ) -> None:
        if any(code.is_failure for code in reason_codes):
            failures.append(
                "MQTT command subscription was rejected: " +
                ",".join(str(code) for code in reason_codes)
            )
            stop_requested.set()
            return
        try:
            readiness_queue.put_nowait(
                [
                    publish_json(
                        client,
                        PRESENCE_TOPIC,
                        build_presence(True),
                        retain=True,
                    ),
                    publish_json(client, STATE_TOPIC, build_state()),
                ]
            )
            subscription_ready.set()
        except Exception as exc:
            failures.append(f"Readiness publish failed: {type(exc).__name__}")
            stop_requested.set()

    def on_message(
        _client: mqtt.Client,
        _userdata: object,
        message: mqtt.MQTTMessage,
    ) -> None:
        if command_received.is_set():
            logger.info(
                "additional_command_ignored accepted_command_id=%s",
                accepted_command_id[0] if accepted_command_id else "pending",
            )
            return
        try:
            envelope = validate_test_command(
                message.payload,
                topic=message.topic,
                qos=message.qos,
                retain=message.retain,
            )
            reference = datetime.now(timezone.utc)
            validate_expected_command(
                envelope,
                expected_delivery_id=expected_delivery_id,
                now=reference,
            )
            command_received.set()
            command_queue.put_nowait((envelope, reference))
            logger.info(
                "l9_workflow_command_accepted command_id=%s delivery_id=%s",
                envelope["commandId"],
                envelope["payload"]["deliveryId"],
            )
        except Exception as exc:
            logger.warning(
                "l9_workflow_command_rejected reason=%s detail=%s",
                type(exc).__name__,
                str(exc),
            )

    def on_publish(
        _client: mqtt.Client,
        _userdata: object,
        _message_id: int,
        reason_code: mqtt.ReasonCode,
        _properties: mqtt.Properties | None,
    ) -> None:
        if reason_code.is_failure:
            failures.append(f"MQTT publish rejected: {reason_code}")
            stop_requested.set()

    def wait_for_publications(
        publications: list[Any],
        *,
        description: str,
    ) -> None:
        for publication in publications:
            publication.wait_for_publish(timeout=15)
            if not publication.is_published():
                raise RuntimeError(f"{description} PUBACK timed out")
        if failures:
            raise RuntimeError(failures[0])

    signal.signal(signal.SIGINT, request_stop)
    signal.signal(signal.SIGTERM, request_stop)

    client = mqtt.Client(
        mqtt.CallbackAPIVersion.VERSION2,
        client_id=config.client_id,
        protocol=mqtt.MQTTv5,
    )
    client.username_pw_set(config.username, config.password)
    client.tls_set(ca_certs=config.ca_file, cert_reqs=ssl.CERT_REQUIRED)
    client.will_set(
        PRESENCE_TOPIC,
        json.dumps(build_presence(False), separators=(",", ":")),
        qos=1,
        retain=True,
    )
    client.on_connect = on_connect
    client.on_subscribe = on_subscribe
    client.on_message = on_message
    client.on_publish = on_publish
    client.reconnect_delay_set(min_delay=1, max_delay=15)

    deadline = time.monotonic() + duration_seconds
    last_presence = time.monotonic()
    last_state = time.monotonic()
    exit_code = 0
    try:
        client.connect(config.host, config.port, keepalive=30)
        client.loop_start()
        if not subscription_ready.wait(15):
            detail = failures[0] if failures else "subscription timed out"
            raise RuntimeError(detail)
        wait_for_publications(
            readiness_queue.get_nowait(),
            description="readiness",
        )
        logger.info("l9_workflow_ready robot_id=%s", TEST_ROBOT_ID)

        while not stop_requested.wait(1):
            if failures:
                raise RuntimeError(failures[0])
            try:
                envelope, reference = command_queue.get_nowait()
            except queue.Empty:
                envelope = None
                reference = None
            if envelope is not None and reference is not None:
                acknowledgement, event = build_linked_messages(
                    envelope,
                    observed_at=reference,
                )
                active_state = build_active_state(
                    delivery_id=envelope["payload"]["deliveryId"],
                    observed_at=reference + timedelta(milliseconds=1),
                )
                publications = [
                    publish_json(client, ACK_TOPIC, acknowledgement),
                    publish_json(client, EVENT_TOPIC, event),
                    publish_json(client, STATE_TOPIC, active_state),
                ]
                accepted_command_id.append(envelope["commandId"])
                wait_for_publications(
                    publications,
                    description="ACK/event/active-state",
                )
                logger.info(
                    "l9_workflow_broker_acknowledged command_id=%s "
                    "delivery_id=%s event_id=%s",
                    envelope["commandId"],
                    envelope["payload"]["deliveryId"],
                    event["eventId"],
                )
                break

            now = time.monotonic()
            if now >= deadline:
                raise RuntimeError("no fresh START_MISSION command arrived")
            if not client.is_connected() or command_received.is_set():
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
            "l9_workflow_failed reason=%s detail=%s",
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
        logger.info("l9_workflow_stopped robot_id=%s", TEST_ROBOT_ID)
    return exit_code


def run_self_test() -> None:
    command_id = "22222222-2222-4222-8222-222222222222"
    delivery_id = "11111111-1111-4111-8111-111111111111"
    observed_at = datetime.now(timezone.utc)
    acknowledgement, event = build_linked_messages(
        {
            "commandId": command_id,
            "payload": {"deliveryId": delivery_id},
        },
        observed_at=observed_at,
    )
    assert acknowledgement["robotId"] == TEST_ROBOT_ID
    assert acknowledgement["commandId"] == command_id
    assert acknowledgement["status"] == "ACKNOWLEDGED"
    assert event["robotId"] == TEST_ROBOT_ID
    assert event["deliveryId"] == delivery_id
    assert event["commandId"] == command_id
    assert event["type"] == "MISSION_STARTED"
    assert event["eventId"] == command_event_id(command_id, "MISSION_STARTED")


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Run one guarded robot-test-l6 readiness/ACK/MISSION_STARTED workflow"
        ),
    )
    parser.add_argument(
        "--duration",
        type=int,
        default=DEFAULT_DURATION_SECONDS,
        help="Automatic shutdown time in seconds (30-1800, default: 900)",
    )
    parser.add_argument(
        "--expected-delivery-id",
        help="Exact fresh isolated delivery UUID that may be accepted",
    )
    parser.add_argument(
        "--self-test",
        action="store_true",
        help="Validate linked payload and identity guards without MQTT",
    )
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    if args.self_test:
        run_self_test()
        logger.info("l9_workflow_self_test_passed")
        return 0
    if not args.expected_delivery_id:
        parser.error("--expected-delivery-id is required")
    try:
        expected_delivery_id = str(uuid.UUID(args.expected_delivery_id))
    except (ValueError, AttributeError):
        parser.error("--expected-delivery-id must be a UUID")
    return run_workflow(load_config(), args.duration, expected_delivery_id)


if __name__ == "__main__":
    raise SystemExit(main())
