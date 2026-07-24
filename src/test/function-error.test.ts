import { describe, expect, it } from "vitest";
import { functionErrorMessage } from "../lib/function-error";

describe("functionErrorMessage", () => {
  it("extracts the Edge Function JSON error from a FunctionsHttpError context", async () => {
    const error = {
      message: "Edge Function returned a non-2xx status code",
      context: new Response(
        JSON.stringify({ error: "Assigned robot is not ready with fresh telemetry" }),
        {
          status: 409,
          headers: { "content-type": "application/json" },
        },
      ),
    };

    await expect(functionErrorMessage(error)).resolves.toBe(
      "Assigned robot is not ready with fresh telemetry",
    );
  });

  it("falls back to a normal Error message", async () => {
    await expect(functionErrorMessage(new Error("Network unavailable"))).resolves.toBe(
      "Network unavailable",
    );
  });
});
