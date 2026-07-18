import { createClient } from "https://esm.sh/@supabase/supabase-js@2";

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
        .in("status", ["PENDING", "PUBLISHED", "ACKNOWLEDGED"])
        .gt("expires_at", new Date().toISOString())
        .limit(1)
        .maybeSingle();
      if (activeCommandError) throw activeCommandError;
      if (activeCommand) return json({ error: "An active mission command already exists for this delivery" }, 409);

      robotId = delivery.robot_id;
      deliveryId = delivery.id;
      commandType = "START_MISSION";
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
      status: "PENDING",
      issued_by: authData.user.id,
      expires_at: expiresAt.toISOString(),
    });
    if (commandError) throw commandError;

    const apiUrl = Deno.env.get("EMQX_API_URL")!;
    const apiKey = Deno.env.get("EMQX_API_KEY")!;
    const apiSecret = Deno.env.get("EMQX_API_SECRET")!;
    const publishResponse = await fetch(`${apiUrl.replace(/\/$/, "")}/api/v5/publish`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Authorization: `Basic ${btoa(`${apiKey}:${apiSecret}`)}`,
      },
      body: JSON.stringify({ topic: `miit/robots/${robotId}/commands`, qos: 1, retain: false, payload: JSON.stringify(envelope) }),
    });
    if (!publishResponse.ok) {
      await admin.from("robot_commands").update({ status: "FAILED", result: { httpStatus: publishResponse.status } }).eq("id", commandId);
      return json({ error: "MQTT publish failed; command remains in the audit log" }, 502);
    }

    const publishedAt = new Date().toISOString();
    const { error: publishedError } = await admin
      .from("robot_commands")
      .update({ status: "PUBLISHED", published_at: publishedAt })
      .eq("id", commandId);
    if (publishedError) throw publishedError;

    if (deliveryId) {
      const { error: deliveryError } = await admin
        .from("deliveries")
        .update({ status: "DISPATCHED", dispatched_at: publishedAt })
        .eq("id", deliveryId)
        .eq("status", "ASSIGNED");
      if (deliveryError) throw deliveryError;
    }
    return json({ commandId, status: "PUBLISHED", robotId });
  } catch (error) {
    console.error(error);
    return json({ error: error instanceof Error ? error.message : "Unexpected server error" }, 500);
  }
});
