import os
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch


sys.path.insert(0, str(Path(__file__).resolve().parent))

import l9_lifecycle_probe as probe


DELIVERY_ID = "11111111-1111-4111-8111-111111111111"
COMMAND_ID = "22222222-2222-4222-8222-222222222222"
EVENT_ID = "33333333-3333-4333-8333-333333333333"


class L9LifecycleProbeTests(unittest.TestCase):
    def test_event_is_scoped_to_the_nonphysical_robot(self):
        event = probe.build_lifecycle_event(
            event_type="MISSION_STARTED",
            delivery_id=DELIVERY_ID,
            command_id=COMMAND_ID,
            event_id=EVENT_ID,
            at=datetime.now(timezone.utc),
        )

        self.assertEqual(event["robotId"], "robot-test-l6")
        self.assertEqual(event["deliveryId"], DELIVERY_ID)
        self.assertEqual(event["commandId"], COMMAND_ID)
        self.assertTrue(event["payload"]["nonPhysical"])
        self.assertEqual(
            event["payload"]["source"],
            "l9-controlled-lifecycle-probe",
        )

    def test_identical_inputs_build_an_exact_replay(self):
        observed_at = datetime.now(timezone.utc)
        args = {
            "event_type": "ARRIVED_SOURCE",
            "delivery_id": DELIVERY_ID,
            "command_id": COMMAND_ID,
            "event_id": EVENT_ID,
            "at": observed_at,
            "detail": {"checkpoint": "source"},
        }

        self.assertEqual(
            probe.build_lifecycle_event(**args),
            probe.build_lifecycle_event(**args),
        )

    def test_non_mission_event_is_rejected(self):
        with self.assertRaisesRegex(
            ValueError,
            "mission lifecycle events only",
        ):
            probe.build_lifecycle_event(
                event_type="OBSTACLE_DETECTED",
                delivery_id=DELIVERY_ID,
                command_id=COMMAND_ID,
            )

    def test_config_rejects_a_physical_identity(self):
        with patch.dict(
            os.environ,
            {
                "L9_MQTT_HOST": "broker.example.test",
                "L9_MQTT_USERNAME": "robot-01",
                "L9_MQTT_PASSWORD": "test-only",
                "L9_MQTT_CLIENT_ID": "robot-01-pi",
            },
            clear=True,
        ):
            with self.assertRaisesRegex(ValueError, "equal robot-test-l6"):
                probe.load_config()

    def test_config_requires_password_without_storing_a_default(self):
        with patch.dict(
            os.environ,
            {
                "L9_MQTT_HOST": "broker.example.test",
                "L9_MQTT_USERNAME": "robot-test-l6",
                "L9_MQTT_CLIENT_ID": "robot-test-l6-l9-probe",
            },
            clear=True,
        ):
            with self.assertRaisesRegex(ValueError, "L9_MQTT_PASSWORD"):
                probe.load_config()


if __name__ == "__main__":
    unittest.main()
