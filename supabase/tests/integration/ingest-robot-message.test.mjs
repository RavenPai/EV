import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { randomUUID } from "node:crypto";
import { dirname, resolve } from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

import { createClient } from "@supabase/supabase-js";

const TEST_ROBOT_ID = "robot-01";
const OTHER_ROBOT_ID = "robot-02";
const projectRoot = resolve(
  dirname(fileURLToPath(import.meta.url)),
  "../../..",
);

const firstEnvironmentValue = (...names) => {
  for (const name of names) {
    const value = process.env[name]?.trim();
    if (value) return value;
  }
  return undefined;
};

const parseJsonObject = (output) => {
  const cleaned = output
    .replace(/\u001b\[[0-?]*[ -/]*[@-~]/g, "")
    .trim();

  try {
    const parsed = JSON.parse(cleaned);
    if (parsed && typeof parsed === "object" && !Array.isArray(parsed)) {
      return parsed;
    }
  } catch {
    // Some CLI versions print a notice before their JSON result.
  }

  for (
    let start = cleaned.indexOf("{");
    start >= 0;
    start = cleaned.indexOf("{", start + 1)
  ) {
    for (
      let end = cleaned.lastIndexOf("}");
      end > start;
      end = cleaned.lastIndexOf("}", end - 1)
    ) {
      try {
        const parsed = JSON.parse(cleaned.slice(start, end + 1));
        if (parsed && typeof parsed === "object" && !Array.isArray(parsed)) {
          return parsed;
        }
      } catch {
        // Continue until a complete JSON object is found.
      }
    }
  }

  throw new Error("Supabase CLI status did not return a JSON object");
};

const normalizedKey = (value) =>
  String(value).toLowerCase().replace(/[^a-z0-9]/g, "");

const findStatusValue = (status, candidateNames) => {
  const candidates = new Set(candidateNames.map(normalizedKey));
  const queue = [status];

  while (queue.length > 0) {
    const current = queue.shift();
    if (!current || typeof current !== "object") continue;

    for (const [key, value] of Object.entries(current)) {
      if (
        candidates.has(normalizedKey(key)) &&
        typeof value === "string" &&
        value.trim()
      ) {
        return value.trim();
      }
      if (value && typeof value === "object") queue.push(value);
    }
  }

  return undefined;
};

const readLocalSupabaseStatus = () => {
  const cliEntrypoint = resolve(
    projectRoot,
    "node_modules/supabase/dist/supabase.js",
  );

  try {
    const output = execFileSync(
      process.execPath,
      [cliEntrypoint, "status", "-o", "json"],
      {
        cwd: projectRoot,
        encoding: "utf8",
        stdio: ["ignore", "pipe", "pipe"],
      },
    );
    return parseJsonObject(output);
  } catch (error) {
    const detail = String(error?.stderr ?? "")
      .trim()
      .split(/\r?\n/)
      .find(Boolean);
    throw new Error(
      "Unable to read the local Supabase stack. Start it before running " +
        `this test${detail ? ` (${detail})` : ""}.`,
    );
  }
};

const discoverConfiguration = () => {
  const environment = {
    url: firstEnvironmentValue(
      "SUPABASE_TEST_URL",
      "LOCAL_SUPABASE_URL",
      "SUPABASE_URL",
      "API_URL",
    ),
    anonKey: firstEnvironmentValue(
      "SUPABASE_TEST_ANON_KEY",
      "LOCAL_SUPABASE_ANON_KEY",
      "SUPABASE_ANON_KEY",
      "ANON_KEY",
    ),
    serviceRoleKey: firstEnvironmentValue(
      "SUPABASE_TEST_SERVICE_ROLE_KEY",
      "LOCAL_SUPABASE_SERVICE_ROLE_KEY",
      "SUPABASE_SERVICE_ROLE_KEY",
      "SERVICE_ROLE_KEY",
    ),
  };

  let status;
  if (!environment.url || !environment.anonKey || !environment.serviceRoleKey) {
    status = readLocalSupabaseStatus();
  }

  const configuration = {
    url:
      environment.url ??
      findStatusValue(status, ["API_URL", "SUPABASE_URL"]),
    anonKey:
      environment.anonKey ??
      findStatusValue(status, [
        "ANON_KEY",
        "SUPABASE_ANON_KEY",
        "PUBLISHABLE_KEY",
      ]),
    serviceRoleKey:
      environment.serviceRoleKey ??
      findStatusValue(status, [
        "SERVICE_ROLE_KEY",
        "SUPABASE_SERVICE_ROLE_KEY",
        "SECRET_KEY",
      ]),
    ingestSecret: firstEnvironmentValue(
      "ROBOT_INGEST_TEST_SECRET",
      "TEST_ROBOT_INGEST_SECRET",
      "ROBOT_INGEST_SECRET",
    ),
  };

  for (const [name, value] of Object.entries(configuration)) {
    if (!value) {
      throw new Error(
        `Missing ${name}. Start the local Supabase stack and provide ` +
          "ROBOT_INGEST_SECRET for the locally served Edge Function.",
      );
    }
  }

  let parsedUrl;
  try {
    parsedUrl = new URL(configuration.url);
  } catch {
    throw new Error("The discovered Supabase URL is invalid");
  }

  if (
    !["localhost", "127.0.0.1", "::1", "[::1]"].includes(
      parsedUrl.hostname.toLowerCase(),
    )
  ) {
    throw new Error(
      `Refusing to run integration fixtures against non-local host ` +
        `"${parsedUrl.hostname}".`,
    );
  }

  return {
    ...configuration,
    url: configuration.url.replace(/\/+$/, ""),
  };
};

const requireData = (result, operation) => {
  if (result.error) {
    throw new Error(
      `${operation}: ${result.error.message}` +
        (result.error.code ? ` (${result.error.code})` : ""),
    );
  }
  return result.data;
};

const actionBody = (
  messageType,
  payload,
  {
    topicRobotId = TEST_ROBOT_ID,
    username = TEST_ROBOT_ID,
    clientid = `${TEST_ROBOT_ID}-pi`,
    qos = 1,
    timestamp = Date.now(),
    mqttMessageId = randomUUID().replaceAll("-", ""),
  } = {},
) => ({
  mqttMessageId,
  topic: `miit/robots/${topicRobotId}/${messageType}`,
  payload,
  clientid,
  username,
  qos,
  timestamp,
});

const presencePayload = (overrides = {}) => ({
  schemaVersion: 1,
  robotId: TEST_ROBOT_ID,
  online: true,
  at: new Date().toISOString(),
  firmwareVersion: "integration-test",
  ...overrides,
});

const robotRestorePatch = (snapshot) => ({
  status: snapshot.status,
  mode: snapshot.mode,
  battery: snapshot.battery,
  location_id: snapshot.location_id,
  signal: snapshot.signal,
  speed_mps: snapshot.speed_mps,
  lidar: snapshot.lidar,
  camera: snapshot.camera,
  esp32: snapshot.esp32,
  motor_temp_c: snapshot.motor_temp_c,
  map_version: snapshot.map_version,
  current_delivery_id: snapshot.current_delivery_id,
  last_seen: snapshot.last_seen,
  telemetry_at: snapshot.telemetry_at,
  telemetry_received_at: snapshot.telemetry_received_at,
  firmware_version: snapshot.firmware_version,
  bridge_last_seen: snapshot.bridge_last_seen,
  bridge_online: snapshot.bridge_online,
  control_event_at: snapshot.control_event_at,
  control_event_received_at: snapshot.control_event_received_at,
  safety_latched_at: snapshot.safety_latched_at,
});

test(
  "EMQX robot messages are authenticated, validated, and persisted",
  { concurrency: false, timeout: 120_000 },
  async (t) => {
    const configuration = discoverConfiguration();
    const endpoint =
      `${configuration.url}/functions/v1/ingest-robot-message`;
    const admin = createClient(
      configuration.url,
      configuration.serviceRoleKey,
      {
        auth: { autoRefreshToken: false, persistSession: false },
      },
    );

    let authUserId;
    let deliveryId;
    let concurrentDeliveryId;
    let robotSnapshot;
    let signedInClient;
    const commandIds = new Set();
    const eventMessageIds = new Set();

    const postAction = async (
      body,
      secret = configuration.ingestSecret,
    ) => {
      const headers = { "Content-Type": "application/json" };
      if (secret !== null) headers["x-emqx-secret"] = secret;

      const response = await fetch(endpoint, {
        method: "POST",
        headers,
        body: JSON.stringify(body),
        signal: AbortSignal.timeout(15_000),
      });
      const text = await response.text();
      let responseBody = text;
      try {
        responseBody = JSON.parse(text);
      } catch {
        // Preserve a non-JSON gateway response for assertion diagnostics.
      }
      return { response, body: responseBody };
    };

    const expectStatus = async (body, expectedStatus, secret) => {
      const result = await postAction(body, secret);
      assert.equal(
        result.response.status,
        expectedStatus,
        `Expected HTTP ${expectedStatus}; received ${result.response.status}: ` +
          JSON.stringify(result.body),
      );
      return result.body;
    };

    const readRobot = async () =>
      requireData(
        await admin
          .from("robots")
          .select("*")
          .eq("id", TEST_ROBOT_ID)
          .single(),
        "read test robot",
      );

    const cleanup = async () => {
      const errors = [];
      const attempt = async (operation, callback) => {
        try {
          await callback();
        } catch (error) {
          errors.push(
            new Error(
              `${operation}: ${
                error instanceof Error ? error.message : String(error)
              }`,
            ),
          );
        }
      };

      if (robotSnapshot) {
        await attempt("clear fixture safety latch", async () => {
          const currentRobot = requireData(
            await admin
              .from("robots")
              .select("*")
              .eq("id", TEST_ROBOT_ID)
              .single(),
            "read robot safety latch during cleanup",
          );
          if (
            !["ESTOP", "FAULT"].includes(currentRobot.mode) ||
            ["ESTOP", "FAULT"].includes(robotSnapshot.mode)
          ) {
            return;
          }

          const cleanupCommandId = randomUUID();
          const cleanupEventId = randomUUID();
          commandIds.add(cleanupCommandId);
          eventMessageIds.add(cleanupEventId);
          const controlTime = currentRobot.control_event_at
            ? new Date(currentRobot.control_event_at).getTime()
            : 0;
          const latchTime = currentRobot.safety_latched_at
            ? new Date(currentRobot.safety_latched_at).getTime()
            : 0;
          const telemetryTime = currentRobot.telemetry_at
            ? new Date(currentRobot.telemetry_at).getTime()
            : 0;
          const bridgeTime = currentRobot.bridge_last_seen
            ? new Date(currentRobot.bridge_last_seen).getTime()
            : 0;
          const safeStateTime = Math.max(Date.now(), telemetryTime + 1);
          const safeBridgeTime = Math.max(Date.now(), bridgeTime + 1);

          requireData(
            await admin.rpc("apply_robot_state_observed", {
              p_robot_id: TEST_ROBOT_ID,
              p_observed_at: new Date(safeStateTime).toISOString(),
              p_broker_observed_at: new Date(safeBridgeTime).toISOString(),
              p_status: "FAULT",
              p_mode: currentRobot.mode,
              p_battery: Math.max(Number(currentRobot.battery) || 0, 20),
              p_signal: Math.max(Number(currentRobot.signal) || 0, 1),
              p_speed_mps: 0,
              p_location_id: currentRobot.location_id ?? "loc-home",
              p_current_delivery_id: null,
              p_lidar: "OK",
              p_camera: "OK",
              p_esp32: "OK",
              p_motor_temp_c: Number(currentRobot.motor_temp_c) || 30,
              p_firmware_version: currentRobot.firmware_version ??
                "integration-cleanup",
            }),
            "record fresh safe state before cleanup RESUME",
          );

          const issuedTime = Math.max(
            Date.now(),
            controlTime + 1,
            latchTime + 1,
          );
          const resetTime = Math.max(issuedTime + 1, safeStateTime + 1);
          const issuedAt = new Date(issuedTime).toISOString();
          const resetAt = new Date(resetTime).toISOString();

          requireData(
            await admin.from("robot_commands").insert({
              id: cleanupCommandId,
              robot_id: TEST_ROBOT_ID,
              command_type: "RESUME",
              payload: {
                source: "integration-cleanup",
                localSafetyChecksPassed: true,
              },
              status: "PUBLISHED",
              issued_by: authUserId,
              issued_at: issuedAt,
              published_at: issuedAt,
              expires_at: new Date(resetTime + 5 * 60_000).toISOString(),
            }),
            "create cleanup RESUME command",
          );
          requireData(
            await admin.rpc("apply_robot_event", {
              p_message_id: cleanupEventId,
              p_robot_id: TEST_ROBOT_ID,
              p_delivery_id: null,
              p_command_id: cleanupCommandId,
              p_event_type: "RESUMED",
              p_severity: "INFO",
              p_payload: {
                source: "integration-cleanup",
                localSafetyChecksPassed: true,
              },
              p_occurred_at: resetAt,
            }),
            "clear robot safety latch during cleanup",
          );
        });

        await attempt("restore robot-01", async () => {
          requireData(
            await admin
              .from("robots")
              .update(robotRestorePatch(robotSnapshot))
              .eq("id", TEST_ROBOT_ID),
            "restore robot-01",
          );
        });
      }

      const fixtureDeliveryIds = [
        deliveryId,
        concurrentDeliveryId,
      ].filter(Boolean);

      if (fixtureDeliveryIds.length > 0) {
        await attempt("remove fixture notifications", async () => {
          requireData(
            await admin
              .from("notifications")
              .delete()
              .in("delivery_id", fixtureDeliveryIds),
            "remove fixture notifications",
          );
        });

        // Presence/offline and expiry workflows create trusted system events
        // without an MQTT message_id. Remove those by fixture delivery before
        // deleting the delivery rows they reference.
        await attempt("remove delivery-scoped robot events", async () => {
          requireData(
            await admin
              .from("robot_events")
              .delete()
              .in("delivery_id", fixtureDeliveryIds),
            "remove delivery-scoped robot events",
          );
        });
      }

      if (eventMessageIds.size > 0) {
        await attempt("remove fixture robot events", async () => {
          requireData(
            await admin
              .from("robot_events")
              .delete()
              .in("message_id", [...eventMessageIds]),
            "remove fixture robot events",
          );
        });
      }

      if (commandIds.size > 0) {
        await attempt("remove fixture robot commands", async () => {
          requireData(
            await admin
              .from("robot_commands")
              .delete()
              .in("id", [...commandIds]),
            "remove fixture robot commands",
          );
        });
      }

      if (fixtureDeliveryIds.length > 0) {
        await attempt("remove fixture deliveries", async () => {
          requireData(
            await admin
              .from("deliveries")
              .delete()
              .in("id", fixtureDeliveryIds),
            "remove fixture deliveries",
          );
        });
      }

      if (signedInClient) {
        await attempt("sign out fixture user", async () => {
          const { error } = await signedInClient.auth.signOut();
          if (error) throw error;
        });
      }

      if (authUserId) {
        await attempt("remove fixture auth user", async () => {
          const result = await admin.auth.admin.deleteUser(authUserId);
          if (result.error) throw result.error;
        });
      }

      if (errors.length > 0) {
        throw new AggregateError(errors, "Integration fixture cleanup failed");
      }
    };

    try {
      robotSnapshot = await readRobot();
      requireData(
        await admin
          .from("robots")
          .update({
            status: "ONLINE",
            mode: "IDLE",
            current_delivery_id: null,
            speed_mps: 0,
            telemetry_at: new Date().toISOString(),
            telemetry_received_at: new Date().toISOString(),
            bridge_last_seen: new Date().toISOString(),
            bridge_online: true,
            control_event_at: null,
            control_event_received_at: null,
            safety_latched_at: null,
            last_seen: new Date().toISOString(),
          })
          .eq("id", TEST_ROBOT_ID),
        "prepare test robot",
      );

      const email = `emqx-ingest-${randomUUID()}@example.test`;
      const password = `Integration-${randomUUID()}-A1!`;
      const fullName = "EMQX Integration User";
      const createdUser = requireData(
        await admin.auth.admin.createUser({
          email,
          password,
          email_confirm: true,
          user_metadata: { full_name: fullName },
        }),
        "create fixture auth user",
      );
      authUserId = createdUser.user.id;
      requireData(
        await admin
          .from("profiles")
          .update({ role: "OPERATOR" })
          .eq("id", authUserId),
        "promote fixture user to operator",
      );

      const signInClient = createClient(
        configuration.url,
        configuration.anonKey,
        {
          auth: { autoRefreshToken: false, persistSession: false },
        },
      );
      signedInClient = signInClient;
      requireData(
        await signInClient.auth.signInWithPassword({ email, password }),
        "sign in fixture auth user",
      );

      deliveryId = randomUUID();
      const trackingCode =
        `INT-${randomUUID().replaceAll("-", "").slice(0, 12).toUpperCase()}`;
      const delivery = requireData(
        await signInClient
          .from("deliveries")
          .insert({
            id: deliveryId,
            tracking_code: trackingCode,
            requester_id: authUserId,
            requester_name: "Untrusted request label",
            requester_email: "untrusted@example.test",
            recipient_name: "Integration Recipient",
            recipient_phone: "+959000000000",
            source_id: "loc-fcs",
            destination_id: "loc-library",
            item_name: "Integration test package",
            category: "DOCUMENT",
            weight_kg: 1.25,
            priority: "NORMAL",
            status: "REQUESTED",
          })
          .select("*")
          .single(),
        "create fixture delivery",
      );
      assert.equal(delivery.requester_id, authUserId);
      assert.equal(delivery.requester_name, fullName);
      assert.equal(delivery.requester_email, email);

      requireData(
        await admin
          .from("deliveries")
          .update({ status: "ASSIGNED", robot_id: TEST_ROBOT_ID })
          .eq("id", deliveryId),
        "assign fixture delivery",
      );

      const commandId = randomUUID();
      const completionCommandId = randomUUID();
      const otherRobotCommandId = randomUUID();
      const resumeCommandId = randomUUID();
      const oldResumeCommandId = randomUUID();
      commandIds.add(commandId);
      commandIds.add(completionCommandId);
      commandIds.add(otherRobotCommandId);
      commandIds.add(resumeCommandId);
      commandIds.add(oldResumeCommandId);
      const expiresAt = new Date(Date.now() + 5 * 60_000).toISOString();
      const issuedAt = new Date().toISOString();

      requireData(
        await admin.from("robot_commands").insert([
          {
            id: commandId,
            robot_id: TEST_ROBOT_ID,
            delivery_id: deliveryId,
            command_type: "START_MISSION",
            payload: {
              schemaVersion: 1,
              commandId,
              robotId: TEST_ROBOT_ID,
              command: "START_MISSION",
              payload: {
                sourceLocationId: "loc-fcs",
                destinationLocationId: "loc-library",
                mapVersion: "miit-campus-v1",
                deliveryId,
              },
              issuedAt,
              expiresAt,
            },
            status: "PUBLISHED",
            issued_by: authUserId,
            expires_at: expiresAt,
            published_at: issuedAt,
          },
          {
            id: completionCommandId,
            robot_id: TEST_ROBOT_ID,
            command_type: "PAUSE",
            payload: {
              schemaVersion: 1,
              commandId: completionCommandId,
              robotId: TEST_ROBOT_ID,
              command: "PAUSE",
              payload: {},
              issuedAt,
              expiresAt,
            },
            status: "PUBLISHED",
            issued_by: authUserId,
            expires_at: expiresAt,
            published_at: issuedAt,
          },
          {
            id: otherRobotCommandId,
            robot_id: OTHER_ROBOT_ID,
            command_type: "PAUSE",
            payload: {
              schemaVersion: 1,
              commandId: otherRobotCommandId,
              robotId: OTHER_ROBOT_ID,
              command: "PAUSE",
              payload: {},
              issuedAt,
              expiresAt,
            },
            status: "PUBLISHED",
            issued_by: authUserId,
            expires_at: expiresAt,
            published_at: issuedAt,
          },
        ]),
        "create fixture robot commands",
      );

      await t.test(
        "rejects missing and incorrect ingestion secrets without mutation",
        async () => {
          const before = await readRobot();
          const message = actionBody("presence", presencePayload());

          const missingSecret = await expectStatus(message, 401, null);
          assert.equal(missingSecret.error, "Unauthorized");

          const wrongSecret = await expectStatus(
            message,
            401,
            "not-the-integration-secret",
          );
          assert.equal(wrongSecret.error, "Unauthorized");

          const after = await readRobot();
          assert.equal(after.last_seen, before.last_seen);
        },
      );

      await t.test(
        "enforces the exact EMQX action envelope and QoS 1",
        async () => {
          const message = actionBody("presence", presencePayload());
          const extraField = await expectStatus(
            { ...message, unexpected: true },
            400,
          );
          assert.match(extraField.error, /unsupported fields/i);

          const missingMessageId = { ...message };
          delete missingMessageId.mqttMessageId;
          const missingField = await expectStatus(missingMessageId, 400);
          assert.match(missingField.error, /missing/i);

          const wrongQos = await expectStatus(
            actionBody("presence", presencePayload(), { qos: 0 }),
            400,
          );
          assert.match(wrongQos.error, /QoS 1/i);
        },
      );

      await t.test(
        "rejects MQTT identity mismatches, unknown robots, and command ownership mismatches",
        async () => {
          const before = await readRobot();

          const usernameMismatch = await expectStatus(
            actionBody("presence", presencePayload(), {
              username: OTHER_ROBOT_ID,
            }),
            403,
          );
          assert.match(usernameMismatch.error, /username/i);

          const clientMismatch = await expectStatus(
            actionBody("presence", presencePayload(), {
              clientid: `${OTHER_ROBOT_ID}-pi`,
            }),
            403,
          );
          assert.match(clientMismatch.error, /client ID/i);

          const payloadMismatch = await expectStatus(
            actionBody(
              "presence",
              presencePayload({ robotId: OTHER_ROBOT_ID }),
            ),
            403,
          );
          assert.match(payloadMismatch.error, /Payload robotId/i);

          const unknownRobotId = "robot-99";
          const unknownRobot = await expectStatus(
            actionBody(
              "presence",
              presencePayload({ robotId: unknownRobotId }),
              {
                topicRobotId: unknownRobotId,
                username: unknownRobotId,
                clientid: `${unknownRobotId}-pi`,
              },
            ),
            404,
          );
          assert.equal(unknownRobot.error, "Unknown robot");

          const wrongOwnerAck = await expectStatus(
            actionBody("acks", {
              schemaVersion: 1,
              commandId: otherRobotCommandId,
              robotId: TEST_ROBOT_ID,
              status: "ACKNOWLEDGED",
              reason: "",
              at: new Date().toISOString(),
            }),
            403,
          );
          assert.match(wrongOwnerAck.error, /another robot/i);

          const otherCommand = requireData(
            await admin
              .from("robot_commands")
              .select("status, acknowledged_at")
              .eq("id", otherRobotCommandId)
              .single(),
            "read other robot command",
          );
          assert.equal(otherCommand.status, "PUBLISHED");
          assert.equal(otherCommand.acknowledged_at, null);

          const after = await readRobot();
          assert.equal(after.last_seen, before.last_seen);
        },
      );

      await t.test(
        "rejects unsupported payload schemas before changing robot state",
        async () => {
          const before = await readRobot();
          const response = await expectStatus(
            actionBody(
              "presence",
              presencePayload({ schemaVersion: 2 }),
            ),
            400,
          );
          assert.equal(response.error, "Unsupported schemaVersion");
          const after = await readRobot();
          assert.equal(after.last_seen, before.last_seen);
        },
      );

      await t.test(
        "persists a valid command acknowledgement without faking telemetry",
        async () => {
          const robotBefore = await readRobot();
          const acknowledgedAt = new Date().toISOString();
          const acknowledgementPayload = {
            schemaVersion: 1,
            commandId,
            robotId: TEST_ROBOT_ID,
            status: "ACKNOWLEDGED",
            reason: "accepted by integration rover",
            at: acknowledgedAt,
          };
          const response = await expectStatus(
            actionBody("acks", acknowledgementPayload),
            200,
          );
          assert.deepEqual(
            {
              accepted: response.accepted,
              robotId: response.robotId,
              messageType: response.messageType,
              duplicate: response.duplicate,
              stale: response.stale,
            },
            {
              accepted: true,
              robotId: TEST_ROBOT_ID,
              messageType: "acks",
              duplicate: false,
              stale: false,
            },
          );

          const command = requireData(
            await admin
              .from("robot_commands")
              .select("status, acknowledged_at, result")
              .eq("id", commandId)
              .single(),
            "read acknowledged command",
          );
          assert.equal(command.status, "ACKNOWLEDGED");
          assert.equal(
            new Date(command.acknowledged_at).getTime(),
            new Date(acknowledgedAt).getTime(),
          );
          assert.equal(command.result.reason, "accepted by integration rover");
          assert.equal(
            new Date(command.result.at).getTime(),
            new Date(acknowledgedAt).getTime(),
          );

          const duplicate = await expectStatus(
            actionBody("acks", acknowledgementPayload),
            200,
          );
          assert.equal(duplicate.duplicate, true);

          const robot = await readRobot();
          assert.equal(robot.last_seen, robotBefore.last_seen);
          assert.equal(robot.telemetry_at, robotBefore.telemetry_at);
        },
      );

      await t.test(
        "persists a command completion acknowledgement",
        async () => {
          const completedAt = new Date().toISOString();
          const response = await expectStatus(
            actionBody("acks", {
              schemaVersion: 1,
              commandId: completionCommandId,
              robotId: TEST_ROBOT_ID,
              status: "COMPLETED",
              reason: "pause command completed",
              at: completedAt,
            }),
            200,
          );
          assert.equal(response.accepted, true);
          assert.equal(response.messageType, "acks");

          const command = requireData(
            await admin
              .from("robot_commands")
              .select("status, acknowledged_at, result")
              .eq("id", completionCommandId)
              .single(),
            "read completed command",
          );
          assert.equal(command.status, "COMPLETED");
          assert.equal(
            new Date(command.acknowledged_at).getTime(),
            new Date(completedAt).getTime(),
          );
          assert.equal(command.result.reason, "pause command completed");
        },
      );

      let acceptedTelemetryAt;
      await t.test(
        "persists valid telemetry and rejects an older state sample as stale",
        async () => {
          const previousRobot = await readRobot();
          const previousTelemetryAt = previousRobot.telemetry_at
            ? new Date(previousRobot.telemetry_at).getTime()
            : 0;
          acceptedTelemetryAt = new Date(
            Math.max(Date.now(), previousTelemetryAt + 1),
          ).toISOString();
          const state = {
            schemaVersion: 1,
            robotId: TEST_ROBOT_ID,
            status: "ONLINE",
            mode: "IDLE",
            battery: 73,
            signal: 84,
            speedMps: 0.42,
            locationId: "loc-fcs",
            currentDeliveryId: null,
            lidar: "OK",
            camera: "OK",
            esp32: "OK",
            motorTempC: 37.5,
            firmwareVersion: "pi-agent-integration",
            at: acceptedTelemetryAt,
          };

          const accepted = await expectStatus(
            actionBody("state", state),
            200,
          );
          assert.equal(accepted.accepted, true);
          assert.equal(accepted.messageType, "state");
          assert.equal(accepted.stale, false);

          const current = await readRobot();
          assert.equal(current.status, "ONLINE");
          assert.equal(current.mode, "IDLE");
          assert.equal(current.battery, 73);
          assert.equal(current.signal, 84);
          assert.equal(Number(current.speed_mps), 0.42);
          assert.equal(current.location_id, "loc-fcs");
          assert.equal(current.lidar, "OK");
          assert.equal(current.camera, "OK");
          assert.equal(current.esp32, "OK");
          assert.equal(Number(current.motor_temp_c), 37.5);
          assert.equal(current.firmware_version, "pi-agent-integration");
          assert.equal(
            new Date(current.telemetry_at).getTime(),
            new Date(acceptedTelemetryAt).getTime(),
          );
          const acceptedReceiptAt = current.telemetry_received_at;
          assert.ok(acceptedReceiptAt);
          assert.ok(
            Math.abs(Date.now() - new Date(acceptedReceiptAt).getTime()) <
              15_000,
          );

          const staleTelemetryAt = new Date(
            new Date(acceptedTelemetryAt).getTime() - 10_000,
          ).toISOString();
          const stale = await expectStatus(
            actionBody("state", {
              ...state,
              status: "FAULT",
              mode: "FAULT",
              battery: 5,
              signal: 2,
              speedMps: 0,
              at: staleTelemetryAt,
            }),
            200,
          );
          assert.equal(stale.accepted, true);
          assert.equal(stale.stale, true);

          const afterStale = await readRobot();
          assert.equal(afterStale.status, "ONLINE");
          assert.equal(afterStale.mode, "IDLE");
          assert.equal(afterStale.battery, 73);
          assert.equal(afterStale.signal, 84);
          assert.equal(
            new Date(afterStale.telemetry_at).getTime(),
            new Date(acceptedTelemetryAt).getTime(),
          );
          assert.equal(
            afterStale.telemetry_received_at,
            acceptedReceiptAt,
          );

          const tooOld = await expectStatus(
            actionBody("state", {
              ...state,
              at: new Date(Date.now() - 61_000).toISOString(),
            }),
            400,
          );
          assert.match(tooOld.error, /too old/i);

          const afterTooOld = await readRobot();
          assert.equal(
            new Date(afterTooOld.telemetry_at).getTime(),
            new Date(acceptedTelemetryAt).getTime(),
          );
        },
      );

      const lowBatteryEventId = randomUUID();
      eventMessageIds.add(lowBatteryEventId);
      await t.test(
        "inserts a valid event and treats a repeated eventId as an idempotent duplicate",
        async () => {
          const eventAt = new Date().toISOString();
          const message = actionBody("events", {
            schemaVersion: 1,
            eventId: lowBatteryEventId,
            robotId: TEST_ROBOT_ID,
            deliveryId,
            commandId,
            type: "LOW_BATTERY",
            severity: "WARNING",
            at: eventAt,
            payload: { battery: 17, source: "integration-test" },
          });

          const accepted = await expectStatus(message, 200);
          assert.equal(accepted.accepted, true);
          assert.equal(accepted.messageType, "events");
          assert.equal(accepted.duplicate, false);

          const event = requireData(
            await admin
              .from("robot_events")
              .select("*")
              .eq("message_id", lowBatteryEventId)
              .single(),
            "read inserted robot event",
          );
          assert.equal(event.robot_id, TEST_ROBOT_ID);
          assert.equal(event.delivery_id, deliveryId);
          assert.equal(event.command_id, commandId);
          assert.equal(event.event_type, "LOW_BATTERY");
          assert.equal(event.severity, "WARNING");
          assert.deepEqual(event.payload, {
            battery: 17,
            source: "integration-test",
          });
          assert.equal(
            new Date(event.occurred_at).getTime(),
            new Date(eventAt).getTime(),
          );

          const duplicate = await expectStatus(message, 200);
          assert.equal(duplicate.accepted, true);
          assert.equal(duplicate.duplicate, true);

          const countResult = await admin
            .from("robot_events")
            .select("id", { count: "exact", head: true })
            .eq("message_id", lowBatteryEventId);
          requireData(countResult, "count duplicate robot events");
          assert.equal(countResult.count, 1);
        },
      );

      const invalidMissionEventId = randomUUID();
      eventMessageIds.add(invalidMissionEventId);
      await t.test(
        "rolls back MISSION_STARTED when its mission command is not active",
        async () => {
          requireData(
            await admin
              .from("robot_commands")
              .update({ status: "FAILED" })
              .eq("id", commandId),
            "fail mission command for rejection fixture",
          );
          const rejected = await expectStatus(
            actionBody("events", {
              schemaVersion: 1,
              eventId: invalidMissionEventId,
              robotId: TEST_ROBOT_ID,
              deliveryId,
              commandId,
              type: "MISSION_STARTED",
              severity: "INFO",
              at: new Date().toISOString(),
              payload: {},
            }),
            409,
          );
          assert.match(rejected.error, /valid START_MISSION command/i);

          const eventResult = await admin
            .from("robot_events")
            .select("id")
            .eq("message_id", invalidMissionEventId)
            .maybeSingle();
          requireData(eventResult, "check rejected mission event");
          assert.equal(eventResult.data, null);

          const delivery = requireData(
            await admin
              .from("deliveries")
              .select("status")
              .eq("id", deliveryId)
              .single(),
            "read delivery after rejected mission event",
          );
          assert.equal(delivery.status, "ASSIGNED");

          requireData(
            await admin
              .from("robot_commands")
              .update({ status: "ACKNOWLEDGED" })
              .eq("id", commandId),
            "restore active mission command fixture",
          );
        },
      );

      const missionStartedEventId = randomUUID();
      eventMessageIds.add(missionStartedEventId);
      await t.test(
        "lets valid robot evidence win the ASSIGNED-to-publish response race",
        async () => {
          const accepted = await expectStatus(
            actionBody("events", {
              schemaVersion: 1,
              eventId: missionStartedEventId,
              robotId: TEST_ROBOT_ID,
              deliveryId,
              commandId,
              type: "MISSION_STARTED",
              severity: "INFO",
              at: new Date().toISOString(),
              payload: {},
            }),
            200,
          );
          assert.equal(accepted.accepted, true);
          assert.equal(accepted.duplicate, false);

          const delivery = requireData(
            await admin
              .from("deliveries")
              .select("status, progress, robot_id")
              .eq("id", deliveryId)
              .single(),
            "read advanced delivery",
          );
          assert.equal(delivery.status, "TO_SOURCE");
          assert.equal(delivery.progress, 28);
          assert.equal(delivery.robot_id, TEST_ROBOT_ID);

          const robot = await readRobot();
          assert.equal(robot.status, "BUSY");
          assert.equal(robot.mode, "AUTO");
          assert.equal(robot.current_delivery_id, deliveryId);
        },
      );

      await t.test(
        "uses presence for connectivity without clearing operational OFFLINE state",
        async () => {
          const presenceBrokerTime = Date.now() + 1_000;
          const offline = await expectStatus(
            actionBody(
              "presence",
              presencePayload({
                online: false,
                firmwareVersion: "presence-offline-test",
              }),
              { timestamp: presenceBrokerTime },
            ),
            200,
          );
          assert.equal(offline.accepted, true);
          assert.equal(offline.messageType, "presence");
          assert.equal(offline.stale, false);

          const offlineRobot = await readRobot();
          assert.equal(offlineRobot.status, "OFFLINE");
          assert.equal(offlineRobot.bridge_online, false);
          assert.equal(Number(offlineRobot.speed_mps), 0);
          assert.equal(offlineRobot.signal, 0);
          assert.equal(offlineRobot.lidar, "OFFLINE");
          assert.equal(offlineRobot.camera, "OFFLINE");
          assert.equal(offlineRobot.esp32, "OFFLINE");
          assert.equal(
            offlineRobot.firmware_version,
            "presence-offline-test",
          );
          assert.ok(
            new Date(offlineRobot.bridge_last_seen).getTime() >=
              Date.now() - 15_000,
          );
          const operationalLastSeen = offlineRobot.last_seen;

          const delayedState = await expectStatus(
            actionBody(
              "state",
              {
                schemaVersion: 1,
                robotId: TEST_ROBOT_ID,
                status: "BUSY",
                mode: "AUTO",
                battery: 72,
                signal: 80,
                speedMps: 0.2,
                locationId: "loc-fcs",
                currentDeliveryId: deliveryId,
                lidar: "OK",
                camera: "OK",
                esp32: "OK",
                motorTempC: 38,
                firmwareVersion: "delayed-state-test",
                at: new Date().toISOString(),
              },
              { timestamp: presenceBrokerTime - 1 },
            ),
            200,
          );
          assert.equal(delayedState.stale, true);
          const afterDelayedState = await readRobot();
          assert.equal(afterDelayedState.bridge_online, false);
          assert.equal(afterDelayedState.status, "OFFLINE");

          const online = await expectStatus(
            actionBody(
              "presence",
              presencePayload({
                online: true,
                firmwareVersion: "presence-online-test",
              }),
              { timestamp: presenceBrokerTime + 1_000 },
            ),
            200,
          );
          assert.equal(online.accepted, true);
          assert.equal(online.messageType, "presence");
          assert.equal(online.stale, false);

          const onlineRobot = await readRobot();
          assert.equal(onlineRobot.status, "OFFLINE");
          assert.equal(onlineRobot.bridge_online, true);
          assert.equal(onlineRobot.mode, "AUTO");
          assert.equal(onlineRobot.current_delivery_id, deliveryId);
          assert.equal(
            onlineRobot.firmware_version,
            "presence-online-test",
          );
          assert.ok(
            new Date(onlineRobot.bridge_last_seen).getTime() >=
              new Date(offlineRobot.bridge_last_seen).getTime(),
          );
          assert.equal(onlineRobot.last_seen, operationalLastSeen);

          const stalePresence = await expectStatus(
            actionBody(
              "presence",
              presencePayload({ online: true }),
              { timestamp: Date.now() - 61_000 },
            ),
            400,
          );
          assert.match(stalePresence.error, /presence is too old/i);
        },
      );

      await t.test(
        "requires a single-use staff RESUME issued after the current ESTOP",
        async () => {
          const pauseEventId = randomUUID();
          const estopEventId = randomUUID();
          const delayedResumeEventId = randomUUID();
          const missingSafetyEvidenceEventId = randomUUID();
          const resumedEventId = randomUUID();
          const blockedProgressEventId = randomUUID();
          eventMessageIds.add(pauseEventId);
          eventMessageIds.add(estopEventId);
          eventMessageIds.add(delayedResumeEventId);
          eventMessageIds.add(missingSafetyEvidenceEventId);
          eventMessageIds.add(resumedEventId);
          eventMessageIds.add(blockedProgressEventId);
          const prePauseRobot = await readRobot();
          const previousControlTime = prePauseRobot.control_event_at
            ? new Date(prePauseRobot.control_event_at).getTime()
            : 0;
          const pauseTime = Math.max(Date.now(), previousControlTime + 100);
          await expectStatus(
            actionBody("events", {
              schemaVersion: 1,
              eventId: pauseEventId,
              robotId: TEST_ROBOT_ID,
              commandId: completionCommandId,
              type: "PAUSED",
              severity: "INFO",
              at: new Date(pauseTime).toISOString(),
              payload: { source: "pre-estop-control-race" },
            }),
            200,
          );

          const pausedRobot = await readRobot();
          assert.equal(pausedRobot.mode, "PAUSED");
          const pauseReceiptTime = new Date(
            pausedRobot.control_event_received_at,
          ).getTime();
          const oldResumeIssuedTime = Math.max(
            Date.now() + 100,
            pauseReceiptTime + 1,
          );
          const oldResumeIssuedAt = new Date(oldResumeIssuedTime).toISOString();
          const oldResumeExpiresAt = new Date(
            oldResumeIssuedTime + 5 * 60_000,
          ).toISOString();
          requireData(
            await admin.from("robot_commands").insert({
              id: oldResumeCommandId,
              robot_id: TEST_ROBOT_ID,
              command_type: "RESUME",
              payload: {
                schemaVersion: 1,
                commandId: oldResumeCommandId,
                robotId: TEST_ROBOT_ID,
                command: "RESUME",
                payload: {},
                issuedAt: oldResumeIssuedAt,
                expiresAt: oldResumeExpiresAt,
              },
              status: "PUBLISHED",
              issued_by: authUserId,
              issued_at: oldResumeIssuedAt,
              published_at: oldResumeIssuedAt,
              expires_at: oldResumeExpiresAt,
            }),
            "create pre-ESTOP RESUME command",
          );

          const estopAt = new Date(oldResumeIssuedTime + 100).toISOString();

          await expectStatus(
            actionBody("events", {
              schemaVersion: 1,
              eventId: estopEventId,
              robotId: TEST_ROBOT_ID,
              type: "ESTOP_TRIGGERED",
              severity: "CRITICAL",
              at: estopAt,
              payload: { source: "event-order-regression" },
            }),
            200,
          );

          const estoppedRobot = await readRobot();
          assert.equal(estoppedRobot.status, "FAULT");
          assert.equal(estoppedRobot.mode, "ESTOP");
          const latchTime = new Date(estoppedRobot.safety_latched_at).getTime();
          const safeStateTime = Math.max(Date.now() + 100, latchTime + 100);
          const safeBrokerTime = Math.max(
            safeStateTime,
            new Date(estoppedRobot.bridge_last_seen ?? 0).getTime() + 1,
          );
          await expectStatus(
            actionBody(
              "state",
              {
                schemaVersion: 1,
                robotId: TEST_ROBOT_ID,
                status: "FAULT",
                mode: "ESTOP",
                battery: 73,
                signal: 88,
                speedMps: 0,
                locationId: "loc-fcs",
                currentDeliveryId: deliveryId,
                lidar: "OK",
                camera: "OK",
                esp32: "OK",
                motorTempC: 38,
                firmwareVersion: "post-estop-safety-test",
                at: new Date(safeStateTime).toISOString(),
              },
              { timestamp: safeBrokerTime },
            ),
            200,
          );

          const rejected = await expectStatus(
            actionBody("events", {
              schemaVersion: 1,
              eventId: delayedResumeEventId,
              robotId: TEST_ROBOT_ID,
              commandId: oldResumeCommandId,
              type: "RESUMED",
              severity: "INFO",
              at: new Date(safeStateTime + 100).toISOString(),
              payload: {
                source: "old-resume-regression",
                localSafetyChecksPassed: true,
              },
            }),
            409,
          );
          assert.match(rejected.error, /active staff-issued RESUME command/i);

          const blockedProgress = await expectStatus(
            actionBody("events", {
              schemaVersion: 1,
              eventId: blockedProgressEventId,
              robotId: TEST_ROBOT_ID,
              deliveryId,
              commandId,
              type: "ARRIVED_SOURCE",
              severity: "INFO",
              at: new Date(safeStateTime + 200).toISOString(),
              payload: { source: "safety-latch-regression" },
            }),
            409,
          );
          assert.match(blockedProgress.error, /safety latch blocks mission progress/i);

          const resetIssuedTime = Math.max(Date.now() + 300, latchTime + 300);
          const resetIssuedAt = new Date(resetIssuedTime).toISOString();
          const resetExpiresAt = new Date(resetIssuedTime + 5 * 60_000)
            .toISOString();
          requireData(
            await admin.from("robot_commands").insert({
              id: resumeCommandId,
              robot_id: TEST_ROBOT_ID,
              command_type: "RESUME",
              payload: {
                schemaVersion: 1,
                commandId: resumeCommandId,
                robotId: TEST_ROBOT_ID,
                command: "RESUME",
                payload: {},
                issuedAt: resetIssuedAt,
                expiresAt: resetExpiresAt,
              },
              status: "PENDING",
              issued_by: authUserId,
              issued_at: resetIssuedAt,
              expires_at: resetExpiresAt,
            }),
            "create post-ESTOP RESUME command",
          );

          const missingSafetyEvidence = await expectStatus(
            actionBody("events", {
              schemaVersion: 1,
              eventId: missingSafetyEvidenceEventId,
              robotId: TEST_ROBOT_ID,
              commandId: resumeCommandId,
              type: "RESUMED",
              severity: "INFO",
              at: new Date(Math.max(resetIssuedTime, safeStateTime) + 100)
                .toISOString(),
              payload: {},
            }),
            409,
          );
          assert.match(missingSafetyEvidence.error, /local safety checks/i);

          await expectStatus(
            actionBody("events", {
              schemaVersion: 1,
              eventId: resumedEventId,
              robotId: TEST_ROBOT_ID,
              commandId: resumeCommandId,
              type: "RESUMED",
              severity: "INFO",
              at: new Date(Math.max(resetIssuedTime, safeStateTime) + 200)
                .toISOString(),
              payload: { localSafetyChecksPassed: true },
            }),
            200,
          );

          const resumedRobot = await readRobot();
          assert.equal(resumedRobot.status, "BUSY");
          assert.equal(resumedRobot.mode, "AUTO");

          const consumedCommand = requireData(
            await admin
              .from("robot_commands")
              .select("status")
              .eq("id", resumeCommandId)
              .single(),
            "read consumed RESUME command",
          );
          assert.equal(consumedCommand.status, "COMPLETED");
        },
      );

      await t.test(
        "serializes concurrent mission starts and same-ID retries",
        async () => {
          requireData(
            await admin
              .from("robot_commands")
              .update({ status: "COMPLETED" })
              .eq("id", commandId),
            "retire the first mission command fixture",
          );
          requireData(
            await admin
              .from("deliveries")
              .update({
                status: "COMPLETED",
                progress: 100,
                completed_at: new Date().toISOString(),
              })
              .eq("id", deliveryId),
            "retire the first delivery fixture",
          );
          requireData(
            await admin
              .from("robots")
              .update({
                status: "ONLINE",
                mode: "IDLE",
                current_delivery_id: null,
                speed_mps: 0,
                telemetry_at: new Date().toISOString(),
                telemetry_received_at: new Date().toISOString(),
                bridge_last_seen: new Date().toISOString(),
                bridge_online: true,
                lidar: "OK",
                camera: "OK",
                esp32: "OK",
              })
              .eq("id", TEST_ROBOT_ID),
            "return the robot to an idle test baseline",
          );

          concurrentDeliveryId = randomUUID();
          const trackingCode =
            `RACE-${randomUUID().replaceAll("-", "").slice(0, 12).toUpperCase()}`;

          requireData(
            await signedInClient
              .from("deliveries")
              .insert({
                id: concurrentDeliveryId,
                tracking_code: trackingCode,
                requester_id: authUserId,
                requester_name: "Untrusted request label",
                requester_email: "untrusted@example.test",
                recipient_name: "Concurrency Recipient",
                recipient_phone: "+959000000001",
                source_id: "loc-fcs",
                destination_id: "loc-library",
                item_name: "Concurrent mission test package",
                category: "DOCUMENT",
                weight_kg: 1,
                priority: "NORMAL",
                status: "REQUESTED",
              }),
            "create concurrent delivery fixture",
          );

          requireData(
            await admin
              .from("deliveries")
              .update({
                status: "ASSIGNED",
                robot_id: TEST_ROBOT_ID,
              })
              .eq("id", concurrentDeliveryId),
            "assign concurrent delivery fixture",
          );

          const concurrentCommandId = randomUUID();
          commandIds.add(concurrentCommandId);
          requireData(
            await admin.from("robot_commands").insert({
              id: concurrentCommandId,
              robot_id: TEST_ROBOT_ID,
              delivery_id: concurrentDeliveryId,
              command_type: "START_MISSION",
              payload: {},
              status: "PUBLISHED",
              issued_by: authUserId,
              expires_at: new Date(Date.now() + 5 * 60_000).toISOString(),
              published_at: new Date().toISOString(),
            }),
            "create concurrent mission command fixture",
          );

          requireData(
            await admin
              .from("deliveries")
              .update({
                status: "DISPATCHED",
                dispatched_at: new Date().toISOString(),
              })
              .eq("id", concurrentDeliveryId),
            "dispatch concurrent delivery fixture",
          );

          const firstEventId = randomUUID();
          const secondEventId = randomUUID();
          eventMessageIds.add(firstEventId);
          eventMessageIds.add(secondEventId);
          const eventAt = new Date(Date.now() + 2_000).toISOString();
          const missionMessage = (eventId, occurredAt = eventAt) =>
            actionBody("events", {
              schemaVersion: 1,
              eventId,
              robotId: TEST_ROBOT_ID,
              deliveryId: concurrentDeliveryId,
              commandId: concurrentCommandId,
              type: "MISSION_STARTED",
              severity: "INFO",
              at: occurredAt,
              payload: { source: "concurrency-regression" },
            });

          const results = await Promise.all([
            postAction(missionMessage(firstEventId)),
            postAction(missionMessage(secondEventId)),
          ]);
          assert.deepEqual(
            results.map(({ response }) => response.status).sort(),
            [200, 409],
          );

          const accepted = results.find(
            ({ response }) => response.status === 200,
          );
          const rejected = results.find(
            ({ response }) => response.status === 409,
          );
          assert.equal(accepted?.body.accepted, true);
          assert.match(
            rejected?.body.error ?? "",
            /current (delivery|control) state/i,
          );

          const eventCount = await admin
            .from("robot_events")
            .select("id", { count: "exact", head: true })
            .in("message_id", [firstEventId, secondEventId]);
          requireData(eventCount, "count concurrent mission events");
          assert.equal(eventCount.count, 1);

          const delivery = requireData(
            await admin
              .from("deliveries")
              .select("status")
              .eq("id", concurrentDeliveryId)
              .single(),
            "read concurrent delivery state",
          );
          assert.equal(delivery.status, "TO_SOURCE");

          requireData(
            await admin
              .from("deliveries")
              .update({ status: "DISPATCHED" })
              .eq("id", concurrentDeliveryId),
            "reset concurrent delivery for retry test",
          );

          const retryEventId = randomUUID();
          eventMessageIds.add(retryEventId);
          const retryMessage = missionMessage(
            retryEventId,
            new Date(new Date(eventAt).getTime() + 1_000).toISOString(),
          );
          const retryResults = await Promise.all([
            postAction(retryMessage),
            postAction(retryMessage),
          ]);
          assert.deepEqual(
            retryResults.map(({ response }) => response.status),
            [200, 200],
          );
          assert.deepEqual(
            retryResults.map(({ body }) => body.duplicate).sort(),
            [false, true],
          );

          const retryEventCount = await admin
            .from("robot_events")
            .select("id", { count: "exact", head: true })
            .eq("message_id", retryEventId);
          requireData(retryEventCount, "count concurrent retry events");
          assert.equal(retryEventCount.count, 1);
        },
      );
    } finally {
      await cleanup();
    }
  },
);
