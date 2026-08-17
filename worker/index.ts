const PROXY_PREFIX = "/supabase";
const ALLOWED_SERVICES = new Set(["auth", "functions", "realtime", "rest"]);

const jsonError = (message: string, status: number) =>
  Response.json(
    { error: message },
    {
      status,
      headers: {
        "Cache-Control": "no-store",
        "X-Content-Type-Options": "nosniff",
      },
    },
  );

const getProxyTarget = (requestUrl: URL, configuredOrigin: string): URL | null => {
  if (!requestUrl.pathname.startsWith(`${PROXY_PREFIX}/`)) return null;

  const upstreamPath = requestUrl.pathname.slice(PROXY_PREFIX.length);
  const service = upstreamPath.split("/")[1];
  if (!ALLOWED_SERVICES.has(service)) return null;

  const upstreamOrigin = new URL(configuredOrigin);
  if (
    upstreamOrigin.protocol !== "https:" ||
    upstreamOrigin.username ||
    upstreamOrigin.password ||
    upstreamOrigin.pathname !== "/" ||
    upstreamOrigin.search ||
    upstreamOrigin.hash
  ) {
    throw new Error("SUPABASE_ORIGIN must be a bare HTTPS origin");
  }

  return new URL(`${upstreamPath}${requestUrl.search}`, upstreamOrigin);
};

export default {
  async fetch(request: Request, env: Env): Promise<Response> {
    const requestUrl = new URL(request.url);

    if (!requestUrl.pathname.startsWith(`${PROXY_PREFIX}/`)) {
      return env.ASSETS.fetch(request);
    }

    let target: URL | null;
    try {
      target = getProxyTarget(requestUrl, env.SUPABASE_ORIGIN);
    } catch (error) {
      console.error(JSON.stringify({
        message: "invalid Supabase proxy configuration",
        error: error instanceof Error ? error.message : "unknown error",
      }));
      return jsonError("Cloud service configuration is invalid", 500);
    }

    if (!target) {
      return jsonError("Unsupported cloud service path", 404);
    }

    try {
      return await fetch(target, {
        method: request.method,
        headers: request.headers,
        body: request.method === "GET" || request.method === "HEAD"
          ? null
          : request.body,
        redirect: "manual",
      });
    } catch (error) {
      console.error(JSON.stringify({
        message: "Supabase proxy request failed",
        service: target.pathname.split("/")[1],
        error: error instanceof Error ? error.message : "unknown error",
      }));
      return jsonError("Cloud service is temporarily unreachable", 502);
    }
  },
} satisfies ExportedHandler<Env>;
