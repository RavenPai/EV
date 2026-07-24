"""One-shot non-physical MQTT event probe for the L7 webhook check.

The probe is hard-locked to ``robot-test-l6`` and publishes exactly one
non-mission ``OBSTACLE_DETECTED`` event. It cannot address the physical robot,
advance a delivery, send a command, or control hardware.
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

from l6_simulator import TEST_ROBOT_ID, assert_test_identity
from message_contract import prepare_event_payload


EVENT_TOPIC = f"miit/robots/{TEST_ROBOT_ID}/events"
EVENT_TYPE = "OBSTACLE_DETECTED"
EVENT_SOURCE = "l7-controlled-probe"

logger = logging.getLogger("miit-l7-event-probe")


@dataclass(frozen=True)
class ProbeConfig:
    host: str
    port: int
    username: str
    password: str
    client_id: str
    ca_file: str | None


def build_probe_event(
    *,
    event_id: str | None = None,
    at: datetime | None = None,
) -> dict[str, Any]:
    event, _changed = prepare_event_payload(
        {
            "eventId": event_id or str(uuid.uuid4()),
            "type": EVENT_TYPE,
            "severity": "WARNING",
            "at": (at or datetime.now(timezone.utc)).isoformat(),
            "payload": {
                "source": EVENT_SOURCE,
                "nonPhysical": True,
                "purpose": "Verify the EMQX-to-Supabase event action",
            },
        },
        robot_id=TEST_ROBOT_ID,
        now=at,
    )
    return event


def load_config() -> ProbeConfig:
    host = os.environ.get("L7_MQTT_HOST", "").strip()
    username = os.environ.get("L7_MQTT_USERNAME", "").strip()
    password = os.environ.get("L7_MQTT_PASSWORD", "")
    client_id = os.environ.get(
        "L7_MQTT_CLIENT_ID",
        f"{TEST_ROBOT_ID}-l7-probe",
    ).strip()
    assert_test_identity(TEST_ROBOT_ID, username, client_id)
    if not host:
        raise ValueError("L7_MQTT_HOST is required")
    if not password:
        raise ValueError("L7_MQTT_PASSWORD is required")
    port = int(os.environ.get("L7_MQTT_PORT", "8883"))
    if not 1 <= port <= 65535:
        raise ValueError("L7_MQTT_PORT is invalid")
    ca_file = os.environ.get("L7_MQTT_CA_FILE", "").strip() or None
    return ProbeConfig(
        host=host,
        port=port,
        username=username,
        password=password,
        client_id=client_id,
        ca_file=ca_file,
    )


def publish_probe(config: ProbeConfig, event: dict[str, Any]) -> None:
    import paho.mqtt.client as mqtt

    assert_test_identity(TEST_ROBOT_ID, config.username, config.client_id)
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
    event = build_probe_event(
        event_id="22222222-2222-4222-8222-222222222222",
        at=datetime.now(timezone.utc),
    )
    assert event["robotId"] == TEST_ROBOT_ID
    assert event["type"] == EVENT_TYPE
    assert event["payload"]["source"] == EVENT_SOURCE
    assert event["payload"]["nonPhysical"] is True
    assert "deliveryId" not in event
    assert "commandId" not in event


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Publish one guarded robot-test-l6 event for L7",
    )
    parser.add_argument(
        "--event-id",
        help="Optional UUID for an idempotent repeat of the same probe",
    )
    parser.add_argument(
        "--self-test",
        action="store_true",
        help="Validate the event and identity guards without MQTT",
    )
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    if args.self_test:
        run_self_test()
        logger.info("l7_probe_self_test_passed")
        return 0

    event = build_probe_event(event_id=args.event_id)
    publish_probe(load_config(), event)
    logger.info(
        "l7_probe_published event_id=%s robot_id=%s",
        event["eventId"],
        event["robotId"],
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
