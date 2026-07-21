"""Pure validation helpers for Pi-to-cloud MQTT messages.

Keep these checks aligned with ``ingest-robot-message`` so malformed local
state or event files are rejected before EMQX accepts them.
"""

from __future__ import annotations

import math
import json
import re
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any


ROBOT_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
UUID_PATTERN = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-"
    r"[89ab][0-9a-f]{3}-[0-9a-f]{12}$",
    re.IGNORECASE,
)

ALLOWED_ROBOT_STATUSES = {"ONLINE", "BUSY", "CHARGING", "OFFLINE", "FAULT"}
ALLOWED_ROBOT_MODES = {"IDLE", "AUTO", "MANUAL", "PAUSED", "ESTOP", "FAULT"}
ALLOWED_SENSOR_STATES = {"OK", "WARNING", "OFFLINE"}
ALLOWED_EVENT_TYPES = {
    "MISSION_STARTED",
    "ARRIVED_SOURCE",
    "PACKAGE_LOADED",
    "DEPARTED_SOURCE",
    "ARRIVED_DESTINATION",
    "PACKAGE_RELEASED",
    "RETURNING_HOME",
    "MISSION_COMPLETED",
    "MISSION_FAILED",
    "PAUSED",
    "RESUMED",
    "ESTOP_TRIGGERED",
    "OBSTACLE_DETECTED",
    "LOW_BATTERY",
    "ESP32_DISCONNECTED",
    "BRIDGE_FAULT",
}
MISSION_EVENT_TYPES = {
    "MISSION_STARTED",
    "ARRIVED_SOURCE",
    "PACKAGE_LOADED",
    "DEPARTED_SOURCE",
    "ARRIVED_DESTINATION",
    "PACKAGE_RELEASED",
    "RETURNING_HOME",
    "MISSION_COMPLETED",
    "MISSION_FAILED",
}
ALLOWED_SEVERITIES = {"INFO", "WARNING", "ERROR", "CRITICAL"}
FUTURE_TOLERANCE = timedelta(minutes=5)
MAX_ROBOT_MESSAGE_BYTES = 32 * 1024


def validate_robot_id(robot_id: str) -> str:
    if not isinstance(robot_id, str) or not ROBOT_ID_PATTERN.fullmatch(robot_id):
        raise ValueError(
            "ROBOT_ID must match [a-z0-9][a-z0-9-]{0,63} for ingestion"
        )
    return robot_id


def as_uuid(value: Any, field: str) -> str:
    if not isinstance(value, str) or not UUID_PATTERN.fullmatch(value):
        raise ValueError(f"{field} must be a UUID")
    return value.lower()


def optional_uuid(value: Any, field: str) -> str | None:
    if value is None or value == "":
        return None
    return as_uuid(value, field)


def parse_timestamp(
    value: Any,
    field: str = "at",
    *,
    now: datetime | None = None,
) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be an ISO-8601 timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{field} must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None:
        raise ValueError(f"{field} must include a timezone")

    parsed = parsed.astimezone(timezone.utc)
    reference = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    if parsed > reference + FUTURE_TOLERANCE:
        raise ValueError(f"{field} is too far in the future")
    return parsed


def _as_number(value: Any, field: str, minimum: float, maximum: float) -> float:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
        or value < minimum
        or value > maximum
    ):
        raise ValueError(f"{field} must be between {minimum} and {maximum}")
    return value


def _enum(value: Any, field: str, allowed: set[str]) -> str:
    if not isinstance(value, str) or value not in allowed:
        raise ValueError(f"{field} contains an unsupported value")
    return value


def prepare_state_payload(
    state: Any,
    *,
    robot_id: str,
    firmware_version: str,
    max_age_seconds: float,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Validate a mission-manager snapshot and preserve its observation time."""

    if not isinstance(state, dict):
        raise ValueError("robot_state.json must contain a JSON object")
    if not math.isfinite(max_age_seconds) or max_age_seconds <= 0:
        raise ValueError("ROBOT_STATE_MAX_AGE_SECONDS must be greater than zero")

    required = {
        "status",
        "mode",
        "battery",
        "signal",
        "speedMps",
        "currentDeliveryId",
        "lidar",
        "camera",
        "esp32",
        "motorTempC",
        "at",
    }
    missing = sorted(required.difference(state))
    if missing:
        raise ValueError(f"robot_state.json is missing: {', '.join(missing)}")

    reference = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    observed_at = parse_timestamp(state["at"], now=reference)
    age_seconds = (reference - observed_at).total_seconds()
    if age_seconds > max_age_seconds:
        raise ValueError(
            "robot_state.json is stale "
            f"({age_seconds:.1f}s old; limit {max_age_seconds:.1f}s)"
        )

    payload = dict(state)
    payload["status"] = _enum(
        state["status"], "status", ALLOWED_ROBOT_STATUSES
    )
    payload["mode"] = _enum(state["mode"], "mode", ALLOWED_ROBOT_MODES)
    payload["battery"] = _as_number(state["battery"], "battery", 0, 100)
    payload["signal"] = _as_number(state["signal"], "signal", 0, 100)
    payload["speedMps"] = _as_number(state["speedMps"], "speedMps", 0, 5)
    payload["motorTempC"] = _as_number(
        state["motorTempC"], "motorTempC", -20, 150
    )
    for field in ("lidar", "camera", "esp32"):
        payload[field] = _enum(state[field], field, ALLOWED_SENSOR_STATES)

    payload["currentDeliveryId"] = optional_uuid(
        state["currentDeliveryId"], "currentDeliveryId"
    )
    if "locationId" in state:
        location_id = state["locationId"]
        if location_id is not None and (
            not isinstance(location_id, str) or not location_id.strip()
        ):
            raise ValueError("locationId must be a non-empty string or null")

    payload.update(
        {
            "schemaVersion": 1,
            "robotId": validate_robot_id(robot_id),
            "at": observed_at.isoformat(),
            "firmwareVersion": firmware_version,
        }
    )
    _validate_message_size(payload)
    return payload


def prepare_event_payload(
    event: Any,
    *,
    robot_id: str,
    now: datetime | None = None,
) -> tuple[dict[str, Any], bool]:
    """Normalize and validate an event-outbox record.

    Returns the normalized event and whether it must be persisted before
    publication (for example because an event ID or timestamp was generated).
    """

    if not isinstance(event, dict):
        raise ValueError("event outbox file must contain a JSON object")
    original = dict(event)
    normalized = dict(event)
    reference = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)

    event_type = _enum(event.get("type"), "type", ALLOWED_EVENT_TYPES)
    severity = _enum(event.get("severity"), "severity", ALLOWED_SEVERITIES)
    normalized["type"] = event_type
    normalized["severity"] = severity

    event_id = str(uuid.uuid4()) if "eventId" not in event else event["eventId"]
    normalized["eventId"] = as_uuid(event_id, "eventId")
    event_at = reference.isoformat() if "at" not in event else event["at"]
    normalized["at"] = parse_timestamp(event_at, now=reference).isoformat()

    for field in ("deliveryId", "commandId"):
        if field in event:
            normalized[field] = optional_uuid(event[field], field)

    if event_type in MISSION_EVENT_TYPES and not normalized.get("deliveryId"):
        raise ValueError(f"{event_type} requires deliveryId")
    if event_type in MISSION_EVENT_TYPES and not normalized.get("commandId"):
        raise ValueError(f"{event_type} requires commandId")

    detail = event.get("payload", {})
    if not isinstance(detail, dict):
        raise ValueError("event payload must be a JSON object")
    normalized["payload"] = detail
    normalized["schemaVersion"] = 1
    normalized["robotId"] = validate_robot_id(robot_id)
    _validate_message_size(normalized)
    return normalized, normalized != original


def event_order_key(
    event: dict[str, Any],
    *,
    file_mtime_ns: int = 0,
) -> tuple[datetime, int, str]:
    """Order queued events by occurrence time, then local write order."""

    return (
        parse_timestamp(event.get("at")),
        file_mtime_ns,
        as_uuid(event.get("eventId"), "eventId"),
    )


def _validate_message_size(payload: dict[str, Any]) -> None:
    encoded = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    if len(encoded) > MAX_ROBOT_MESSAGE_BYTES:
        raise ValueError(
            f"robot message exceeds {MAX_ROBOT_MESSAGE_BYTES} bytes"
        )
