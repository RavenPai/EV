"""One-shot non-physical MQTT lifecycle-event probe for L9.

The probe is hard-locked to ``robot-test-l6``. It publishes exactly one QoS 1,
non-retained mission event and cannot address the physical robot, publish a
command, or control hardware. MQTT credentials are accepted only through
environment variables so they do not appear in shell history.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import ssl
import threading
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from message_contract import (
    MISSION_EVENT_TYPES,
    parse_timestamp,
    prepare_event_payload,
)


TEST_ROBOT_ID = "robot-test-l6"
EVENT_TOPIC = f"miit/robots/{TEST_ROBOT_ID}/events"
EVENT_SOURCE = "l9-controlled-lifecycle-probe"

logger = logging.getLogger("miit-l9-lifecycle-probe")


@dataclass(frozen=True)
class ProbeConfig:
    host: str
    port: int
    username: str
    password: str
    client_id: str
    ca_file: str | None


def assert_test_identity(username: str, client_id: str) -> None:
    if username != TEST_ROBOT_ID:
        raise ValueError("L9 MQTT username must equal robot-test-l6")
    if not client_id.startswith(f"{TEST_ROBOT_ID}-"):
        raise ValueError("L9 MQTT client ID must start with robot-test-l6-")


def build_lifecycle_event(
    *,
    event_type: str,
    delivery_id: str,
    command_id: str,
    event_id: str | None = None,
    severity: str = "INFO",
    at: datetime | None = None,
    detail: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build one schema-valid event for the isolated L9 robot."""

    if event_type not in MISSION_EVENT_TYPES:
        raise ValueError("L9 probe accepts mission lifecycle events only")
    observed_at = at or datetime.now(timezone.utc)
    event, _changed = prepare_event_payload(
        {
            "eventId": event_id or str(uuid.uuid4()),
            "deliveryId": delivery_id,
            "commandId": command_id,
            "type": event_type,
            "severity": severity,
            "at": observed_at.isoformat(),
            "payload": {
                "source": EVENT_SOURCE,
                "nonPhysical": True,
                "purpose": "Verify event-driven delivery advancement",
                **(detail or {}),
            },
        },
        robot_id=TEST_ROBOT_ID,
        now=observed_at,
    )
    return event


def load_config() -> ProbeConfig:
    host = os.environ.get("L9_MQTT_HOST", "").strip()
    username = os.environ.get("L9_MQTT_USERNAME", "").strip()
    password = os.environ.get("L9_MQTT_PASSWORD", "")
    client_id = os.environ.get(
        "L9_MQTT_CLIENT_ID",
        f"{TEST_ROBOT_ID}-l9-probe",
    ).strip()
    assert_test_identity(username, client_id)
    if not host:
        raise ValueError("L9_MQTT_HOST is required")
    if not password:
        raise ValueError("L9_MQTT_PASSWORD is required")
    port = int(os.environ.get("L9_MQTT_PORT", "8883"))
    if not 1 <= port <= 65535:
        raise ValueError("L9_MQTT_PORT is invalid")
    ca_file = os.environ.get("L9_MQTT_CA_FILE", "").strip() or None
    return ProbeConfig(
        host=host,
        port=port,
        username=username,
        password=password,
        client_id=client_id,
        ca_file=ca_file,
    )


def publish_event(config: ProbeConfig, event: dict[str, Any]) -> None:
    """Publish one event and wait for its broker acknowledgement."""

    import paho.mqtt.client as mqtt

    assert_test_identity(config.username, config.client_id)
    connected = threading.Event()
    published = threading.Event()
    failure: list[str] = []

    def on_connect(
        _client: mqtt.Client,
        _userdata: object,
        _flags: mqtt.ConnectFlags,
        reason_code: mqtt.ReasonCode,
        _properties: mqtt.Properties | None,
    ) -> None:
        if reason_code.is_failure:
            failure.append(f"MQTT connection rejected: {reason_code}")
        connected.set()

    def on_publish(
        _client: mqtt.Client,
        _userdata: object,
        _message_id: int,
        reason_code: mqtt.ReasonCode,
        _properties: mqtt.Properties | None,
    ) -> None:
        if reason_code.is_failure:
            failure.append(f"MQTT publish rejected: {reason_code}")
        published.set()

    client = mqtt.Client(
        mqtt.CallbackAPIVersion.VERSION2,
        client_id=config.client_id,
        protocol=mqtt.MQTTv5,
    )
    client.username_pw_set(config.username, config.password)
    client.tls_set(ca_certs=config.ca_file, cert_reqs=ssl.CERT_REQUIRED)
    client.on_connect = on_connect
    client.on_publish = on_publish

    try:
        client.connect(config.host, config.port, keepalive=30)
        client.loop_start()
        if not connected.wait(15):
            raise RuntimeError("MQTT connection timed out")
        if failure:
            raise RuntimeError(failure[0])

        result = client.publish(
            EVENT_TOPIC,
            json.dumps(event, separators=(",", ":")),
            qos=1,
            retain=False,
        )
        if result.rc != mqtt.MQTT_ERR_SUCCESS:
            raise RuntimeError(f"MQTT publish request failed with code {result.rc}")
        if not published.wait(15):
            raise RuntimeError("MQTT publish acknowledgement timed out")
        if failure:
            raise RuntimeError(failure[0])
    finally:
        client.disconnect()
        client.loop_stop()


def run_self_test() -> None:
    observed_at = datetime.now(timezone.utc)
    event = build_lifecycle_event(
        event_type="MISSION_STARTED",
        delivery_id="11111111-1111-4111-8111-111111111111",
        command_id="22222222-2222-4222-8222-222222222222",
        event_id="33333333-3333-4333-8333-333333333333",
        at=observed_at,
    )
    assert event["robotId"] == TEST_ROBOT_ID
    assert event["type"] == "MISSION_STARTED"
    assert event["payload"]["source"] == EVENT_SOURCE
    assert event["payload"]["nonPhysical"] is True
    assert event["deliveryId"] == "11111111-1111-4111-8111-111111111111"
    assert event["commandId"] == "22222222-2222-4222-8222-222222222222"


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Publish one guarded robot-test-l6 lifecycle event for L9",
    )
    parser.add_argument(
        "--event-type",
        choices=sorted(MISSION_EVENT_TYPES),
        help="Mission lifecycle event to publish",
    )
    parser.add_argument("--delivery-id", help="Isolated test delivery UUID")
    parser.add_argument("--command-id", help="Matching START_MISSION command UUID")
    parser.add_argument(
        "--event-id",
        help="Optional UUID; reuse only with identical content for replay testing",
    )
    parser.add_argument(
        "--at",
        help="Optional ISO-8601 occurrence time; required for an exact replay",
    )
    parser.add_argument(
        "--severity",
        choices=("INFO", "WARNING", "ERROR", "CRITICAL"),
        default="INFO",
    )
    parser.add_argument(
        "--detail-json",
        default="{}",
        help="Optional JSON object merged into the non-physical evidence payload",
    )
    parser.add_argument(
        "--self-test",
        action="store_true",
        help="Validate event and identity guards without MQTT",
    )
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    if args.self_test:
        run_self_test()
        logger.info("l9_probe_self_test_passed")
        return 0

    if not args.event_type or not args.delivery_id or not args.command_id:
        parser.error("--event-type, --delivery-id, and --command-id are required")
    try:
        detail = json.loads(args.detail_json)
    except json.JSONDecodeError as exc:
        parser.error(f"--detail-json is not valid JSON: {exc.msg}")
    if not isinstance(detail, dict):
        parser.error("--detail-json must decode to a JSON object")

    observed_at = parse_timestamp(args.at) if args.at else None
    event = build_lifecycle_event(
        event_type=args.event_type,
        delivery_id=args.delivery_id,
        command_id=args.command_id,
        event_id=args.event_id,
        severity=args.severity,
        at=observed_at,
        detail=detail,
    )
    publish_event(load_config(), event)
    logger.info(
        "l9_event_published event_id=%s event_type=%s robot_id=%s "
        "delivery_id=%s command_id=%s at=%s",
        event["eventId"],
        event["type"],
        event["robotId"],
        event["deliveryId"],
        event["commandId"],
        event["at"],
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
