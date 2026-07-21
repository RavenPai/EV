import { createClient } from "https://esm.sh/@supabase/supabase-js@2";

type JsonObject = Record<string, unknown>;
type MessageType = "acks" | "state" | "events" | "presence";

class HttpError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

const json = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });

const isObject = (value: unknown): value is JsonObject =>
  typeof value === "object" && value !== null && !Array.isArray(value);

const asString = (value: unknown, field: string): string => {
  if (typeof value !== "string" || !value.trim()) {
    throw new HttpError(400, `${field} must be a non-empty string`);
  }
  return value;
};

const asNumber = (
  value: unknown,
  field: string,
  minimum: number,
  maximum: number,
): number => {
  if (
    typeof value !== "number" ||
    !Number.isFinite(value) ||
    value < minimum ||
    value > maximum
  ) {
    throw new HttpError(
      400,
      `${field} must be between ${minimum} and ${maximum}`,
    );
  }
  return value;
};

const asInteger = (
  value: unknown,
  field: string,
  minimum: number,
  maximum: number,
): number => {
  const number = asNumber(value, field, minimum, maximum);
  if (!Number.isInteger(number)) {
    throw new HttpError(400, `${field} must be an integer`);
  }
  return number;
};

const asUuid = (value: unknown, field: string): string => {
  const text = asString(value, field);
  if (
    !/^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i
      .test(text)
  ) {
    throw new HttpError(400, `${field} must be a UUID`);
  }
  return text;
};

const optionalUuid = (value: unknown, field: string): string | null => {
  if (value === undefined || value === null || value === "") return null;
  return asUuid(value, field);
};

const asTimestamp = (
  value: unknown,
  field = "at",
  maximumAgeMs?: number,
): string => {
  const text = asString(value, field);
  if (
    !/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,9})?(?:Z|[+-]\d{2}:\d{2})$/.test(
      text,
    )
  ) {
    throw new HttpError(400, `${field} must be an ISO-8601 timestamp with a timezone`);
  }
  const timestamp = Date.parse(text);
  if (!Number.isFinite(timestamp)) {
    throw new HttpError(400, `${field} must be an ISO-8601 timestamp`);
  }
  if (timestamp > Date.now() + 5 * 60_000) {
    throw new HttpError(400, `${field} is too far in the future`);
  }
  if (maximumAgeMs !== undefined && timestamp < Date.now() - maximumAgeMs) {
    throw new HttpError(400, `${field} is too old`);
  }
  return new Date(timestamp).toISOString();
};

const assertPayloadKeys = (
  payload: JsonObject,
  required: readonly string[],
  optional: readonly string[] = [],
) => {
  const allowed = new Set([...required, ...optional]);
  const missing = required.filter((key) => !(key in payload));
  const unexpected = Object.keys(payload).filter((key) => !allowed.has(key));
  if (missing.length > 0) {
    throw new HttpError(400, `MQTT payload is missing: ${missing.join(", ")}`);
  }
  if (unexpected.length > 0) {
    throw new HttpError(
      400,
      `MQTT payload has unsupported fields: ${unexpected.join(", ")}`,
    );
  }
};

const optionalString = (
  value: unknown,
  field: string,
  maximumLength: number,
): string | null => {
  if (value === undefined || value === null) return null;
  if (typeof value !== "string" || value.length > maximumLength) {
    throw new HttpError(
      400,
      `${field} must be a string up to ${maximumLength} characters`,
    );
  }
  return value;
};

const asBrokerTimestamp = (value: unknown): string => {
  const timestamp = asInteger(
    value,
    "timestamp",
    0,
    Number.MAX_SAFE_INTEGER,
  );
  if (timestamp > Date.now() + 5 * 60_000) {
    throw new HttpError(400, "timestamp is too far in the future");
  }
  const observedAt = new Date(timestamp);
  if (!Number.isFinite(observedAt.getTime())) {
    throw new HttpError(400, "timestamp is outside the supported range");
  }
  return observedAt.toISOString();
};

const constantTimeEqual = (left: string, right: string): boolean => {
  const encoder = new TextEncoder();
  const a = encoder.encode(left);
  const b = encoder.encode(right);
  if (a.length !== b.length) return false;

  let difference = 0;
  for (let index = 0; index < a.length; index += 1) {
    difference |= a[index] ^ b[index];
  }
  return difference === 0;
};

const MAX_REQUEST_BYTES = 64 * 1024;

const readLimitedBody = async (request: Request): Promise<string> => {
  const declaredLength = request.headers.get("content-length");
  if (declaredLength !== null) {
    if (!/^\d+$/.test(declaredLength)) {
      throw new HttpError(400, "Invalid Content-Length");
    }
    if (Number(declaredLength) > MAX_REQUEST_BYTES) {
      throw new HttpError(413, "Request body is too large");
    }
  }

  if (!request.body) return "";
  const reader = request.body.getReader();
  const chunks: Uint8Array[] = [];
  let total = 0;

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    total += value.byteLength;
    if (total > MAX_REQUEST_BYTES) {
      await reader.cancel();
      throw new HttpError(413, "Request body is too large");
    }
    chunks.push(value);
  }

  const bytes = new Uint8Array(total);
  let offset = 0;
  for (const chunk of chunks) {
    bytes.set(chunk, offset);
    offset += chunk.byteLength;
  }

  try {
    return new TextDecoder("utf-8", { fatal: true }).decode(bytes);
  } catch {
    throw new HttpError(400, "Request body must be UTF-8");
  }
};

const parsePayload = (value: unknown): JsonObject => {
  let payload = value;
  if (typeof payload === "string") {
    try {
      payload = JSON.parse(payload);
    } catch {
      throw new HttpError(400, "MQTT payload is not valid JSON");
    }
  }
  if (!isObject(payload)) {
    throw new HttpError(400, "MQTT payload must be a JSON object");
  }
  return payload;
};

const allowedEventTypes = new Set([
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
]);

const missionEventTypes = new Set([
  "MISSION_STARTED",
  "ARRIVED_SOURCE",
  "PACKAGE_LOADED",
  "DEPARTED_SOURCE",
  "ARRIVED_DESTINATION",
  "PACKAGE_RELEASED",
  "RETURNING_HOME",
  "MISSION_COMPLETED",
  "MISSION_FAILED",
]);

Deno.serve(async (request) => {
  if (request.method !== "POST") {
    return json({ error: "Method not allowed" }, 405);
  }

  try {
    const expectedSecret = Deno.env.get("ROBOT_INGEST_SECRET") ?? "";
    const suppliedSecret = request.headers.get("x-emqx-secret") ?? "";
    if (
      !expectedSecret ||
      !suppliedSecret ||
      !constantTimeEqual(suppliedSecret, expectedSecret)
    ) {
      throw new HttpError(401, "Unauthorized");
    }

    const requestText = await readLimitedBody(request);
    let body: unknown;
    try {
      body = JSON.parse(requestText);
    } catch {
      throw new HttpError(400, "Request body is not valid JSON");
    }
    if (!isObject(body)) {
      throw new HttpError(400, "Webhook body must be a JSON object");
    }

    assertPayloadKeys(body, [
      "mqttMessageId",
      "topic",
      "payload",
      "clientid",
      "username",
      "qos",
      "timestamp",
    ]);
    const mqttMessageId = asString(body.mqttMessageId, "mqttMessageId");
    if (mqttMessageId.length > 256) {
      throw new HttpError(400, "mqttMessageId is too long");
    }

    const topic = asString(body.topic, "topic");
    const username = asString(body.username, "username");
    const clientId = asString(body.clientid, "clientid");
    if (asInteger(body.qos, "qos", 0, 2) !== 1) {
      throw new HttpError(400, "Robot messages require MQTT QoS 1");
    }
    const brokerObservedAt = asBrokerTimestamp(body.timestamp);
    const topicMatch = topic.match(
      /^miit\/robots\/([a-z0-9][a-z0-9-]{0,63})\/(acks|state|events|presence)$/,
    );
    if (!topicMatch) {
      throw new HttpError(400, "Unsupported MQTT topic");
    }

    const robotId = topicMatch[1];
    const messageType = topicMatch[2] as MessageType;
    if (username !== robotId) {
      throw new HttpError(403, "MQTT username does not match topic robot");
    }
    if (clientId !== robotId && !clientId.startsWith(`${robotId}-`)) {
      throw new HttpError(403, "MQTT client ID does not match topic robot");
    }

    const payload = parsePayload(body.payload);
    if (payload.schemaVersion !== 1) {
      throw new HttpError(400, "Unsupported schemaVersion");
    }
    if (payload.robotId !== robotId) {
      throw new HttpError(403, "Payload robotId does not match topic robot");
    }

    const admin = createClient(
      Deno.env.get("SUPABASE_URL")!,
      Deno.env.get("SUPABASE_SERVICE_ROLE_KEY")!,
      { auth: { persistSession: false, autoRefreshToken: false } },
    );
    const { data: robot, error: robotError } = await admin
      .from("robots")
      .select("id, current_delivery_id")
      .eq("id", robotId)
      .maybeSingle();
    if (robotError) throw robotError;
    if (!robot) throw new HttpError(404, "Unknown robot");

    let duplicate = false;
    let stale = false;

    if (messageType === "acks") {
      assertPayloadKeys(
        payload,
        ["schemaVersion", "robotId", "commandId", "status", "at"],
        ["reason"],
      );
      const commandId = asUuid(payload.commandId, "commandId");
      const status = asString(payload.status, "status");
      const at = asTimestamp(payload.at);
      if (
        !["ACKNOWLEDGED", "REJECTED", "COMPLETED", "FAILED"].includes(status)
      ) {
        throw new HttpError(400, "Invalid acknowledgement status");
      }

      const { data: command, error: commandError } = await admin
        .from("robot_commands")
        .select("id, robot_id")
        .eq("id", commandId)
        .maybeSingle();
      if (commandError) throw commandError;
      if (!command) throw new HttpError(404, "Unknown commandId");
      if (command.robot_id !== robotId) {
        throw new HttpError(403, "Command belongs to another robot");
      }

      const { data, error } = await admin.rpc("apply_robot_ack", {
        p_command_id: commandId,
        p_robot_id: robotId,
        p_status: status,
        p_reason: optionalString(payload.reason, "reason", 240) ?? "",
        p_occurred_at: at,
      });
      if (error) {
        if (error.code === "P0001") throw new HttpError(409, error.message);
        throw error;
      }
      duplicate = data === false;
    } else if (messageType === "state") {
      assertPayloadKeys(
        payload,
        [
          "schemaVersion",
          "robotId",
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
        ],
        ["locationId", "firmwareVersion"],
      );
      const status = asString(payload.status, "status");
      const mode = asString(payload.mode, "mode");
      if (
        !["ONLINE", "BUSY", "CHARGING", "OFFLINE", "FAULT"].includes(status)
      ) {
        throw new HttpError(400, "Invalid robot status");
      }
      if (
        !["IDLE", "AUTO", "MANUAL", "PAUSED", "ESTOP", "FAULT"].includes(mode)
      ) {
        throw new HttpError(400, "Invalid robot mode");
      }
      for (const field of ["lidar", "camera", "esp32"]) {
        if (
          typeof payload[field] !== "string" ||
          !["OK", "WARNING", "OFFLINE"].includes(payload[field] as string)
        ) {
          throw new HttpError(400, `Invalid ${field} state`);
        }
      }

      const locationId = optionalString(payload.locationId, "locationId", 128);
      if (locationId !== null && !locationId.trim()) {
        throw new HttpError(400, "locationId must be non-empty when provided");
      }
      const firmwareVersion = optionalString(
        payload.firmwareVersion,
        "firmwareVersion",
        80,
      );

      const { data, error } = await admin.rpc("apply_robot_state_observed", {
        p_robot_id: robotId,
        p_observed_at: asTimestamp(payload.at, "at", 60_000),
        p_broker_observed_at: brokerObservedAt,
        p_status: status,
        p_mode: mode,
        p_battery: asInteger(payload.battery, "battery", 0, 100),
        p_signal: asInteger(payload.signal, "signal", 0, 100),
        p_speed_mps: asNumber(payload.speedMps, "speedMps", 0, 5),
        p_location_id: locationId,
        p_current_delivery_id: optionalUuid(
          payload.currentDeliveryId,
          "currentDeliveryId",
        ),
        p_lidar: payload.lidar,
        p_camera: payload.camera,
        p_esp32: payload.esp32,
        p_motor_temp_c: asNumber(
          payload.motorTempC,
          "motorTempC",
          -20,
          150,
        ),
        p_firmware_version: firmwareVersion,
      });
      if (error) {
        if (error.code === "P0001") throw new HttpError(409, error.message);
        throw error;
      }
      stale = data === false;
    } else if (messageType === "events") {
      assertPayloadKeys(
        payload,
        ["schemaVersion", "robotId", "eventId", "type", "severity", "at"],
        ["deliveryId", "commandId", "payload"],
      );
      const eventType = asString(payload.type, "type");
      const severity = asString(payload.severity, "severity");
      if (!allowedEventTypes.has(eventType)) {
        throw new HttpError(400, "Unsupported event type");
      }
      if (!["INFO", "WARNING", "ERROR", "CRITICAL"].includes(severity)) {
        throw new HttpError(400, "Invalid event severity");
      }
      if (payload.payload !== undefined && !isObject(payload.payload)) {
        throw new HttpError(400, "Event payload must be a JSON object");
      }

      const deliveryId = missionEventTypes.has(eventType)
        ? asUuid(payload.deliveryId, "deliveryId")
        : optionalUuid(payload.deliveryId, "deliveryId");
      const commandId = missionEventTypes.has(eventType) || eventType === "RESUMED"
        ? asUuid(payload.commandId, "commandId")
        : optionalUuid(payload.commandId, "commandId");

      const { data, error } = await admin.rpc("apply_robot_event", {
        p_message_id: asUuid(payload.eventId, "eventId"),
        p_robot_id: robotId,
        p_delivery_id: deliveryId,
        p_command_id: commandId,
        p_event_type: eventType,
        p_severity: severity,
        p_payload: isObject(payload.payload) ? payload.payload : {},
        p_occurred_at: asTimestamp(payload.at),
      });
      if (error) {
        if (error.code === "P0001") {
          throw new HttpError(409, error.message);
        }
        throw error;
      }
      duplicate = data === false;
    } else {
      assertPayloadKeys(
        payload,
        ["schemaVersion", "robotId", "online", "at"],
        ["firmwareVersion"],
      );
      if (typeof payload.online !== "boolean") {
        throw new HttpError(400, "online must be a boolean");
      }
      asTimestamp(payload.at);
      if (
        payload.online &&
        new Date(brokerObservedAt).getTime() < Date.now() - 60_000
      ) {
        throw new HttpError(400, "online presence is too old");
      }

      // Presence is bridge connectivity, not operational telemetry. The RPC
      // records it separately and fails the robot safe when telemetry expires.
      // p_observed_at is required so out-of-order or replayed presence
      // messages cannot move bridge_last_seen backwards.
      const { data, error } = await admin.rpc("apply_robot_presence", {
        p_robot_id: robotId,
        p_online: payload.online,
        p_firmware_version: optionalString(
          payload.firmwareVersion,
          "firmwareVersion",
          80,
        ),
        // The broker action time remains fresh for Last Will messages even
        // though their payload timestamp was fixed when the client connected.
        p_observed_at: brokerObservedAt,
      });
      if (error) {
        if (error.code === "P0001") throw new HttpError(409, error.message);
        throw error;
      }
      stale = data === false;
    }

    return json({
      accepted: true,
      robotId,
      messageType,
      duplicate,
      stale,
    });
  } catch (error) {
    console.error(error);
    if (error instanceof HttpError) {
      return json({ error: error.message }, error.status);
    }
    return json({ error: "Ingestion failed" }, 500);
  }
});
