from __future__ import annotations

import os
import unittest
from datetime import datetime, timezone
from unittest.mock import patch

import l7_event_probe


class L7EventProbeTests(unittest.TestCase):
    def test_event_is_nonphysical_and_cannot_advance_delivery(self) -> None:
        event = l7_event_probe.build_probe_event(
            event_id="22222222-2222-4222-8222-222222222222",
            at=datetime.now(timezone.utc),
        )

        self.assertEqual(event["robotId"], "robot-test-l6")
        self.assertEqual(event["type"], "OBSTACLE_DETECTED")
        self.assertEqual(event["severity"], "WARNING")
        self.assertEqual(event["payload"]["source"], "l7-controlled-probe")
        self.assertTrue(event["payload"]["nonPhysical"])
        self.assertNotIn("deliveryId", event)
        self.assertNotIn("commandId", event)

    def test_config_rejects_physical_identity(self) -> None:
        with patch.dict(
            os.environ,
            {
                "L7_MQTT_HOST": "mqtt.example.invalid",
                "L7_MQTT_USERNAME": "robot-01",
                "L7_MQTT_PASSWORD": "test-only-placeholder",
                "L7_MQTT_CLIENT_ID": "robot-01-l7-probe",
            },
            clear=True,
        ):
            with self.assertRaisesRegex(ValueError, "username"):
                l7_event_probe.load_config()

    def test_config_requires_password_without_storing_a_default(self) -> None:
        with patch.dict(
            os.environ,
            {
                "L7_MQTT_HOST": "mqtt.example.invalid",
                "L7_MQTT_USERNAME": "robot-test-l6",
                "L7_MQTT_CLIENT_ID": "robot-test-l6-l7-probe",
            },
            clear=True,
        ):
            with self.assertRaisesRegex(ValueError, "L7_MQTT_PASSWORD"):
                l7_event_probe.load_config()


if __name__ == "__main__":
    unittest.main()
