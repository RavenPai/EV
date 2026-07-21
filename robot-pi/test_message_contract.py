from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from message_contract import (
    event_order_key,
    prepare_event_payload,
    prepare_state_payload,
    validate_robot_id,
)


NOW = datetime(2026, 7, 21, 2, 30, tzinfo=timezone.utc)
DELIVERY_ID = "11111111-1111-4111-8111-111111111111"
COMMAND_ID = "22222222-2222-4222-8222-222222222222"
EVENT_ID = "33333333-3333-4333-8333-333333333333"


def valid_state(**overrides):
    state = {
        "status": "BUSY",
        "mode": "AUTO",
        "battery": 82,
        "signal": 91,
        "speedMps": 0.45,
        "locationId": "loc-fcs",
        "currentDeliveryId": DELIVERY_ID,
        "lidar": "OK",
        "camera": "OK",
        "esp32": "OK",
        "motorTempC": 37.2,
        "at": (NOW - timedelta(seconds=5)).isoformat(),
    }
    state.update(overrides)
    return state


class RobotIdentityTests(unittest.TestCase):
    def test_robot_id_matches_ingestion_topic_contract(self):
        self.assertEqual(validate_robot_id("robot-01"), "robot-01")
        for invalid in ("Robot-01", "robot_01", "-robot", "", "r" * 65):
            with self.subTest(invalid=invalid):
                with self.assertRaises(ValueError):
                    validate_robot_id(invalid)


class StateContractTests(unittest.TestCase):
    def test_state_preserves_mission_manager_observation_time(self):
        state = valid_state()
        payload = prepare_state_payload(
            state,
            robot_id="robot-01",
            firmware_version="pi-agent-test",
            max_age_seconds=15,
            now=NOW,
        )

        self.assertEqual(payload["at"], state["at"])
        self.assertNotEqual(payload["at"], NOW.isoformat())
        self.assertEqual(payload["schemaVersion"], 1)
        self.assertEqual(payload["robotId"], "robot-01")
        self.assertEqual(payload["currentDeliveryId"], DELIVERY_ID)

    def test_state_rejects_stale_or_future_observations(self):
        for at in (
            (NOW - timedelta(seconds=16)).isoformat(),
            (NOW + timedelta(minutes=6)).isoformat(),
        ):
            with self.subTest(at=at):
                with self.assertRaises(ValueError):
                    prepare_state_payload(
                        valid_state(at=at),
                        robot_id="robot-01",
                        firmware_version="pi-agent-test",
                        max_age_seconds=15,
                        now=NOW,
                    )

    def test_state_rejects_missing_timestamp_and_bad_schema_values(self):
        missing_at = valid_state()
        del missing_at["at"]
        invalid_states = [
            missing_at,
            valid_state(status="READY"),
            valid_state(mode="DRIVING"),
            valid_state(battery=101),
            valid_state(speedMps=-0.1),
            valid_state(esp32="UNKNOWN"),
            valid_state(currentDeliveryId="not-a-uuid"),
            valid_state(locationId=""),
            valid_state(debug="x" * (33 * 1024)),
        ]
        for state in invalid_states:
            with self.subTest(state=state):
                with self.assertRaises(ValueError):
                    prepare_state_payload(
                        state,
                        robot_id="robot-01",
                        firmware_version="pi-agent-test",
                        max_age_seconds=15,
                        now=NOW,
                    )


class EventContractTests(unittest.TestCase):
    def test_backlogged_events_sort_by_occurrence_not_random_uuid_filename(self):
        later = {
            "eventId": "11111111-1111-4111-8111-111111111111",
            "at": "2026-07-21T02:30:02+00:00",
        }
        earlier = {
            "eventId": "ffffffff-ffff-4fff-8fff-ffffffffffff",
            "at": "2026-07-21T02:30:01+00:00",
        }
        ordered = sorted([later, earlier], key=event_order_key)
        self.assertEqual(ordered, [earlier, later])

    def test_event_matches_ingestion_schema(self):
        event, changed = prepare_event_payload(
            {
                "eventId": EVENT_ID,
                "deliveryId": DELIVERY_ID,
                "commandId": COMMAND_ID,
                "type": "MISSION_STARTED",
                "severity": "INFO",
                "at": NOW.isoformat(),
                "payload": {"locationId": "loc-fcs"},
            },
            robot_id="robot-01",
            now=NOW,
        )

        self.assertTrue(changed)
        self.assertEqual(event["schemaVersion"], 1)
        self.assertEqual(event["robotId"], "robot-01")
        self.assertEqual(event["deliveryId"], DELIVERY_ID)
        self.assertEqual(event["commandId"], COMMAND_ID)

    def test_non_mission_event_gets_durable_identity_and_timestamp(self):
        event, changed = prepare_event_payload(
            {
                "type": "ESP32_DISCONNECTED",
                "severity": "ERROR",
                "payload": {"reason": "SerialException"},
            },
            robot_id="robot-01",
            now=NOW,
        )

        self.assertTrue(changed)
        self.assertRegex(event["eventId"], r"^[0-9a-f-]{36}$")
        self.assertEqual(event["at"], NOW.isoformat())

    def test_event_rejects_values_the_ingestion_function_would_reject(self):
        invalid_events = [
            {"type": "MISSION_STARTED", "severity": "INFO"},
            {
                "eventId": EVENT_ID,
                "deliveryId": DELIVERY_ID,
                "type": "MISSION_STARTED",
                "severity": "INFO",
            },
            {
                "eventId": 0,
                "type": "BRIDGE_FAULT",
                "severity": "ERROR",
            },
            {
                "eventId": EVENT_ID,
                "type": "BRIDGE_FAULT",
                "severity": "ERROR",
                "at": False,
            },
            {
                "eventId": "not-a-uuid",
                "type": "BRIDGE_FAULT",
                "severity": "ERROR",
            },
            {
                "eventId": EVENT_ID,
                "type": "BRIDGE_FAULT",
                "severity": "ERROR",
                "payload": [],
            },
            {
                "eventId": EVENT_ID,
                "type": "UNKNOWN_EVENT",
                "severity": "INFO",
            },
            {
                "eventId": EVENT_ID,
                "type": "BRIDGE_FAULT",
                "severity": "DEBUG",
            },
            {
                "eventId": EVENT_ID,
                "type": "BRIDGE_FAULT",
                "severity": "ERROR",
                "at": (NOW + timedelta(minutes=6)).isoformat(),
            },
            {
                "eventId": EVENT_ID,
                "type": "BRIDGE_FAULT",
                "severity": "ERROR",
                "payload": {"debug": "x" * (33 * 1024)},
            },
        ]
        for event in invalid_events:
            with self.subTest(event=event):
                with self.assertRaises(ValueError):
                    prepare_event_payload(event, robot_id="robot-01", now=NOW)


if __name__ == "__main__":
    unittest.main()
