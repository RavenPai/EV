import { describe, expect, it } from "vitest";
import { resolveSupabaseUrl } from "../lib/supabase-url";

describe("Supabase browser URL selection", () => {
  it("uses the same-origin proxy for production workers.dev deployments", () => {
    expect(resolveSupabaseUrl({
      configuredUrl: "https://project.supabase.co",
      browserOrigin: "https://delivery.example.workers.dev",
      production: true,
    })).toBe("https://delivery.example.workers.dev/supabase");
  });

  it("uses an explicit proxy path on a custom production domain", () => {
    expect(resolveSupabaseUrl({
      configuredUrl: "https://project.supabase.co",
      proxyPath: "/supabase",
      browserOrigin: "https://delivery.example.com",
      production: true,
    })).toBe("https://delivery.example.com/supabase");
  });

  it("keeps the configured URL for local development", () => {
    expect(resolveSupabaseUrl({
      configuredUrl: "https://project.supabase.co",
      browserOrigin: "http://localhost:4173",
      production: false,
    })).toBe("https://project.supabase.co");
  });
});
