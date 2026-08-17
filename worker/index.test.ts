import { afterEach, describe, expect, it, vi } from "vitest";
import worker from "./index";

const assetsResponse = new Response("asset");
const env = {
  ASSETS: { fetch: vi.fn(async () => assetsResponse.clone()) },
  SUPABASE_ORIGIN: "https://example-project.supabase.co",
} as Env;

afterEach(() => {
  vi.restoreAllMocks();
  env.ASSETS.fetch.mockClear();
});

describe("Cloudflare Supabase proxy", () => {
  it("leaves non-proxy requests with the static asset binding", async () => {
    const response = await worker.fetch(
      new Request("https://delivery.example/dispatch"),
      env,
      {} as ExecutionContext,
    );

    expect(await response.text()).toBe("asset");
    expect(env.ASSETS.fetch).toHaveBeenCalledOnce();
  });

  it("streams allowed Auth requests to the fixed Supabase origin", async () => {
    const upstreamFetch = vi.fn(async (target: URL | RequestInfo, init?: RequestInit) =>
      Response.json({
        url: target.toString(),
        apiKey: new Headers(init?.headers).get("apikey"),
        body: init?.body ? await new Response(init.body).text() : "",
      }));
    vi.stubGlobal("fetch", upstreamFetch);

    const response = await worker.fetch(
      new Request(
        "https://delivery.example/supabase/auth/v1/token?grant_type=password",
        {
          method: "POST",
          headers: { apikey: "browser-safe-key" },
          body: JSON.stringify({ email: "user@example.com" }),
        },
      ),
      env,
      {} as ExecutionContext,
    );
    const body = await response.json();

    expect(body).toEqual({
      url: "https://example-project.supabase.co/auth/v1/token?grant_type=password",
      apiKey: "browser-safe-key",
      body: JSON.stringify({ email: "user@example.com" }),
    });
    expect(upstreamFetch).toHaveBeenCalledOnce();
  });

  it("rejects paths outside the required Supabase services", async () => {
    const upstreamFetch = vi.fn();
    vi.stubGlobal("fetch", upstreamFetch);

    const response = await worker.fetch(
      new Request("https://delivery.example/supabase/unknown/v1/data"),
      env,
      {} as ExecutionContext,
    );

    expect(response.status).toBe(404);
    expect(upstreamFetch).not.toHaveBeenCalled();
  });

  it("returns a safe 502 response when Supabase cannot be reached", async () => {
    vi.spyOn(console, "error").mockImplementation(() => undefined);
    vi.stubGlobal("fetch", vi.fn(async () => {
      throw new Error("private upstream detail");
    }));

    const response = await worker.fetch(
      new Request("https://delivery.example/supabase/rest/v1/robots"),
      env,
      {} as ExecutionContext,
    );

    expect(response.status).toBe(502);
    expect(await response.json()).toEqual({
      error: "Cloud service is temporarily unreachable",
    });
  });
});
