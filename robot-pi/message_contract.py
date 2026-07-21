"""Pure validation helpers for Pi-to-cloud MQTT messages.

Keep these checks aligned with ``ingest-robot-message`` so malformed local
state or event files are rejected before EMQX accepts them.
"""

from __future__ import annotations

import json
import math
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
ALLOWED_COMMANDS = {"START_MISSION", "PAUSE", "RESUME", "RETURN_HOME", "ESTOP"}
ALLOWED_ACK_STATUSES = {"ACKNOWLEDGED", "REJECTED", "COMPLETED", "FAILED"}
COMMAND_TTL_SECONDS = {
    "START_MISSION": 300,
    "PAUSE": 300,
    "RESUME": 300,
    "RETURN_HOME": 300,
    "ESTOP": 60,
}
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


def _as_integer(value: Any, field: str, minimum: int, maximum: int) -> int:
    value = _as_number(value, field, minimum, maximum)
    if not isinstance(value, int):
        raise ValueError(f"{field} must be an integer")
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
    allowed = required | {"locationId"}
    missing = sorted(required.difference(state))
    unexpected = sorted(set(state).difference(allowed))
    if missing:
        raise ValueError(f"robot_state.json is missing: {', '.join(missing)}")
    if unexpected:
        raise ValueError(
            f"robot_state.json has unsupported fields: {', '.join(unexpected)}"
        )

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
    payload["battery"] = _as_integer(state["battery"], "battery", 0, 100)
    payload["signal"] = _as_integer(state["signal"], "signal", 0, 100)
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
            not isinstance(location_id, str)
            or not location_id.strip()
            or len(location_id) > 128
        ):
            raise ValueError(
                "locationId must be a non-empty string up to 128 characters or null"
            )
    if (
        not isinstance(firmware_version, str)
        or not firmware_version.strip()
        or len(firmware_version) > 80
    ):
        raise ValueError(
            "firmwareVersion must be a non-empty string up to 80 characters"
        )

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


def prepare_command_envelope(
    envelope: Any,
    *,
    robot_id: str,
    now: datetime | None = None,
) -> tuple[dict[str, Any], datetime, datetime]:
    """Validate a cloud command without deciding whether an expired duplicate is safe."""

    if not isinstance(envelope, dict):
        raise ValueError("command envelope must be a JSON object")
    required = {
        "schemaVersion",
        "commandId",
        "robotId",
        "command",
        "payload",
        "issuedAt",
        "expiresAt",
    }
    missing = sorted(required.difference(envelope))
    unexpected = sorted(set(envelope).difference(required))
    if missing:
        raise ValueError(f"command envelope is missing: {', '.join(missing)}")
    if unexpected:
        raise ValueError(f"command envelope has unsupported fields: {', '.join(unexpected)}")
    if envelope["schemaVersion"] != 1:
        raise ValueError("unsupported command schemaVersion")
    if envelope["robotId"] != validate_robot_id(robot_id):
        raise ValueError("command robotId does not match this robot")

    command_id = as_uuid(envelope["commandId"], "commandId")
    command = _enum(envelope["command"], "command", ALLOWED_COMMANDS)
    payload = envelope["payload"]
    if not isinstance(payload, dict):
        raise ValueError("command payload must be a JSON object")

    reference = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    issued_at = parse_timestamp(envelope["issuedAt"], "issuedAt", now=reference)
    expires_at = parse_timestamp(envelope["expiresAt"], "expiresAt", now=reference)
    lifetime = (expires_at - issued_at).total_seconds()
    if lifetime <= 0 or lifetime > COMMAND_TTL_SECONDS[command]:
        raise ValueError(f"{command} command lifetime is invalid")

    if command == "START_MISSION":
        expected_payload = {
            "sourceLocationId",
            "destinationLocationId",
            "mapVersion",
            "deliveryId",
        }
        if set(payload) != expected_payload:
            raise ValueError("START_MISSION payload fields are invalid")
        normalized_payload = dict(payload)
        normalized_payload["deliveryId"] = as_uuid(payload["deliveryId"], "deliveryId")
        for field in ("sourceLocationId", "destinationLocationId", "mapVersion"):
            value = payload[field]
            if not isinstance(value, str) or not value.strip() or len(value) > 128:
                raise ValueError(f"{field} must be a non-empty string up to 128 characters")
    else:
        if set(payload).difference({"reason"}):
            raise ValueError(f"{command} payload fields are invalid")
        normalized_payload = dict(payload)
        if "reason" in payload and (
            not isinstance(payload["reason"], str) or len(payload["reason"]) > 240
        ):
            raise ValueError("reason must be a string up to 240 characters")

    normalized = {
        "schemaVersion": 1,
        "commandId": command_id,
        "robotId": robot_id,
        "command": command,
        "payload": normalized_payload,
        "issuedAt": issued_at.isoformat(),
        "expiresAt": expires_at.isoformat(),
    }
    _validate_message_size(normalized)
    return normalized, issued_at, expires_at


def validate_command_transport(
    *,
    topic: Any,
    expected_topic: str,
    qos: Any,
    retain: Any,
) -> None:
    """Reject command deliveries that cannot provide the production guarantees."""

    if topic != expected_topic:
        raise ValueError("command arrived on an unexpected MQTT topic")
    if qos != 1:
        raise ValueError("commands require MQTT QoS 1")
    if bool(retain):
        raise ValueError("retained commands are not accepted")


def command_event_id(command_id: str, event_type: str) -> str:
    """Create a stable event UUID for a side effect tied to one cloud command."""

    normalized_id = as_uuid(command_id, "commandId")
    if not isinstance(event_type, str) or not event_type:
        raise ValueError("event type must be a non-empty string")
    return str(uuid.uuid5(uuid.UUID(normalized_id), f"miit-rover:{event_type}"))


def prepare_ack_payload(
    *,
    robot_id: str,
    command_id: str,
    status: str,
    reason: str = "",
    at: str | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Create the durable acknowledgement record published by the Pi."""

    status = _enum(status, "status", ALLOWED_ACK_STATUSES)
    if not isinstance(reason, str):
        raise ValueError("acknowledgement reason must be a string")
    reference = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    observed_at = parse_timestamp(
        reference.isoformat() if at is None else at,
        "at",
        now=reference,
    )
    payload = {
        "schemaVersion": 1,
        "commandId": as_uuid(command_id, "commandId"),
        "robotId": validate_robot_id(robot_id),
        "status": status,
        "reason": reason[:240],
        "at": observed_at.isoformat(),
    }
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
    allowed = {
        "schemaVersion",
        "robotId",
        "eventId",
        "deliveryId",
        "commandId",
        "type",
        "severity",
        "at",
        "payload",
    }
    unexpected = sorted(set(event).difference(allowed))
    if unexpected:
        raise ValueError(
            f"event outbox has unsupported fields: {', '.join(unexpected)}"
        )
    if "schemaVersion" in event and event["schemaVersion"] != 1:
        raise ValueError("event schemaVersion is unsupported")
    if "robotId" in event and event["robotId"] != validate_robot_id(robot_id):
        raise ValueError("event robotId does not match this robot")
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
    if event_type == "RESUMED" and not normalized.get("commandId"):
        raise ValueError("RESUMED requires commandId")

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
    try:
        encoded = json.dumps(
            payload,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ValueError("robot message must contain finite JSON values") from exc
    if len(encoded) > MAX_ROBOT_MESSAGE_BYTES:
        raise ValueError(
            f"robot message exceeds {MAX_ROBOT_MESSAGE_BYTES} bytes"
        )
