/**
 * POST /users/create  and  GET /users/auth
 *
 * Status codes here are load-bearing. KOSyncClient.lua treats register as
 * successful only on 201, and Spore raises a Lua error on any status outside
 * api.json's `expected_status` of [201, 402] — which surfaces on-device as
 * "An error occurred while registering" with no message. So a refusal we want the
 * user to actually read has to be a 402 carrying `message`, not a 403 or a 409.
 */

import { authenticate, hashKey } from "../auth";
import type { Config } from "../config";
import { badRequest, json, unauthorized } from "../http";
import { nowSeconds } from "../time";

function isUniqueViolation(err: unknown): boolean {
  return err instanceof Error && /UNIQUE constraint failed/i.test(err.message);
}

export async function createUser(request: Request, env: Env, config: Config): Promise<Response> {
  let body: Record<string, unknown>;
  try {
    body = await request.json<Record<string, unknown>>();
  } catch {
    return badRequest();
  }

  const username = typeof body.username === "string" ? body.username.trim() : "";
  // `password` is already an MD5 of the real password by the time it reaches us —
  // the client hashes before sending. This is why an account must be registered
  // from KOReader rather than with curl.
  const key = typeof body.password === "string" ? body.password : "";
  if (!username || !key) return badRequest();

  // Registration is open only while `users` is empty, so the endpoint closes
  // itself after the first account without anyone having to remember to do it.
  // ALLOW_REGISTRATION is the re-open override, not the primary gate.
  if (!config.allowRegistration) {
    const existing = await env.DB.prepare("SELECT COUNT(*) AS n FROM users").first<{ n: number }>();
    if ((existing?.n ?? 0) > 0) {
      return json({ message: "Registration is closed." }, 402);
    }
  }

  const keyHash = await hashKey(env.AUTH_PEPPER, key);
  try {
    await env.DB.prepare("INSERT INTO users (username, key_hash, created_at) VALUES (?1, ?2, ?3)")
      .bind(username, keyHash, nowSeconds())
      .run();
  } catch (err) {
    if (isUniqueViolation(err)) {
      return json({ message: "Username is already registered." }, 402);
    }
    throw err;
  }

  return json({ username }, 201);
}

export async function authorizeUser(request: Request, env: Env): Promise<Response> {
  const user = await authenticate(request, env);
  if (!user) return unauthorized();
  return json({ authorized: "OK" }, 200);
}
