from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from local_store import (
    enqueue_command_request,
    move_file_durable,
    recover_atomic_json_files,
)


FIRST_ID = "11111111-1111-4111-8111-111111111111"
SECOND_ID = "22222222-2222-4222-8222-222222222222"


class CommandInboxTests(unittest.TestCase):
    def test_each_command_has_a_separate_durable_handoff_file(self):
        with tempfile.TemporaryDirectory() as temporary:
            inbox = Path(temporary)
            first = enqueue_command_request(
                inbox,
                "START_MISSION",
                FIRST_ID,
                "2026-07-21T02:30:00+00:00",
                {"deliveryId": "delivery-one"},
            )
            second = enqueue_command_request(
                inbox,
                "PAUSE",
                SECOND_ID,
                "2026-07-21T02:30:01+00:00",
            )

            self.assertNotEqual(first, second)
            self.assertEqual(len(list(inbox.glob("*.json"))), 2)
            self.assertEqual(json.loads(first.read_text())["type"], "START_MISSION")
            self.assertEqual(json.loads(second.read_text())["type"], "PAUSE")

    def test_same_command_is_idempotent_but_conflicting_content_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            inbox = Path(temporary)
            first = enqueue_command_request(
                inbox,
                "PAUSE",
                FIRST_ID,
                "2026-07-21T02:30:00+00:00",
            )
            repeated = enqueue_command_request(
                inbox,
                "PAUSE",
                FIRST_ID,
                "2026-07-21T02:30:00+00:00",
            )
            self.assertEqual(first, repeated)

            repeated_after_restart = enqueue_command_request(
                inbox,
                "PAUSE",
                FIRST_ID,
                "2026-07-21T02:30:01+00:00",
            )
            self.assertEqual(first, repeated_after_restart)

            with self.assertRaises(ValueError):
                enqueue_command_request(
                    inbox,
                    "ESTOP",
                    FIRST_ID,
                    "2026-07-21T02:30:00+00:00",
                )

    def test_complete_interrupted_atomic_write_is_recovered(self):
        with tempfile.TemporaryDirectory() as temporary:
            inbox = Path(temporary)
            interrupted = inbox / f"{FIRST_ID}.json.tmp"
            interrupted.write_text(
                json.dumps({"commandId": FIRST_ID, "type": "PAUSE"}),
                encoding="utf-8",
            )

            recover_atomic_json_files(inbox)

            self.assertFalse(interrupted.exists())
            self.assertTrue((inbox / f"{FIRST_ID}.json").exists())

    def test_durable_move_creates_destination_and_removes_source(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "outbox" / "event.json"
            destination = root / "archive" / "event.json"
            source.parent.mkdir()
            source.write_text("{}", encoding="utf-8")

            move_file_durable(source, destination)

            self.assertFalse(source.exists())
            self.assertEqual(destination.read_text(encoding="utf-8"), "{}")


if __name__ == "__main__":
    unittest.main()
