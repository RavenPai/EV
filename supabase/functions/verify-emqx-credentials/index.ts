/**
 * Temporary L6 operator probe. Deploy this function only during credential
 * rotation, protect it with a fresh L6_PROBE_TOKEN, then delete the deployed
 * function and unset that token immediately after verification.
 */

const json = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), {
    status,
    headers: {
      "Content-Type": "application/json",
      "Cache-Control": "no-store",
    },
  });

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

const requestWithTimeout = async (
  url: string,
  init: RequestInit,
): Promise<Response> => {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 10_000);
  try {
    return await fetch(url, { ...init, signal: controller.signal });
  } finally {
    clearTimeout(timeout);
  }
};

Deno.serve(async (request) => {
  if (request.method !== "POST") {
    return json({ error: "Method not allowed" }, 405);
  }

  const expectedToken = Deno.env.get("L6_PROBE_TOKEN") ?? "";
  const suppliedToken = request.headers.get("x-l6-probe-token") ?? "";
  if (
    !expectedToken ||
    !suppliedToken ||
    !constantTimeEqual(suppliedToken, expectedToken)
  ) {
    return json({ error: "Unauthorized" }, 401);
  }

  const apiUrl = (Deno.env.get("EMQX_API_URL") ?? "").replace(/\/$/, "");
  const apiKey = Deno.env.get("EMQX_API_KEY") ?? "";
  const apiSecret = Deno.env.get("EMQX_API_SECRET") ?? "";
  if (!apiUrl || !apiKey || !apiSecret) {
    return json({ error: "EMQX server configuration is incomplete" }, 500);
  }

  const authorization = `Basic ${btoa(`${apiKey}:${apiSecret}`)}`;
  const headers = {
    Accept: "application/json",
    Authorization: authorization,
  };

  try {
    const clientsResponse = await requestWithTimeout(
      `${apiUrl}/api/v5/clients?page=1&limit=1`,
      { method: "GET", headers },
    );

    const publishResponse = await requestWithTimeout(
      `${apiUrl}/api/v5/publish`,
      {
        method: "POST",
        headers: { ...headers, "Content-Type": "application/json" },
        body: JSON.stringify({
          topic: `miit/tests/credential-rotation/${crypto.randomUUID()}`,
          qos: 1,
          retain: false,
          payload: JSON.stringify({
            purpose: "credential-rotation-probe",
            nonPhysical: true,
            at: new Date().toISOString(),
          }),
        }),
      },
    );

    const clientsStatus = clientsResponse.status;
    const publishStatus = publishResponse.status;
    const passed = clientsStatus === 200 && [200, 202].includes(publishStatus);
    return json({
      passed,
      clientsStatus,
      publishStatus,
      publishClassification: publishStatus === 200
        ? "delivered"
        : publishStatus === 202
        ? "no_matching_subscribers"
        : "unexpected",
    }, passed ? 200 : 502);
  } catch (error) {
    const reason = error instanceof DOMException && error.name === "AbortError"
      ? "timeout"
      : "network_error";
    return json({ passed: false, reason }, 502);
  }
});
