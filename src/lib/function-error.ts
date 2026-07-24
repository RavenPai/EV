type ResponseLikeError = {
  context?: unknown;
  message?: unknown;
};

function payloadMessage(payload: unknown): string | undefined {
  if (!payload || typeof payload !== "object") return undefined;
  const candidate = payload as Record<string, unknown>;
  for (const field of ["error", "message"]) {
    const value = candidate[field];
    if (typeof value === "string" && value.trim()) return value.trim();
  }
  return undefined;
}

export async function functionErrorMessage(
  error: unknown,
  fallback = "The request could not be completed.",
): Promise<string> {
  if (error && typeof error === "object") {
    const context = (error as ResponseLikeError).context;
    if (context instanceof Response) {
      try {
        const payload = await context.clone().json();
        const message = payloadMessage(payload);
        if (message) return message;
      } catch {
        // Fall through to the normal Error message or the safe fallback.
      }
    }
  }
  if (error instanceof Error && error.message.trim()) {
    return error.message.trim();
  }
  return fallback;
}
