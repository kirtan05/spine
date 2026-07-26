/**
 * Response helpers.
 *
 * Status codes are part of the kosync contract, not a stylistic choice. The
 * KOReader client branches on them directly and the wrong code reads as an auth
 * failure on-device. See src/routes/syncs.ts for the specific traps.
 */

export function json(body: unknown, status = 200, headers: Record<string, string> = {}): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json; charset=utf-8", ...headers },
  });
}

/** KOReader renders `body.message` verbatim in its error dialog. */
export function unauthorized(): Response {
  return json({ message: "Unauthorized" }, 401);
}

export function badRequest(message = "Invalid request"): Response {
  return json({ message }, 400);
}

export function notFound(): Response {
  return json({ message: "Not found" }, 404);
}

/** Narrow an untrusted JSON field to a non-empty trimmed string. */
export function str(value: unknown): string {
  return typeof value === "string" ? value.trim() : "";
}
