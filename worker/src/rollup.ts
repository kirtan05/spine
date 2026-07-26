/**
 * Nightly aggregation of `sessions` into the summary tables.
 *
 * Two rules the rest of the system depends on:
 *
 * 1. **Never sum across confidence.** `confidence` is in the primary key of every
 *    aggregate table, so there is no row that can hold measured and modelled time
 *    added together. Reconstructed history is seductive and a lifetime-hours
 *    figure that silently mixes the two is worse than no figure at all.
 * 2. **Bucket on IST dates.** See src/time.ts.
 *
 * This is a full recompute rather than an incremental update. At one reader's
 * volume the cost is trivial, and it means a corrected or re-imported session row
 * fixes the aggregates on the next run instead of leaving permanent drift.
 */

import { IST_OFFSET_SECONDS, istDay, nowSeconds } from "./time";

export interface Streak {
  current: number;
  longest: number;
}

export interface RollupSummary {
  ran_at: number;
  days: number;
  books: number;
  streaks: Record<string, Streak>;
}

export async function runRollup(env: Env): Promise<RollupSummary> {
  const ranAt = nowSeconds();

  await env.DB.batch([
    env.DB.prepare("DELETE FROM daily_stats"),
    env.DB.prepare(
      `INSERT INTO daily_stats (day, confidence, seconds, pages, sessions, books)
       SELECT date(started_at + ?1, 'unixepoch') AS day,
              confidence,
              COALESCE(SUM(duration_s), 0),
              COALESCE(SUM(pages), 0),
              COUNT(*),
              COUNT(DISTINCT doc_hash)
       FROM sessions
       GROUP BY day, confidence`,
    ).bind(IST_OFFSET_SECONDS),
    env.DB.prepare("DELETE FROM book_stats"),
    env.DB.prepare(
      `INSERT INTO book_stats (doc_hash, confidence, seconds, pages, sessions, first_read, last_read)
       SELECT doc_hash,
              confidence,
              COALESCE(SUM(duration_s), 0),
              COALESCE(SUM(pages), 0),
              COUNT(*),
              MIN(started_at),
              MAX(COALESCE(ended_at, started_at))
       FROM sessions
       WHERE doc_hash IS NOT NULL
       GROUP BY doc_hash, confidence`,
    ),
  ]);

  const today = istDay(ranAt);
  const [exactDays, allDays, bookCount] = await Promise.all([
    distinctDays(env, "WHERE confidence = 'exact'"),
    distinctDays(env, ""),
    env.DB.prepare("SELECT COUNT(DISTINCT doc_hash) AS n FROM book_stats").first<{ n: number }>(),
  ]);

  const streaks: Record<string, Streak> = {
    exact: computeStreak(exactDays, today),
    all: computeStreak(allDays, today),
  };

  await env.DB.batch([
    metaUpsert(env, "last_run", String(ranAt)),
    metaUpsert(env, "streaks", JSON.stringify(streaks)),
  ]);

  return { ran_at: ranAt, days: allDays.length, books: bookCount?.n ?? 0, streaks };
}

async function distinctDays(env: Env, where: string): Promise<string[]> {
  const { results } = await env.DB.prepare(
    `SELECT DISTINCT day FROM daily_stats ${where} ORDER BY day`,
  ).all<{ day: string }>();
  return (results ?? []).map((r) => r.day);
}

function metaUpsert(env: Env, key: string, value: string): D1PreparedStatement {
  return env.DB.prepare(
    `INSERT INTO rollup_meta (key, value) VALUES (?1, ?2)
     ON CONFLICT(key) DO UPDATE SET value = excluded.value`,
  ).bind(key, value);
}

/**
 * Longest run of consecutive reading days, and the run in progress.
 *
 * `current` tolerates today being empty: the cron fires at 01:00 IST, when
 * "today" has barely started, and a streak that resets every night at one in the
 * morning would be useless. A gap of two days ends it.
 */
export function computeStreak(days: readonly string[], today: string): Streak {
  if (days.length === 0) return { current: 0, longest: 0 };

  const dayNumber = (d: string) => Math.floor(Date.parse(`${d}T00:00:00Z`) / 86_400_000);
  const sorted = [...new Set(days)].sort();
  const nums = sorted.map(dayNumber);

  let longest = 1;
  let run = 1;
  for (let i = 1; i < nums.length; i++) {
    run = nums[i]! - nums[i - 1]! === 1 ? run + 1 : 1;
    if (run > longest) longest = run;
  }

  const gapToToday = dayNumber(today) - nums[nums.length - 1]!;
  const current = gapToToday <= 1 ? run : 0;
  return { current, longest };
}
