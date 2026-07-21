from __future__ import annotations

import json
import os
import sys
import tempfile
import types
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch


def _install_dependency_stubs() -> None:
    """Allow bridge logic tests to run without installing Pi-only packages."""

    try:
        import paho.mqtt.client  # noqa: F401
    except ModuleNotFoundError:
        paho = types.ModuleType("paho")
        mqtt_package = types.ModuleType("paho.mqtt")
        mqtt_client = types.ModuleType("paho.mqtt.client")
        mqtt_client.MQTT_ERR_SUCCESS = 0
        mqtt_client.Client = object
        mqtt_client.MQTTMessage = object
        paho.mqtt = mqtt_package
        mqtt_package.client = mqtt_client
        sys.modules["paho"] = paho
        sys.modules["paho.mqtt"] = mqtt_package
        sys.modules["paho.mqtt.client"] = mqtt_client

    try:
        import serial  # noqa: F401
    except ModuleNotFoundError:
        serial_module = types.ModuleType("serial")
        serial_module.Serial = object
        sys.modules["serial"] = serial_module


_install_dependency_stubs()
_state_root = tempfile.TemporaryDirectory()
os.environ.setdefault("ROBOT_ID", "robot-01")
os.environ.setdefault("MQTT_HOST", "broker.invalid")
os.environ.setdefault("MQTT_USERNAME", "robot-01")
os.environ.setdefault("MQTT_PASSWORD", "test-only")
os.environ["ROBOT_STATE_DIR"] = _state_root.name
os.environ["ROBOT_REQUIRE_TIME_SYNC"] = "false"

import agent  # noqa: E402


COMMAND_ID = "22222222-2222-4222-8222-222222222222"
DELIVERY_ID = "11111111-1111-4111-8111-111111111111"


class FakeMessage:
    def __init__(
        self,
        payload: dict,
        *,
        topic: str = agent.COMMAND_TOPIC,
        qos: int = 1,
        retain: bool = False,
        mid: int = 7,
    ) -> None:
        self.payload = json.dumps(payload, separators=(",", ":")).encode()
        self.topic = topic
        self.qos = qos
        self.retain = retain
        self.mid = mid


class FakeClient:
    def __init__(self, on_ack=None) -> None:
        self.acks: list[tuple[int, int]] = []
        self.on_ack = on_ack

    def ack(self, mid: int, qos: int) -> int:
        if self.on_ack:
            self.on_ack()
        self.acks.append((mid, qos))
        return agent.mqtt.MQTT_ERR_SUCCESS


def command_envelope(
    *,
    command: str = "PAUSE",
    command_id: str = COMMAND_ID,
    payload: dict | None = None,
    lifetime_seconds: float = 60,
) -> dict:
    issued_at = datetime.now(timezone.utc)
    if payload is None:
        payload = {"reason": "test"}
    return {
        "schemaVersion": 1,
        "commandId": command_id,
        "robotId": "robot-01",
        "command": command,
        "payload": payload,
        "issuedAt": issued_at.isoformat(),
        "expiresAt": (issued_at + timedelta(seconds=lifetime_seconds)).isoformat(),
    }


class AgentCommandTests(unittest.TestCase):
    def setUp(self) -> None:
        agent.db.execute("delete from processed_commands")
        agent.db.commit()
        agent.stop_event.clear()
        for directory in (
            agent.COMMAND_INBOX_DIR,
            agent.COMMAND_ARCHIVE_DIR,
            agent.EVENT_OUTBOX_DIR,
            agent.EVENT_ARCHIVE_DIR,
            agent.ACK_OUTBOX_DIR,
            agent.ACK_ARCHIVE_DIR,
        ):
            for path in directory.iterdir():
                if path.is_file():
                    path.unlink()

    @classmethod
    def tearDownClass(cls) -> None:
        agent.db.close()
        _state_root.cleanup()

    def test_estop_serial_happens_before_durable_handoffs_and_ack(self):
        order: list[str] = []
        message = FakeMessage(command_envelope(command="ESTOP"))

        def inbound_ack_check() -> None:
            self.assertEqual(len(list(agent.ACK_OUTBOX_DIR.glob("*.json"))), 1)
            order.append("mqtt-ack")

        with (
            patch.object(agent, "send_to_esp32", side_effect=lambda command: order.append(f"serial-{command}")),
            patch.object(agent, "publish_event", side_effect=lambda *args, **kwargs: order.append("event") or Path("event.json")),
            patch.object(agent, "write_mission_request", side_effect=lambda *args, **kwargs: order.append("inbox")),
        ):
            client = FakeClient(inbound_ack_check)
            agent.on_message(client, None, message)

        self.assertEqual(order, ["serial-ESTOP", "event", "inbox", "mqtt-ack"])
        ack = json.loads(next(agent.ACK_OUTBOX_DIR.glob("*.json")).read_text())
        self.assertEqual(ack["status"], "ACKNOWLEDGED")

    def test_retained_command_is_rejected_without_execution(self):
        message = FakeMessage(command_envelope(), retain=True)
        client = FakeClient()
        with (
            patch.object(agent, "send_to_esp32") as send,
            patch.object(agent, "write_mission_request") as handoff,
        ):
            agent.on_message(client, None, message)

        send.assert_not_called()
        handoff.assert_not_called()
        ack = json.loads(next(agent.ACK_OUTBOX_DIR.glob("*.json")).read_text())
        self.assertEqual(ack["status"], "REJECTED")
        self.assertIn("retained", ack["reason"])
        self.assertEqual(client.acks, [(message.mid, 1)])

    def test_conflicting_replay_keeps_original_outcome(self):
        first = FakeMessage(command_envelope(payload={"reason": "first"}))
        client = FakeClient()
        with (
            patch.object(agent, "send_to_esp32"),
            patch.object(agent, "write_mission_request"),
        ):
            agent.on_message(client, None, first)

        ack_path = next(agent.ACK_OUTBOX_DIR.glob("*.json"))
        ack_path.replace(agent.ACK_ARCHIVE_DIR / ack_path.name)
        conflict = FakeMessage(command_envelope(payload={"reason": "changed"}), mid=8)
        with (
            patch.object(agent, "send_to_esp32"),
            patch.object(agent, "publish_event_safely", return_value=Path("fault.json")) as fault,
        ):
            agent.on_message(client, None, conflict)

        fault.assert_called_once()
        self.assertEqual(list(agent.ACK_OUTBOX_DIR.glob("*.json")), [])
        archived = json.loads(next(agent.ACK_ARCHIVE_DIR.glob("*.json")).read_text())
        self.assertEqual(archived["status"], "ACKNOWLEDGED")
        self.assertEqual(client.acks, [(7, 1), (8, 1)])

    def test_broker_message_is_not_acked_when_ack_file_cannot_be_persisted(self):
        message = FakeMessage(command_envelope())
        client = FakeClient()
        with (
            patch.object(agent, "send_to_esp32"),
            patch.object(agent, "write_mission_request"),
            patch.object(agent, "acknowledge", side_effect=OSError("disk unavailable")),
        ):
            agent.on_message(client, None, message)

        self.assertEqual(client.acks, [])

    def test_broker_message_is_not_acked_when_outcome_database_fails(self):
        message = FakeMessage(command_envelope())
        client = FakeClient()
        with (
            patch.object(agent, "send_to_esp32"),
            patch.object(agent, "write_mission_request"),
            patch.object(agent, "mark_processed", side_effect=OSError("database unavailable")),
            patch.object(agent, "processed_outcome", return_value=None),
            patch.object(agent, "publish_event_safely", return_value=Path("fault.json")),
        ):
            agent.on_message(client, None, message)

        self.assertEqual(client.acks, [])
        self.assertEqual(list(agent.ACK_OUTBOX_DIR.glob("*.json")), [])

    def test_expired_duplicate_replays_original_ack_timestamp(self):
        envelope = command_envelope(lifetime_seconds=60)
        first = FakeMessage(envelope)
        client = FakeClient()
        with (
            patch.object(agent, "send_to_esp32"),
            patch.object(agent, "write_mission_request"),
        ):
            agent.on_message(client, None, first)

        ack_path = next(agent.ACK_OUTBOX_DIR.glob("*.json"))
        original = json.loads(ack_path.read_text())
        ack_path.replace(agent.ACK_ARCHIVE_DIR / ack_path.name)
        duplicate = FakeMessage(envelope, mid=9)
        real_datetime = datetime

        class FutureDateTime(real_datetime):
            @classmethod
            def now(cls, tz=None):
                value = real_datetime.now(timezone.utc) + timedelta(minutes=2)
                return value if tz is not None else value.replace(tzinfo=None)

        with patch.object(agent, "datetime", FutureDateTime):
            agent.on_message(client, None, duplicate)
        replayed = json.loads(next(agent.ACK_ARCHIVE_DIR.glob("*.json")).read_text())
        self.assertEqual(replayed["status"], "ACKNOWLEDGED")
        self.assertEqual(replayed["at"], original["at"])
        self.assertEqual(client.acks[-1], (9, 1))

    def test_existing_deterministic_event_must_match_content(self):
        event_id = agent.command_event_id(COMMAND_ID, "ESTOP_TRIGGERED")
        agent.write_json_atomic(
            agent.EVENT_OUTBOX_DIR / f"{event_id}.json",
            {
                "schemaVersion": 1,
                "robotId": agent.ROBOT_ID,
                "eventId": event_id,
                "commandId": COMMAND_ID,
                "type": "BRIDGE_FAULT",
                "severity": "ERROR",
                "at": datetime.now(timezone.utc).isoformat(),
                "payload": {},
            },
        )

        with self.assertRaises(ValueError):
            agent.publish_event(
                FakeClient(),
                "ESTOP_TRIGGERED",
                "CRITICAL",
                {"physicalConfirmation": False, "phase": "requested"},
                command_id=COMMAND_ID,
                event_id=event_id,
            )

    def test_archived_command_handoff_prevents_duplicate_mission_file(self):
        envelope = command_envelope(
            command="START_MISSION",
            payload={
                "sourceLocationId": "loc-fcs",
                "destinationLocationId": "loc-library",
                "mapVersion": "miit-campus-v1",
                "deliveryId": DELIVERY_ID,
            },
        )
        requested_at = envelope["issuedAt"]
        agent.write_json_atomic(
            agent.COMMAND_ARCHIVE_DIR / f"{COMMAND_ID}.json",
            {
                **envelope["payload"],
                "type": "START_MISSION",
                "commandId": COMMAND_ID,
                "requestedAt": requested_at,
            },
        )

        client = FakeClient()
        agent.on_message(client, None, FakeMessage(envelope))

        self.assertEqual(list(agent.COMMAND_INBOX_DIR.glob("*.json")), [])
        self.assertTrue((agent.COMMAND_ARCHIVE_DIR / f"{COMMAND_ID}.json").exists())
        self.assertEqual(client.acks, [(7, 1)])

    def test_estop_serial_frame_is_latching_and_has_no_ttl(self):
        class Link:
            def __init__(self) -> None:
                self.frames: list[bytes] = []

            def write(self, frame: bytes) -> None:
                self.frames.append(frame)

            def flush(self) -> None:
                return None

        link = Link()
        with patch.object(agent, "ensure_esp32", return_value=link):
            agent.esp32_ready_at = 0
            agent.send_to_esp32("ESTOP")

        self.assertEqual(json.loads(link.frames[0]), {"v": 1, "cmd": "ESTOP"})


if __name__ == "__main__":
    unittest.main()
