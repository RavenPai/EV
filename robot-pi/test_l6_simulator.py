from __future__ import annotations

import json
import unittest
from datetime import datetime, timedelta, timezone

import l6_simulator


class L6SimulatorTests(unittest.TestCase):
    def test_identity_guard_rejects_physical_robot(self) -> None:
        with self.assertRaisesRegex(ValueError, "locked to robot-test-l6"):
            l6_simulator.assert_test_identity(
                "robot-01",
                "robot-test-l6",
                "robot-test-l6-subscriber",
            )

    def test_readiness_payload_is_scoped_to_test_robot(self) -> None:
        state = l6_simulator.build_state()
        self.assertEqual(state["robotId"], "robot-test-l6")
        self.assertEqual(state["status"], "ONLINE")
        self.assertEqual(state["mode"], "IDLE")
        self.assertEqual(state["speedMps"], 0)
        self.assertIsNone(state["currentDeliveryId"])
        self.assertEqual(
            [state["lidar"], state["camera"], state["esp32"]],
            ["OK", "OK", "OK"],
        )

    def test_validates_only_test_start_mission_commands(self) -> None:
        issued_at = datetime.now(timezone.utc)
        envelope = {
            "schemaVersion": 1,
            "commandId": "22222222-2222-4222-8222-222222222222",
            "robotId": "robot-test-l6",
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
        validated = l6_simulator.validate_test_command(
            json.dumps(envelope).encode(),
            topic=l6_simulator.COMMAND_TOPIC,
            qos=1,
            retain=False,
        )
        self.assertEqual(validated["commandId"], envelope["commandId"])

        envelope["command"] = "PAUSE"
        envelope["payload"] = {"reason": "test"}
        with self.assertRaisesRegex(ValueError, "START_MISSION only"):
            l6_simulator.validate_test_command(
                json.dumps(envelope).encode(),
                topic=l6_simulator.COMMAND_TOPIC,
                qos=1,
                retain=False,
            )


if __name__ == "__main__":
    unittest.main()
