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

const asTimestamp = (value: unknown, field = "at"): string => {
  const text = asString(value, field);
  const timestamp = Date.parse(text);
  if (!Number.isFinite(timestamp)) {
    throw new HttpError(400, `${field} must be an ISO-8601 timestamp`);
  }
  if (timestamp > Date.now() + 5 * 60_000) {
    throw new HttpError(400, `${field} is too far in the future`);
  }
  return new Date(timestamp).toISOString();
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

Deno.serve(async (request) => {
  if (request.method !== "POST") {
    return json({ error: "Method not allowed" }, 405);
  }

  try {
    const contentLength = Number(request.headers.get("content-length") ?? "0");
    if (contentLength > 64 * 1024) {
      throw new HttpError(413, "Request body is too large");
    }

    const expectedSecret = Deno.env.get("ROBOT_INGEST_SECRET") ?? "";
    const suppliedSecret = request.headers.get("x-emqx-secret") ?? "";
    if (
      !expectedSecret ||
      !suppliedSecret ||
      !constantTimeEqual(suppliedSecret, expectedSecret)
    ) {
      throw new HttpError(401, "Unauthorized");
    }

    let body: unknown;
    try {
      body = await request.json();
    } catch {
      throw new HttpError(400, "Request body is not valid JSON");
    }
    if (!isObject(body)) {
      throw new HttpError(400, "Webhook body must be a JSON object");
    }

    const topic = asString(body.topic, "topic");
    const username = asString(body.username, "username");
    const clientId = asString(body.clientid, "clientid");
    asNumber(body.qos, "qos", 0, 2);
    asNumber(body.timestamp, "timestamp", 0, Number.MAX_SAFE_INTEGER);
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
      .select("id, status, current_delivery_id")
      .eq("id", robotId)
      .maybeSingle();
    if (robotError) throw robotError;
    if (!robot) throw new HttpError(404, "Unknown robot");

    let duplicate = false;
    let stale = false;

    if (messageType === "acks") {
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

      const commandPatch: JsonObject = {
        status,
        acknowledged_at: at,
        result: {
          reason: typeof payload.reason === "string" ? payload.reason : "",
          at,
        },
      };

      const { error } = await admin
        .from("robot_commands")
        .update(commandPatch)
        .eq("id", commandId)
        .in("status", ["PENDING", "PUBLISHED", "ACKNOWLEDGED"]);
      if (error) throw error;
    } else if (messageType === "state") {
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

      const { data, error } = await admin.rpc("apply_robot_state", {
        p_robot_id: robotId,
        p_observed_at: asTimestamp(payload.at),
        p_status: status,
        p_mode: mode,
        p_battery: Math.round(asNumber(payload.battery, "battery", 0, 100)),
        p_signal: Math.round(asNumber(payload.signal, "signal", 0, 100)),
        p_speed_mps: asNumber(payload.speedMps, "speedMps", 0, 5),
        p_location_id: typeof payload.locationId === "string"
          ? payload.locationId
          : null,
        p_current_delivery_id: payload.currentDeliveryId === undefined
          ? robot.current_delivery_id
          : optionalUuid(payload.currentDeliveryId, "currentDeliveryId"),
        p_lidar: payload.lidar,
        p_camera: payload.camera,
        p_esp32: payload.esp32,
        p_motor_temp_c: asNumber(
          payload.motorTempC,
          "motorTempC",
          -20,
          150,
        ),
        p_firmware_version: typeof payload.firmwareVersion === "string"
          ? payload.firmwareVersion.slice(0, 80)
          : null,
      });
      if (error) throw error;
      stale = data === false;
    } else if (messageType === "events") {
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

      const { data, error } = await admin.rpc("apply_robot_event", {
        p_message_id: asUuid(payload.eventId, "eventId"),
        p_robot_id: robotId,
        p_delivery_id: optionalUuid(payload.deliveryId, "deliveryId"),
        p_command_id: optionalUuid(payload.commandId, "commandId"),
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
      if (typeof payload.online !== "boolean") {
        throw new HttpError(400, "online must be a boolean");
      }
      if (payload.at !== undefined) asTimestamp(payload.at);

      const now = new Date().toISOString();
      const presencePatch: JsonObject = {
        last_seen: now,
        updated_at: now,
      };
      if (typeof payload.firmwareVersion === "string") {
        presencePatch.firmware_version = payload.firmwareVersion.slice(0, 80);
      }

      if (payload.online) {
        if (robot.status === "OFFLINE") {
          presencePatch.status = robot.current_delivery_id ? "BUSY" : "ONLINE";
        }
      } else {
        Object.assign(presencePatch, {
          status: "OFFLINE",
          speed_mps: 0,
          signal: 0,
          lidar: "OFFLINE",
          camera: "OFFLINE",
          esp32: "OFFLINE",
        });
      }

      const { error } = await admin
        .from("robots")
        .update(presencePatch)
        .eq("id", robotId);
      if (error) throw error;
    }

    if (messageType !== "state" && messageType !== "presence") {
      const { error } = await admin
        .from("robots")
        .update({
          last_seen: new Date().toISOString(),
          updated_at: new Date().toISOString(),
        })
        .eq("id", robotId);
      if (error) throw error;
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
    return json(
      { error: error instanceof Error ? error.message : "Ingestion failed" },
      500,
    );
  }
});
