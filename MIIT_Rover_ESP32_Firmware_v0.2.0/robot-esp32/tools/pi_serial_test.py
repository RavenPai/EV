#!/usr/bin/env python3
"""Safe Raspberry Pi bench client for the MIIT Rover ESP32 protocol.

The default action only sends STOP and STATUS. Motion requires the explicit
--motion-test and --wheels-raised-and-area-clear flags.
"""

from __future__ import annotations

import argparse
import binascii
import json
import sys
import time
import uuid
from dataclasses import dataclass
from typing import Any


PROTOCOL_VERSION = 1
MAX_BENCH_PERMILLE = 350


def canonical_payload(
    session: str,
    sequence: int,
    command: str,
    vx: int = 0,
    vy: int = 0,
    wz: int = 0,
    ttl_ms: int = 200,
) -> str:
    return (
        f"{PROTOCOL_VERSION}|{session}|{sequence}|{command}|"
        f"{vx}|{vy}|{wz}|{ttl_ms}"
    )


def crc16_ccitt_false(payload: str) -> int:
    return binascii.crc_hqx(payload.encode("ascii"), 0xFFFF)


def protected_frame(
    session: str,
    sequence: int,
    command: str,
    vx: int = 0,
    vy: int = 0,
    wz: int = 0,
    ttl_ms: int = 200,
) -> dict[str, Any]:
    canonical = canonical_payload(
        session=session,
        sequence=sequence,
        command=command,
        vx=vx,
        vy=vy,
        wz=wz,
        ttl_ms=ttl_ms,
    )
    return {
        "v": PROTOCOL_VERSION,
        "session": session,
        "seq": sequence,
        "cmd": command,
        "vx": vx,
        "vy": vy,
        "wz": wz,
        "ttlMs": ttl_ms,
        "crc16": f"{crc16_ccitt_false(canonical):04X}",
    }


def json_line(frame: dict[str, Any]) -> bytes:
    return (json.dumps(frame, separators=(",", ":")) + "\n").encode("ascii")


@dataclass
class SequenceCounter:
    value: int = 0

    def next(self) -> int:
        self.value = (self.value + 1) & 0xFFFFFFFF
        return self.value


def print_vectors() -> None:
    session = "pi-test-001"
    for sequence, command, vx, vy, wz, ttl_ms in (
        (1, "ARM", 0, 0, 0, 200),
        (2, "HEARTBEAT", 0, 0, 0, 200),
        (3, "DRIVE", 120, 0, 0, 200),
        (4, "DRIVE", 0, 100, 0, 200),
        (5, "DRIVE", 0, 0, 100, 200),
    ):
        canonical = canonical_payload(
            session, sequence, command, vx, vy, wz, ttl_ms
        )
        print(canonical)
        print(json.dumps(protected_frame(
            session, sequence, command, vx, vy, wz, ttl_ms
        ), separators=(",", ":")))


def read_replies(serial_port: Any, seconds: float) -> list[dict[str, Any]]:
    replies: list[dict[str, Any]] = []
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        raw = serial_port.readline()
        if not raw:
            continue
        text = raw.decode("utf-8", errors="replace").rstrip()
        print("ESP32 <-", text)
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            replies.append(parsed)
    return replies


def require_ack(
    replies: list[dict[str, Any]],
    command: str,
    *,
    sequence: int | None = None,
    require_armed: bool | None = None,
) -> dict[str, Any]:
    for reply in reversed(replies):
        if reply.get("cmd") != command:
            continue
        if sequence is not None and reply.get("seq") != sequence:
            continue
        if reply.get("type") != "ACK" or reply.get("ok") is not True:
            raise RuntimeError(
                f"ESP32 rejected {command}: {reply.get('reason', 'unknown reason')}"
            )
        if require_armed is not None and reply.get("armed") is not require_armed:
            raise RuntimeError(f"ESP32 {command} ACK has an unsafe armed state")
        return reply
    raise RuntimeError(f"ESP32 did not acknowledge {command}")


def send(serial_port: Any, frame: dict[str, Any]) -> None:
    encoded = json_line(frame)
    print("Pi ->", encoded.decode("ascii").rstrip())
    serial_port.write(encoded)
    serial_port.flush()


def validate_motion_arguments(args: argparse.Namespace) -> None:
    if not args.motion_test:
        return
    if not args.wheels_raised_and_area_clear:
        raise SystemExit(
            "Refusing motion: add --wheels-raised-and-area-clear only after "
            "physically raising every wheel and keeping the E-stop in reach."
        )
    for name in ("vx", "vy", "wz"):
        value = getattr(args, name)
        if not (-MAX_BENCH_PERMILLE <= value <= MAX_BENCH_PERMILLE):
            raise SystemExit(
                f"--{name} must be within +/-{MAX_BENCH_PERMILLE}"
            )
    if not (0.1 <= args.duration <= 3.0):
        raise SystemExit("--duration must be between 0.1 and 3.0 seconds")
    if not (50 <= args.ttl_ms <= 300):
        raise SystemExit("--ttl-ms must be between 50 and 300")
    if not (40 <= args.repeat_ms < args.ttl_ms):
        raise SystemExit("--repeat-ms must be >=40 and shorter than --ttl-ms")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--port",
        help="Stable ESP32 path, preferably /dev/serial/by-id/...",
    )
    parser.add_argument("--baud", type=int, default=115200)
    parser.add_argument(
        "--print-vectors",
        action="store_true",
        help="Print CRC examples without opening a serial device",
    )
    parser.add_argument(
        "--motion-test",
        action="store_true",
        help="Arm and issue a short repeated DRIVE target",
    )
    parser.add_argument(
        "--wheels-raised-and-area-clear",
        action="store_true",
        help="Required physical-safety confirmation for --motion-test",
    )
    parser.add_argument("--vx", type=int, default=100, help="Forward + / reverse -")
    parser.add_argument("--vy", type=int, default=0, help="Left + / right -")
    parser.add_argument("--wz", type=int, default=0, help="Turn left + / right -")
    parser.add_argument("--duration", type=float, default=1.0)
    parser.add_argument("--ttl-ms", type=int, default=200)
    parser.add_argument("--repeat-ms", type=int, default=80)
    parser.add_argument(
        "--estop",
        action="store_true",
        help="Send a latching software ESTOP; local button reset is required",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.print_vectors:
        print_vectors()
        if not args.port:
            return 0
    if not args.port:
        raise SystemExit("--port is required unless only --print-vectors is used")

    validate_motion_arguments(args)

    try:
        import serial  # Imported late so --print-vectors needs no dependency.
    except ImportError as error:
        raise SystemExit(
            "pyserial is missing. Activate the rover venv and run: "
            "python -m pip install pyserial==3.5"
        ) from error

    serial_port = serial.Serial(
        args.port,
        args.baud,
        timeout=0.05,
        write_timeout=0.5,
    )

    try:
        # Many ESP32 DevKit boards reset when USB serial is opened.
        time.sleep(2.0)
        read_replies(serial_port, 0.4)

        send(serial_port, {"v": 1, "cmd": "STOP", "ttlMs": 300})
        require_ack(read_replies(serial_port, 0.4), "STOP", require_armed=False)
        send(serial_port, {"v": 1, "cmd": "STATUS"})
        require_ack(read_replies(serial_port, 0.4), "STATUS")

        if args.estop:
            send(serial_port, {"v": 1, "cmd": "ESTOP"})
            estop_reply = require_ack(
                read_replies(serial_port, 0.5),
                "ESTOP",
                require_armed=False,
            )
            if estop_reply.get("estopLatched") is not True:
                raise RuntimeError("ESP32 ESTOP ACK did not report a latched stop")
            return 0

        if not args.motion_test:
            print("No motion requested. STOP/STATUS test complete.")
            return 0

        session = str(uuid.uuid4())
        sequence = SequenceCounter()
        arm_sequence = sequence.next()
        send(
            serial_port,
            protected_frame(
                session, arm_sequence, "ARM", ttl_ms=args.ttl_ms
            ),
        )
        arm_reply = require_ack(
            read_replies(serial_port, 0.4),
            "ARM",
            sequence=arm_sequence,
            require_armed=True,
        )
        if (
            arm_reply.get("estopLatched") is not False
            or arm_reply.get("estopInputHealthy") is not True
            or arm_reply.get("piHardStop") is not False
            or arm_reply.get("motorOutputsEnabled") is not True
        ):
            raise RuntimeError("ESP32 ARM ACK did not report a safe output state")

        end_time = time.monotonic() + args.duration
        interval = args.repeat_ms / 1000.0
        while time.monotonic() < end_time:
            started = time.monotonic()
            drive_sequence = sequence.next()
            send(
                serial_port,
                protected_frame(
                    session=session,
                    sequence=drive_sequence,
                    command="DRIVE",
                    vx=args.vx,
                    vy=args.vy,
                    wz=args.wz,
                    ttl_ms=args.ttl_ms,
                ),
            )
            require_ack(
                read_replies(serial_port, min(0.05, interval * 0.75)),
                "DRIVE",
                sequence=drive_sequence,
                require_armed=True,
            )
            remaining = interval - (time.monotonic() - started)
            if remaining > 0:
                time.sleep(remaining)

        return 0
    except KeyboardInterrupt:
        print("Interrupted; sending STOP.", file=sys.stderr)
        return 130
    finally:
        try:
            send(serial_port, {"v": 1, "cmd": "STOP", "ttlMs": 300})
            time.sleep(0.1)
        finally:
            serial_port.close()


if __name__ == "__main__":
    raise SystemExit(main())
