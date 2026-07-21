import json
import pathlib
import sys
import unittest


TOOLS = pathlib.Path(__file__).resolve().parents[1] / "tools"
sys.path.insert(0, str(TOOLS))

from pi_serial_test import (  # noqa: E402
    canonical_payload,
    crc16_ccitt_false,
    json_line,
    protected_frame,
    require_ack,
)


class ProtocolTests(unittest.TestCase):
    @staticmethod
    def firmware_style_crc(payload: str) -> int:
        crc = 0xFFFF
        for byte in payload.encode("ascii"):
            crc ^= byte << 8
            for _ in range(8):
                crc = ((crc << 1) ^ 0x1021) & 0xFFFF if crc & 0x8000 else (crc << 1) & 0xFFFF
        return crc

    def test_known_crc_vectors(self) -> None:
        vectors = {
            "1|pi-test-001|1|ARM|0|0|0|200": 0x33FC,
            "1|pi-test-001|2|HEARTBEAT|0|0|0|200": 0x970E,
            "1|pi-test-001|3|DRIVE|120|0|0|200": 0xE396,
            "1|pi-test-001|4|DRIVE|0|100|0|200": 0x466A,
            "1|pi-test-001|5|DRIVE|0|0|100|200": 0x4DA3,
        }
        for payload, expected in vectors.items():
            with self.subTest(payload=payload):
                self.assertEqual(crc16_ccitt_false(payload), expected)
                self.assertEqual(self.firmware_style_crc(payload), expected)

    def test_protected_frame_matches_canonical_crc(self) -> None:
        frame = protected_frame(
            session="pi-test-001",
            sequence=3,
            command="DRIVE",
            vx=120,
            vy=0,
            wz=0,
            ttl_ms=200,
        )
        canonical = canonical_payload("pi-test-001", 3, "DRIVE", 120, 0, 0, 200)
        self.assertEqual(frame["crc16"], f"{crc16_ccitt_false(canonical):04X}")

    def test_json_line_is_compact_ascii_and_newline_delimited(self) -> None:
        encoded = json_line({"v": 1, "cmd": "STOP", "ttlMs": 300})
        self.assertTrue(encoded.endswith(b"\n"))
        self.assertNotIn(b" ", encoded)
        self.assertEqual(json.loads(encoded), {"v": 1, "cmd": "STOP", "ttlMs": 300})

    def test_motion_requires_a_matching_positive_ack(self) -> None:
        reply = require_ack(
            [{"type": "ACK", "ok": True, "cmd": "ARM", "seq": 7, "armed": True}],
            "ARM",
            sequence=7,
            require_armed=True,
        )
        self.assertTrue(reply["armed"])

        with self.assertRaises(RuntimeError):
            require_ack(
                [{"type": "NACK", "ok": False, "cmd": "ARM", "seq": 7}],
                "ARM",
                sequence=7,
                require_armed=True,
            )


if __name__ == "__main__":
    unittest.main()
