/**
 * kosync authentication.
 *
 * The wire format is `X-AUTH-USER` plus `X-AUTH-KEY`, where the key is an
 * unsalted MD5 of the password, sent on every request. That is weak, and it is
 * inherited from the protocol rather than chosen. The real control is using a
 * random credential that exists nowhere else; everything here is secondary.
 *
 * Stored form is HMAC-SHA-256(AUTH_PEPPER, key) via WebCrypto — deliberately not
 * bcrypt or argon2. A memory-hard KDF on every sync request exceeds the Workers
 * free-tier CPU budget (~10 ms), and it would be spent defending a credential the
 * wire format has already capped at unsalted MD5 in a header. The stored hash
 * only needs to survive a database dump, which a random single-purpose credential
 * survives fine.
 */

const encoder = new TextEncoder();

function toHex(buffer: ArrayBuffer): string {
  const bytes = new Uint8Array(buffer);
  let out = "";
  for (const byte of bytes) out += byte.toString(16).padStart(2, "0");
  return out;
}

/** HMAC-SHA-256(pepper, key), hex encoded. */
export async function hashKey(pepper: string, key: string): Promise<string> {
  const cryptoKey = await crypto.subtle.importKey(
    "raw",
    encoder.encode(pepper),
    { name: "HMAC", hash: "SHA-256" },
    false,
    ["sign"],
  );
  const signature = await crypto.subtle.sign("HMAC", cryptoKey, encoder.encode(key));
  return toHex(signature);
}

/** Constant-time comparison of two hex digests. */
export function timingSafeEqualHex(a: string, b: string): boolean {
  const left = encoder.encode(a);
  const right = encoder.encode(b);
  // timingSafeEqual throws on a length mismatch. Both operands here are fixed
  // width SHA-256 hex, so a mismatch means malformed stored data, not a guess.
  if (left.byteLength !== right.byteLength) return false;
  return crypto.subtle.timingSafeEqual(left, right);
}

export interface AuthedUser {
  username: string;
}

/**
 * Returns the authenticated user, or null.
 *
 * The HMAC is computed before the row is checked so that a request for an unknown
 * username costs the same as one for a known username with a wrong key.
 */
export async function authenticate(request: Request, env: Env): Promise<AuthedUser | null> {
  const username = request.headers.get("x-auth-user");
  const key = request.headers.get("x-auth-key");
  if (!username || !key) return null;

  const expected = await hashKey(env.AUTH_PEPPER, key);
  const row = await env.DB.prepare("SELECT username, key_hash FROM users WHERE username = ?1")
    .bind(username)
    .first<{ username: string; key_hash: string }>();
  if (!row) return null;

  return timingSafeEqualHex(row.key_hash, expected) ? { username: row.username } : null;
}
