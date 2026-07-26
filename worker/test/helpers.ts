import { SELF, env } from "cloudflare:test";

export const USER = "spine-test";
/** Stands in for KOReader's MD5-of-password. Opaque to the server. */
export const KEY = "0123456789abcdef0123456789abcdef";

const KOSYNC_ACCEPT = "application/vnd.koreader.v1+json";

export function authHeaders(username = USER, key = KEY): Record<string, string> {
  return { "x-auth-user": username, "x-auth-key": key, accept: KOSYNC_ACCEPT };
}

export function register(username = USER, password = KEY): Promise<Response> {
  return SELF.fetch("https://spine.test/users/create", {
    method: "POST",
    headers: { "content-type": "application/json", accept: KOSYNC_ACCEPT },
    body: JSON.stringify({ username, password }),
  });
}

export function authorize(headers = authHeaders()): Promise<Response> {
  return SELF.fetch("https://spine.test/users/auth", { headers });
}

export function push(
  body: Record<string, unknown>,
  headers = authHeaders(),
): Promise<Response> {
  return SELF.fetch("https://spine.test/syncs/progress", {
    method: "PUT",
    headers: { ...headers, "content-type": "application/json" },
    body: JSON.stringify(body),
  });
}

export function pull(document: string, headers = authHeaders()): Promise<Response> {
  return SELF.fetch(`https://spine.test/syncs/progress/${encodeURIComponent(document)}`, {
    headers,
  });
}

export function readingApi(): Promise<Response> {
  return SELF.fetch("https://spine.test/api/reading");
}

/**
 * Write a progress row directly, with a chosen `updated_at`.
 *
 * Conflict-policy tests need control over relative timestamps. The HTTP path
 * deliberately refuses to accept a client timestamp, so the only honest way to
 * set one up is to write the row.
 */
export async function seedProgress(row: {
  doc_hash: string;
  device_id: string;
  device?: string;
  progress?: string;
  percentage: number;
  updated_at: number;
}): Promise<void> {
  await env.DB.prepare(
    `INSERT INTO progress (doc_hash, device_id, device, progress, percentage, updated_at)
     VALUES (?1, ?2, ?3, ?4, ?5, ?6)
     ON CONFLICT(doc_hash, device_id) DO UPDATE SET
       device = excluded.device, progress = excluded.progress,
       percentage = excluded.percentage, updated_at = excluded.updated_at`,
  )
    .bind(
      row.doc_hash,
      row.device_id,
      row.device ?? row.device_id,
      row.progress ?? String(Math.round(row.percentage * 100)),
      row.percentage,
      row.updated_at,
    )
    .run();
}

export async function seedSession(row: {
  id: string;
  doc_hash?: string;
  device_id?: string;
  started_at: number;
  ended_at?: number;
  duration_s?: number;
  pages?: number;
  source?: string;
  confidence: string;
  method?: string;
}): Promise<void> {
  await env.DB.prepare(
    `INSERT INTO sessions (id, doc_hash, device_id, started_at, ended_at, duration_s, pages,
                           source, confidence, method, imported_at)
     VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10, ?11)`,
  )
    .bind(
      row.id,
      row.doc_hash ?? null,
      row.device_id ?? null,
      row.started_at,
      row.ended_at ?? null,
      row.duration_s ?? null,
      row.pages ?? null,
      row.source ?? "koreader",
      row.confidence,
      row.method ?? null,
      0,
    )
    .run();
}
