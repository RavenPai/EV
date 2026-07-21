import { createClient } from "https://esm.sh/@supabase/supabase-js@2";
import { classifyEmqxPublishStatus } from "./publish-response.js";

const corsHeaders = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Headers": "authorization, x-client-info, apikey, content-type",
};

const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), {
  status,
  headers: { ...corsHeaders, "Content-Type": "application/json" },
});

Deno.serve(async (request) => {
  if (request.method === "OPTIONS") return new Response("ok", { headers: corsHeaders });
  if (request.method !== "POST") return json({ error: "Method not allowed" }, 405);

  try {
    const supabaseUrl = Deno.env.get("SUPABASE_URL")!;
    const serviceKey = Deno.env.get("SUPABASE_SERVICE_ROLE_KEY")!;
    const authorization = request.headers.get("Authorization");
    if (!authorization) return json({ error: "Missing authorization" }, 401);

    const userClient = createClient(supabaseUrl, Deno.env.get("SUPABASE_ANON_KEY")!, {
      global: { headers: { Authorization: authorization } },
    });
    const { data: authData, error: authError } = await userClient.auth.getUser();
    if (authError || !authData.user) return json({ error: "Invalid session" }, 401);

    const admin = createClient(supabaseUrl, serviceKey);
    const { data: profile } = await admin.from("profiles").select("role").eq("id", authData.user.id).single();
    if (!profile || !["ADMIN", "OPERATOR"].includes(profile.role)) return json({ error: "Staff role required" }, 403);

    const body = await request.json();
    if (!body || typeof body !== "object" || Array.isArray(body)) {
      return json({ error: "Request body must be a JSON object" }, 400);
    }

    if ("reconcileCommandId" in body) {
      const reconcileCommandId = String(body.reconcileCommandId ?? "");
      if (!/^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i.test(reconcileCommandId)) {
        return json({ error: "reconcileCommandId must be a UUID" }, 400);
      }
      if (body.resolution !== "CONFIRMED_NOT_PUBLISHED") {
        return json({
          error: "Only a broker-verified CONFIRMED_NOT_PUBLISHED outcome can release an unknown command",
        }, 400);
      }
      const { data: resolved, error: resolveError } = await admin.rpc(
        "resolve_unknown_robot_command",
        {
          p_command_id: reconcileCommandId,
          p_actor_id: authData.user.id,
          p_resolution: body.resolution,
        },
      );
      if (resolveError) {
        if (resolveError.code === "P0001") {
          return json({ error: resolveError.message }, 409);
        }
        throw resolveError;
      }
      return json({
        commandId: reconcileCommandId,
        status: "FAILED",
        reconciled: resolved === true,
      });
    }

    let robotId: string;
    let deliveryId: string | null = null;
    let commandType: string;
    let payload: Record<string, unknown>;

    if (body.deliveryId) {
      const { data: delivery, error } = await admin.from("deliveries").select("*").eq("id", body.deliveryId).single();
      if (error || !delivery) return json({ error: "Delivery not found" }, 404);
      if (delivery.status !== "ASSIGNED" || !delivery.robot_id) return json({ error: "Delivery must be assigned before dispatch" }, 409);

      const { data: activeCommand, error: activeCommandError } = await admin
        .from("robot_commands")
        .select("id")
        .eq("delivery_id", delivery.id)
        .eq("command_type", "START_MISSION")
        .in("status", ["PENDING", "PUBLISH_UNKNOWN", "PUBLISHED", "ACKNOWLEDGED"])
        .gt("expires_at", new Date().toISOString())
        .limit(1)
        .maybeSingle();
      if (activeCommandError) throw activeCommandError;
      if (activeCommand) return json({ error: "An active mission command already exists for this delivery" }, 409);

      robotId = delivery.robot_id;
      deliveryId = delivery.id;
      commandType = "START_MISSION";

      const { data: robot, error: robotError } = await admin
        .from("robots")
        .select("id, status, mode, current_delivery_id, battery, speed_mps, lidar, camera, esp32, bridge_online, bridge_last_seen, telemetry_received_at")
        .eq("id", robotId)
        .maybeSingle();
      if (robotError) throw robotError;
      if (!robot) return json({ error: "Assigned robot not found" }, 409);
      const freshnessCutoff = Date.now() - 60_000;
      const bridgeFresh = robot.bridge_online && robot.bridge_last_seen &&
        new Date(robot.bridge_last_seen).getTime() >= freshnessCutoff;
      const telemetryFresh = robot.telemetry_received_at &&
        new Date(robot.telemetry_received_at).getTime() >= freshnessCutoff;
      const sensorsReady = [robot.lidar, robot.camera, robot.esp32]
        .every((value) => value === "OK");
      if (
        robot.status !== "ONLINE" ||
        robot.mode !== "IDLE" ||
        robot.current_delivery_id !== null ||
        Number(robot.speed_mps) !== 0 ||
        robot.battery < 20 ||
        !bridgeFresh ||
        !telemetryFresh ||
        !sensorsReady
      ) {
        return json({ error: "Assigned robot is not ready with fresh telemetry" }, 409);
      }

      const { data: robotCommand, error: robotCommandError } = await admin
        .from("robot_commands")
        .select("id")
        .eq("robot_id", robotId)
        .eq("command_type", "START_MISSION")
        .in("status", ["PENDING", "PUBLISH_UNKNOWN", "PUBLISHED", "ACKNOWLEDGED"])
        .limit(1)
        .maybeSingle();
      if (robotCommandError) throw robotCommandError;
      if (robotCommand) {
        return json({ error: "The assigned robot already has an active mission command" }, 409);
      }

      const { data: commandBarriers, error: commandBarrierError } = await admin
        .from("robot_commands")
        .select("command_type, status, result")
        .eq("robot_id", robotId)
        .neq("command_type", "START_MISSION")
        .in("status", [
          "PENDING", "PUBLISH_UNKNOWN", "PUBLISHED", "ACKNOWLEDGED",
          "COMPLETED",
        ]);
      if (commandBarrierError) throw commandBarrierError;
      const hasCommandBarrier = (commandBarriers ?? []).some((candidate) =>
        candidate.status === "PUBLISH_UNKNOWN" ||
        (["PAUSE", "RESUME", "RETURN_HOME", "ESTOP"].includes(
          candidate.command_type,
        ) && (candidate.status !== "COMPLETED" ||
          candidate.result?.consumed !== true))
      );
      if (hasCommandBarrier) {
        return json({
          error: "Resolve the robot's active control command before dispatch",
        }, 409);
      }

      payload = {
        sourceLocationId: delivery.source_id,
        destinationLocationId: delivery.destination_id,
        mapVersion: "miit-campus-v1",
        deliveryId: delivery.id,
      };
    } else if (body.robotId && body.command) {
      robotId = String(body.robotId);
      commandType = String(body.command);
      if (!["PAUSE", "RESUME", "RETURN_HOME", "ESTOP"].includes(commandType)) return json({ error: "Unsupported command" }, 400);
      const { data: robot, error: robotError } = await admin
        .from("robots")
        .select("id, mode, status, current_delivery_id")
        .eq("id", robotId)
        .maybeSingle();
      if (robotError) throw robotError;
      if (!robot) return json({ error: "Robot not found" }, 404);
      const safetyLatched = robot.mode === "ESTOP" || robot.mode === "FAULT";
      if (commandType === "RESUME" && !safetyLatched && robot.mode !== "PAUSED") {
        return json({ error: "Robot is not paused or safety-latched" }, 409);
      }
      if (commandType === "PAUSE" && robot.mode === "PAUSED") {
        return json({ error: "Robot is already paused" }, 409);
      }
      if (commandType === "RETURN_HOME" && robot.mode === "PAUSED") {
        return json({ error: "Resume the robot before returning home" }, 409);
      }
      if (["PAUSE", "RETURN_HOME"].includes(commandType) && safetyLatched) {
        return json({ error: "Reset the robot safety latch before this command" }, 409);
      }
      if (commandType !== "ESTOP") {
        const { data: uncertainCommand, error: uncertainCommandError } = await admin
          .from("robot_commands")
          .select("id")
          .eq("robot_id", robotId)
          .eq("status", "PUBLISH_UNKNOWN")
          .limit(1)
          .maybeSingle();
        if (uncertainCommandError) throw uncertainCommandError;
        if (uncertainCommand) {
          return json({
            error: "Resolve the robot's unknown publish outcome before sending another command",
          }, 409);
        }

        const { data: activeControlCommands, error: activeControlCommandError } =
          await admin
            .from("robot_commands")
            .select("id, command_type, status, result")
            .eq("robot_id", robotId)
            .in("command_type", ["PAUSE", "RESUME", "RETURN_HOME"])
            .in("status", [
              "PENDING", "PUBLISH_UNKNOWN", "PUBLISHED", "ACKNOWLEDGED",
              "COMPLETED",
            ])
            .limit(10);
        if (activeControlCommandError) throw activeControlCommandError;
        const activeControl = (activeControlCommands ?? []).some((candidate) =>
          candidate.status !== "COMPLETED" ||
          candidate.result?.consumed !== true
        );
        if (activeControl) {
          return json({
            error: "An active control command already exists for this robot",
          }, 409);
        }
      }
      if (commandType === "RETURN_HOME") {
        if (!robot.current_delivery_id) {
          return json({ error: "Robot has no active delivery to return from" }, 409);
        }
        deliveryId = robot.current_delivery_id ?? null;
      }
      payload = { reason: "Authorized web operation" };
    } else {
      return json({ error: "Provide deliveryId or robotId and command" }, 400);
    }

    const commandId = crypto.randomUUID();
    const issuedAt = new Date();
    const expiresAt = new Date(issuedAt.getTime() + (commandType === "ESTOP" ? 60_000 : 5 * 60_000));
    const envelope = {
      schemaVersion: 1,
      commandId,
      robotId,
      command: commandType,
      payload,
      issuedAt: issuedAt.toISOString(),
      expiresAt: expiresAt.toISOString(),
    };

    const { error: commandError } = await admin.from("robot_commands").insert({
      id: commandId,
      robot_id: robotId,
      delivery_id: deliveryId,
      command_type: commandType,
      payload: envelope,
      // Enter the conservative, non-expiring state before the external call.
      // If this Edge isolate dies after EMQX accepts the message, the command
      // reservation remains blocked until robot evidence or an operator
      // reconciliation proves the outcome.
      status: "PUBLISH_UNKNOWN",
      issued_by: authData.user.id,
      expires_at: expiresAt.toISOString(),
    });
    if (commandError) {
      if (commandError.code === "23505") {
        return json({
          error: commandType === "START_MISSION"
            ? "An active mission command already exists"
            : "An active control command already exists for this robot",
        }, 409);
      }
      if (commandError.code === "P0001") {
        return json({
          error: commandError.message,
        }, 409);
      }
      throw commandError;
    }

    const finalizePublishedCommand = async (publishedAt: string): Promise<string> => {
      const { data: finalizedStatus, error: finalizeError } = await admin.rpc(
        "finalize_robot_command_publish",
        {
          p_command_id: commandId,
          p_robot_id: robotId,
          p_delivery_id: deliveryId,
          p_published_at: publishedAt,
        },
      );
      if (finalizeError) throw finalizeError;
      return String(finalizedStatus);
    };

    const reconcileRobotEvidence = async (): Promise<Response | null> => {
      const { data: observedCommand, error: observedCommandError } = await admin
        .from("robot_commands")
        .select("status")
        .eq("id", commandId)
        .single();
      if (observedCommandError) throw observedCommandError;
      const observedStatus = String(observedCommand.status);

      if (["REJECTED", "FAILED", "EXPIRED"].includes(observedStatus)) {
        return json({
          error: "The command was rejected, failed, or expired",
          commandId,
          status: observedStatus,
        }, 409);
      }
      if (!["ACKNOWLEDGED", "COMPLETED"].includes(observedStatus)) {
        return null;
      }

      const finalizedStatus = await finalizePublishedCommand(
        new Date().toISOString(),
      );

      return json({
        commandId,
        status: finalizedStatus,
        robotId,
        reconciledFromRobotEvidence: true,
      });
    };

    const apiUrl = Deno.env.get("EMQX_API_URL")!;
    const apiKey = Deno.env.get("EMQX_API_KEY")!;
    const apiSecret = Deno.env.get("EMQX_API_SECRET")!;
    let publishResponse: Response;
    const publishController = new AbortController();
    const publishTimeout = setTimeout(() => publishController.abort(), 10_000);
    try {
      publishResponse = await fetch(`${apiUrl.replace(/\/$/, "")}/api/v5/publish`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          Authorization: `Basic ${btoa(`${apiKey}:${apiSecret}`)}`,
        },
        body: JSON.stringify({ topic: `miit/robots/${robotId}/commands`, qos: 1, retain: false, payload: JSON.stringify(envelope) }),
        signal: publishController.signal,
      });
    } catch (publishError) {
      // A timeout is ambiguous: the broker might have accepted the request
      // before the response was lost. Keep a non-expiring reconciliation
      // barrier so a new command ID cannot authorize the same action again.
      const { error: unknownError } = await admin.from("robot_commands").update({
        result: { reason: "EMQX publish outcome is unknown" },
      }).eq("id", commandId).eq("status", "PUBLISH_UNKNOWN");
      if (unknownError) console.error(unknownError);
      console.error(publishError);
      const reconciled = await reconcileRobotEvidence();
      if (reconciled) return reconciled;
      return json({ error: "MQTT publish outcome is unknown; reconcile the command audit before retrying" }, 504);
    } finally {
      clearTimeout(publishTimeout);
    }
    const publishOutcome = classifyEmqxPublishStatus(publishResponse.status);
    if (publishOutcome === "NO_MATCHING_SUBSCRIBERS") {
      // EMQX uses 202 for no_matching_subscribers. Although Response.ok is
      // true, no robot received this command, so dispatch must not advance.
      const reconciled = await reconcileRobotEvidence();
      if (reconciled) return reconciled;
      const { data: failedCommand, error: failedCommandError } = await admin
        .from("robot_commands")
        .update({
          status: "FAILED",
          result: {
            reason: "EMQX reported no matching robot subscriber",
            httpStatus: 202,
          },
        })
        .eq("id", commandId)
        .eq("status", "PUBLISH_UNKNOWN")
        .select("id")
        .maybeSingle();
      if (failedCommandError) throw failedCommandError;
      if (!failedCommand) {
        const racedEvidence = await reconcileRobotEvidence();
        if (racedEvidence) return racedEvidence;
        return json({
          error: "Command state changed while recording the missing subscriber; reconcile before retrying",
          commandId,
        }, 409);
      }
      return json({
        error: "Robot is not subscribed to its command topic",
        commandId,
        status: "FAILED",
      }, 503);
    }
    if (publishOutcome !== "DELIVERED") {
      const reconciled = await reconcileRobotEvidence();
      if (reconciled) return reconciled;
      if (publishOutcome === "DEFINITIVE_REJECTION") {
        // EMQX rejected the request before accepting it for publication. This
        // is the only HTTP failure class that safely releases the reservation.
        const { data: rejectedCommand, error: rejectedCommandError } =
          await admin.from("robot_commands").update({
          status: "FAILED",
          result: { httpStatus: publishResponse.status },
          }).eq("id", commandId)
            .eq("status", "PUBLISH_UNKNOWN")
            .select("id")
            .maybeSingle();
        if (rejectedCommandError) throw rejectedCommandError;
        if (!rejectedCommand) {
          const racedEvidence = await reconcileRobotEvidence();
          if (racedEvidence) return racedEvidence;
          return json({
            error: "Command state changed while recording the broker rejection; reconcile before retrying",
            commandId,
          }, 409);
        }
        return json({ error: "MQTT publish was rejected; command remains in the audit log" }, 502);
      }

      // A broker/proxy 5xx may be returned after EMQX accepted the message.
      // Preserve the command ID as a reconciliation barrier just as for a
      // timeout, preventing a duplicate physical command from being issued.
      const { error: serverErrorRecordError } = await admin.from("robot_commands").update({
        result: {
          reason: "EMQX publish outcome is unknown",
          httpStatus: publishResponse.status,
        },
      }).eq("id", commandId).eq("status", "PUBLISH_UNKNOWN");
      if (serverErrorRecordError) throw serverErrorRecordError;
      const racedEvidence = await reconcileRobotEvidence();
      if (racedEvidence) return racedEvidence;
      return json({ error: "MQTT publish outcome is unknown; reconcile the command audit before retrying" }, 502);
    }

    const publishedAt = new Date().toISOString();
    const finalCommandStatus = await finalizePublishedCommand(publishedAt);

    if (["REJECTED", "FAILED", "EXPIRED"].includes(finalCommandStatus)) {
      return json({
        error: "The robot rejected or failed the published command",
        commandId,
        status: finalCommandStatus,
      }, 409);
    }

    return json({ commandId, status: finalCommandStatus, robotId });
  } catch (error) {
    console.error(error);
    return json({ error: error instanceof Error ? error.message : "Unexpected server error" }, 500);
  }
});
