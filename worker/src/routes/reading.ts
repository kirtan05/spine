/**
 * GET /api/reading — the only public route.
 *
 * Everything else on this Worker is behind kosync auth. What leaves here is
 * deliberately narrow: no device_id, no device names, no raw progress strings, no
 * usernames, and no full document hashes. Finished books are limited to a
 * RECENT_WINDOW_DAYS window rather than rendering the whole history.
 *
 * Aggregates are split into `measured` (confidence = 'exact') and `estimated`
 * (everything else) and never combined, so nothing on the page can present a
 * modelled number as an observed one.
 *
 * Cached at the edge for an hour: page views should not reach D1.
 */

import type { Config } from "../config";
import { json } from "../http";
import type { Streak } from "../rollup";
import { istDay, nowSeconds } from "../time";

const CACHE_SECONDS = 3600;
const MAX_ITEMS = 25;

interface BookRow {
  doc_hash: string;
  title: string | null;
  authors: string | null;
  series: string | null;
  series_index: number | null;
  percentage: number;
  updated_at: number;
  last_finished_at: number | null;
  finish_count: number;
}

interface DailyRow {
  day: string;
  confidence: string;
  seconds: number;
  pages: number;
  sessions: number;
  books: number;
}

/** A hash with no catalogue row is a signal that an ingest step was missed. */
function present(row: BookRow) {
  return {
    id: row.doc_hash.slice(0, 12),
    title: row.title ?? "Unknown",
    authors: row.authors,
    series: row.series,
    series_index: row.series_index,
  };
}

export async function readingJson(env: Env, config: Config): Promise<Response> {
  const now = nowSeconds();
  const since = now - config.recentWindowDays * 86_400;

  const [current, finished, daily, meta] = await Promise.all([
    env.DB.prepare(
      `SELECT b.doc_hash, b.percentage, b.updated_at, b.last_finished_at, b.finish_count,
              d.title, d.authors, d.series, d.series_index
       FROM book_status b
       LEFT JOIN documents d ON d.doc_hash = b.doc_hash
       WHERE b.percentage < ?1 AND b.updated_at >= ?2
       ORDER BY b.updated_at DESC
       LIMIT ?3`,
    )
      .bind(config.finishedThreshold, since, MAX_ITEMS)
      .all<BookRow>(),

    env.DB.prepare(
      `SELECT b.doc_hash, b.percentage, b.updated_at, b.last_finished_at, b.finish_count,
              d.title, d.authors, d.series, d.series_index
       FROM book_status b
       LEFT JOIN documents d ON d.doc_hash = b.doc_hash
       WHERE b.last_finished_at IS NOT NULL AND b.last_finished_at >= ?1
       ORDER BY b.last_finished_at DESC
       LIMIT ?2`,
    )
      .bind(since, MAX_ITEMS)
      .all<BookRow>(),

    env.DB.prepare(
      `SELECT day, confidence, seconds, pages, sessions, books
       FROM daily_stats WHERE day >= ?1 ORDER BY day`,
    )
      .bind(istDay(since))
      .all<DailyRow>(),

    env.DB.prepare("SELECT key, value FROM rollup_meta").all<{ key: string; value: string }>(),
  ]);

  const metaMap = new Map((meta.results ?? []).map((r) => [r.key, r.value]));
  const streaks = safeJson<Record<string, Streak>>(metaMap.get("streaks"), {});
  const lastRun = Number(metaMap.get("last_run") ?? 0) || null;

  const body = {
    generated_at: now,
    timezone: "Asia/Kolkata",
    window_days: config.recentWindowDays,
    rollup_last_run: lastRun,

    currently_reading: (current.results ?? []).map((row) => ({
      ...present(row),
      percent: Math.round(row.percentage * 1000) / 10,
      last_read: istDay(row.updated_at),
    })),

    recently_finished: (finished.results ?? []).map((row) => ({
      ...present(row),
      finished: istDay(row.last_finished_at ?? row.updated_at),
      times_read: row.finish_count,
    })),

    stats: {
      measured: bucket(daily.results ?? [], (c) => c === "exact", streaks.exact),
      estimated: bucket(daily.results ?? [], (c) => c !== "exact", streaks.all),
    },
  };

  return json(body, 200, {
    "cache-control": `public, s-maxage=${CACHE_SECONDS}`,
    "access-control-allow-origin": "*",
  });
}

/**
 * `measured` is what somebody's device actually timed. `estimated` is everything
 * a model produced. The page must label them; the API refuses to merge them.
 */
function bucket(rows: readonly DailyRow[], keep: (confidence: string) => boolean, streak?: Streak) {
  const days = new Map<string, { day: string; seconds: number; pages: number; sessions: number }>();
  let seconds = 0;
  let pages = 0;
  let sessions = 0;

  for (const row of rows) {
    if (!keep(row.confidence)) continue;
    seconds += row.seconds;
    pages += row.pages;
    sessions += row.sessions;
    const entry = days.get(row.day) ?? { day: row.day, seconds: 0, pages: 0, sessions: 0 };
    entry.seconds += row.seconds;
    entry.pages += row.pages;
    entry.sessions += row.sessions;
    days.set(row.day, entry);
  }

  return {
    total_seconds: seconds,
    total_pages: pages,
    total_sessions: sessions,
    streak: streak ?? { current: 0, longest: 0 },
    days: [...days.values()].sort((a, b) => a.day.localeCompare(b.day)),
  };
}

function safeJson<T>(raw: string | undefined, fallback: T): T {
  if (!raw) return fallback;
  try {
    return JSON.parse(raw) as T;
  } catch {
    return fallback;
  }
}
