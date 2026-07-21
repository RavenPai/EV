"""Power-loss-conscious local JSON handoff helpers for the Pi bridge."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

from message_contract import as_uuid, parse_timestamp


def _fsync_directory(directory: Path) -> None:
    """Persist a directory entry update on filesystems that support it."""

    directory_fd: int | None = None
    try:
        directory_fd = os.open(directory, os.O_RDONLY)
        os.fsync(directory_fd)
    except OSError:
        # Windows does not permit fsync on a directory. The deployed Linux Pi
        # does. Do not hide a Linux storage failure behind portability logic.
        if sys.platform != "win32":
            raise
    finally:
        if directory_fd is not None:
            os.close(directory_fd)


def write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    """Write, fsync, and atomically replace one JSON file.

    The directory fsync is supported on Linux. Other development platforms may
    reject it, so the file fsync and atomic replace remain the portable floor.
    """

    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(f"{path.suffix}.tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, separators=(",", ":"), allow_nan=False)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)
    _fsync_directory(path.parent)


def move_file_durable(source: Path, destination: Path) -> None:
    """Atomically move a file and persist both affected directory entries."""

    destination.parent.mkdir(parents=True, exist_ok=True)
    os.replace(source, destination)
    _fsync_directory(destination.parent)
    if source.parent != destination.parent:
        _fsync_directory(source.parent)


def enqueue_command_request(
    inbox: Path,
    request_type: str,
    command_id: str,
    requested_at: str,
    payload: dict[str, Any] | None = None,
    archive: Path | None = None,
) -> Path:
    """Persist one command per file so a newer request cannot overwrite it."""

    normalized_id = as_uuid(command_id, "commandId")
    normalized_at = parse_timestamp(requested_at, "requestedAt").isoformat()
    if not isinstance(request_type, str) or not request_type.strip():
        raise ValueError("request type must be a non-empty string")
    if payload is not None and not isinstance(payload, dict):
        raise ValueError("command request payload must be a JSON object")

    request = {
        **(payload or {}),
        "type": request_type,
        "commandId": normalized_id,
        "requestedAt": normalized_at,
    }
    path = inbox / f"{normalized_id}.json"
    candidates = [path]
    if archive is not None:
        candidates.append(archive / path.name)
    for existing_path in candidates:
        if not existing_path.exists():
            continue
        existing = json.loads(existing_path.read_text(encoding="utf-8"))
        existing_identity = {
            key: value for key, value in existing.items() if key != "requestedAt"
        }
        request_identity = {
            key: value for key, value in request.items() if key != "requestedAt"
        }
        if existing_identity != request_identity:
            raise ValueError("command inbox contains a conflicting commandId")
        return existing_path

    write_json_atomic(path, request)
    return path


def recover_atomic_json_files(directory: Path) -> None:
    """Recover complete `*.json.tmp` writes left by a process interruption."""

    for temporary in directory.glob("*.json.tmp"):
        target = temporary.with_suffix("")
        if target.exists():
            temporary.unlink()
            _fsync_directory(directory)
            continue
        try:
            payload = json.loads(temporary.read_text(encoding="utf-8"))
        except (UnicodeError, RecursionError, json.JSONDecodeError):
            move_file_durable(temporary, temporary.with_suffix(".bad"))
            continue
        if not isinstance(payload, dict):
            move_file_durable(temporary, temporary.with_suffix(".bad"))
            continue
        move_file_durable(temporary, target)
