/**
 * PUT /syncs/progress  and  GET /syncs/progress/:document
 *
 * Two things about the client that are easy to get wrong:
 *
 * 1. **Push must return exactly 200.** api.json lists [200, 202, 401] as expected
 *    statuses, but KOSyncClient.lua:137 reports success as `res.status == 200`.
 *    A 202 is therefore not "accepted" — it is a failure that gets queued for
 *    retry, silently, forever.
 * 2. **The pull response's `timestamp` drives the client's prompt direction.**
 *    KOReader compares it against its own local page-turn time to decide whether
 *    it is behind or ahead. We still assign it server-side: a device with a wrong
 *    clock will misjudge the prompt wording, but server-side resolution under
 *    `newest` stays correct, which is the property that actually matters.
 */

import { authenticate } from "../auth";
import type { Config } from "../config";
import { resolve, type ProgressRow } from "../conflict";
import { parseMetadata, upsertFromPush } from "../documents";
import { badRequest, json, str, unauthorized } from "../http";
import { nowSeconds } from "../time";

const SELECT_ROWS = `SELECT doc_hash, device_id, device, progress, percentage, updated_at
                     FROM progress WHERE doc_hash = ?1`;

export async function putProgress(request: Request, env: Env, config: Config): Promise<Response> {
  const user = await authenticate(request, env);
  if (!user) return unauthorized();

  let body: Record<string, unknown>;
  try {
    body = await request.json<Record<string, unknown>>();
  } catch {
    return badRequest();
  }

  const document = str(body.document);
  const deviceId = str(body.device_id);
  const device = str(body.device) || null;
  // The client sends `tostring(progress)`; it is a page number for paged formats
  // and an xpointer for reflowable ones. Opaque to us either way.
  const progress = typeof body.progress === "string" ? body.progress : "";
  const percentage = typeof body.percentage === "number" ? body.percentage : Number(body.percentage);

  if (!document || !deviceId || !progress || !Number.isFinite(percentage)) {
    return badRequest();
  }
  const pct = Math.min(1, Math.max(0, percentage));
  const now = nowSeconds(); // server clock, never the device's

  const statements: D1PreparedStatement[] = [
    env.DB.prepare(
      `INSERT INTO progress (doc_hash, device_id, device, progress, percentage, updated_at)
       VALUES (?1, ?2, ?3, ?4, ?5, ?6)
       ON CONFLICT(doc_hash, device_id) DO UPDATE SET
         device     = excluded.device,
         progress   = excluded.progress,
         percentage = excluded.percentage,
         updated_at = excluded.updated_at`,
    ).bind(document, deviceId, device, progress, pct, now),
  ];

  const meta = parseMetadata(body.metadata);
  if (meta) statements.push(upsertFromPush(env.DB, document, meta, now));

  // Runs inside the same implicit transaction as the upsert above, so it sees
  // the row we just wrote. Saves a round trip over resolving separately.
  statements.push(env.DB.prepare(SELECT_ROWS).bind(document));

  const results = await env.DB.batch<ProgressRow>(statements);
  const rows = results[results.length - 1]?.results ?? [];
  const winner = resolve(rows, config.conflictPolicy);
  if (winner) {
    await recordBookStatus(env.DB, winner, config);
  }

  return json({ document, timestamp: now }, 200);
}

export async function getProgress(
  request: Request,
  env: Env,
  config: Config,
  document: string,
): Promise<Response> {
  const user = await authenticate(request, env);
  if (!user) return unauthorized();
  if (!document) return badRequest();

  const { results } = await env.DB.prepare(SELECT_ROWS).bind(document).all<ProgressRow>();
  const winner = resolve(results ?? [], config.conflictPolicy);

  // No rows is a normal state, not an error: the client checks for a missing
  // `percentage` and shows "No progress found for this document."
  if (!winner) return json({ document }, 200);

  return json(
    {
      document,
      progress: winner.progress,
      percentage: winner.percentage,
      // Returned intact so the client can recognise its own push and skip it,
      // and can name the other device in the sync prompt.
      device: winner.device,
      device_id: winner.device_id,
      timestamp: winner.updated_at,
    },
    200,
  );
}

/**
 * Maintain resolved per-book state, including the finished crossing.
 *
 * Done here rather than in the nightly rollup because a crossing that reverses
 * before the cron runs — end matter, a re-read started the same evening — would
 * never be observed by a once-a-day snapshot of the current percentage.
 *
 * A crossing that reverses *within minutes* to well below the threshold is undone
 * instead: that is not a reader, it is Readest computing (page + 1) / totalPages
 * while a book is first laid out and totalPages is still 1. Seen for real — a
 * book "finished" at 01:14:07 was at 14% by 01:15:24. The floor keeps a glance
 * back after a genuine finish (a comic flipped back a page) from undoing it, and
 * prior_finished_at lets the undo restore an earlier genuine finish.
 *
 * Expressed as a single upsert so the previous percentage is read and compared
 * inside SQLite, without a separate read round trip.
 */
function recordBookStatus(db: D1Database, winner: ProgressRow, config: Config): Promise<unknown> {
  const crossed = `book_status.percentage < ?4 AND excluded.percentage >= ?4`;
  const reverted = `book_status.percentage >= ?4 AND excluded.percentage < ?7
                    AND book_status.last_finished_at IS NOT NULL
                    AND excluded.updated_at - book_status.last_finished_at <= ?6`;
  return db
    .prepare(
      `INSERT INTO book_status (doc_hash, percentage, device, updated_at, finished_at, last_finished_at, finish_count)
       VALUES (?1, ?2, ?3, ?5,
               CASE WHEN ?2 >= ?4 THEN ?5 END,
               CASE WHEN ?2 >= ?4 THEN ?5 END,
               CASE WHEN ?2 >= ?4 THEN 1 ELSE 0 END)
       ON CONFLICT(doc_hash) DO UPDATE SET
         percentage        = excluded.percentage,
         device            = excluded.device,
         updated_at        = excluded.updated_at,
         finished_at       = CASE WHEN ${reverted} AND book_status.finish_count <= 1 THEN NULL
                                  ELSE COALESCE(book_status.finished_at,
                                         CASE WHEN ${crossed} THEN excluded.updated_at END) END,
         last_finished_at  = CASE WHEN ${crossed} THEN excluded.updated_at
                                  WHEN ${reverted} THEN book_status.prior_finished_at
                                  ELSE book_status.last_finished_at END,
         prior_finished_at = CASE WHEN ${crossed} THEN book_status.last_finished_at
                                  WHEN ${reverted} THEN NULL
                                  ELSE book_status.prior_finished_at END,
         finish_count      = book_status.finish_count
                             + CASE WHEN ${crossed} THEN 1 WHEN ${reverted} THEN -1 ELSE 0 END`,
    )
    .bind(winner.doc_hash, winner.percentage, winner.device, config.finishedThreshold,
          winner.updated_at, config.spuriousFinishSeconds, config.spuriousFinishBelow)
    .run();
}
