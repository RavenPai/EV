"""Hardware-free tests for the isolated L9 workflow simulator."""

from __future__ import annotations

import os
import sys
import unittest
from datetime import datetime, timedelta, timezone


sys.path.insert(0, os.path.dirname(__file__))

from l9_workflow_simulator import (
    ProbeConfig,
    assert_workflow_identity,
    build_active_state,
    build_linked_messages,
    validate_expected_command,
)
from message_contract import command_event_id


class L9WorkflowSimulatorTests(unittest.TestCase):
    def test_requires_the_exact_authorized_client_id(self) -> None:
        base = {
            "host": "example.invalid",
            "port": 8883,
            "username": "robot-test-l6",
            "password": "not-a-real-secret",
            "ca_file": None,
        }
        assert_workflow_identity(
            ProbeConfig(
                **base,
                client_id="robot-test-l6-subscriber",
            )
        )
        with self.assertRaisesRegex(ValueError, "must equal"):
            assert_workflow_identity(
                ProbeConfig(
                    **base,
                    client_id="robot-test-l6-l9-probe",
                )
            )

    def test_builds_linked_ack_and_mission_started_event(self) -> None:
        observed_at = datetime.now(timezone.utc)
        acknowledgement, event = build_linked_messages(
            {
                "commandId": "22222222-2222-4222-8222-222222222222",
                "payload": {
                    "deliveryId": "11111111-1111-4111-8111-111111111111",
                },
            },
            observed_at=observed_at,
        )

        self.assertEqual(acknowledgement["robotId"], "robot-test-l6")
        self.assertEqual(acknowledgement["status"], "ACKNOWLEDGED")
        self.assertEqual(
            acknowledgement["commandId"],
            "22222222-2222-4222-8222-222222222222",
        )
        self.assertEqual(event["robotId"], "robot-test-l6")
        self.assertEqual(event["type"], "MISSION_STARTED")
        self.assertEqual(
            event["deliveryId"],
            "11111111-1111-4111-8111-111111111111",
        )
        self.assertEqual(event["commandId"], acknowledgement["commandId"])
        self.assertEqual(
            event["eventId"],
            command_event_id(event["commandId"], "MISSION_STARTED"),
        )
        self.assertTrue(event["payload"]["nonPhysical"])
        self.assertEqual(
            event["payload"]["runner"],
            "l9-workflow-simulator",
        )

    def test_missing_delivery_link_is_rejected(self) -> None:
        with self.assertRaises(KeyError):
            build_linked_messages(
                {
                    "commandId": "22222222-2222-4222-8222-222222222222",
                    "payload": {},
                },
            )

    def test_expected_delivery_and_command_freshness_are_required(self) -> None:
        now = datetime.now(timezone.utc)
        delivery_id = "11111111-1111-4111-8111-111111111111"
        envelope = {
            "issuedAt": (now - timedelta(seconds=5)).isoformat(),
            "expiresAt": (now + timedelta(minutes=4)).isoformat(),
            "payload": {"deliveryId": delivery_id},
        }

        validate_expected_command(
            envelope,
            expected_delivery_id=delivery_id,
            now=now,
        )
        with self.assertRaisesRegex(ValueError, "expected test delivery"):
            validate_expected_command(
                envelope,
                expected_delivery_id="33333333-3333-4333-8333-333333333333",
                now=now,
            )

    def test_old_or_nearly_expired_commands_are_rejected(self) -> None:
        now = datetime.now(timezone.utc)
        delivery_id = "11111111-1111-4111-8111-111111111111"
        with self.assertRaisesRegex(ValueError, "fresh test command"):
            validate_expected_command(
                {
                    "issuedAt": (now - timedelta(seconds=31)).isoformat(),
                    "expiresAt": (now + timedelta(minutes=4)).isoformat(),
                    "payload": {"deliveryId": delivery_id},
                },
                expected_delivery_id=delivery_id,
                now=now,
            )
        with self.assertRaisesRegex(ValueError, "close to expiration"):
            validate_expected_command(
                {
                    "issuedAt": (now - timedelta(seconds=5)).isoformat(),
                    "expiresAt": (now + timedelta(seconds=29)).isoformat(),
                    "payload": {"deliveryId": delivery_id},
                },
                expected_delivery_id=delivery_id,
                now=now,
            )

    def test_active_state_is_newer_and_linked(self) -> None:
        observed_at = datetime.now(timezone.utc)
        delivery_id = "11111111-1111-4111-8111-111111111111"
        state = build_active_state(
            delivery_id=delivery_id,
            observed_at=observed_at,
        )

        self.assertEqual(state["status"], "BUSY")
        self.assertEqual(state["mode"], "AUTO")
        self.assertEqual(state["currentDeliveryId"], delivery_id)
        self.assertEqual(state["at"], observed_at.isoformat())


if __name__ == "__main__":
    unittest.main()
