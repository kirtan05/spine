import { env } from "cloudflare:test";
import { beforeEach, describe, expect, it } from "vitest";
import { runRollup } from "../src/rollup";
import { istDayStart } from "../src/time";
import { push, readingApi, register, seedSession } from "./helpers";

const DOC = "9f2c1b7e4a3d5c6f8091a2b3c4d5e6f7";
const DOC2 = "1111222233334444555566667777888a";

interface ReadingBody {
  generated_at: number;
  timezone: string;
  window_days: number;
  currently_reading: Array<{ id: string; title: string; percent: number; last_read: string }>;
  recently_finished: Array<{ id: string; title: string; finished: string; times_read: number }>;
  stats: Record<string, { total_seconds: number; total_pages: number; total_sessions: number; streak: { current: number; longest: number }; days: Array<{ day: string; seconds: number }> }>;
}

const readBody = async (): Promise<ReadingBody> => (await readingApi()).json<ReadingBody>();

describe("GET /api/reading", () => {
  it("renders against an empty database", async () => {
    const res = await readingApi();
    expect(res.status).toBe(200);
    const body = await res.json<ReadingBody>();
    expect(body.currently_reading).toEqual([]);
    expect(body.recently_finished).toEqual([]);
    expect(body.stats.measured!.total_seconds).toBe(0);
    expect(body.stats.estimated!.total_seconds).toBe(0);
    expect(body.timezone).toBe("Asia/Kolkata");
  });

  it("is public and cached at the edge", async () => {
    const res = await readingApi();
    expect(res.status).toBe(200);
    expect(res.headers.get("cache-control")).toContain("s-maxage=3600");
  });

  it("shows a book in progress after a push", async () => {
    await register();
    await push({
      document: DOC,
      progress: "142",
      percentage: 0.42,
      device: "Pixel",
      device_id: "pixel-1",
      metadata: { title: "Gardens of the Moon", authors: "Steven Erikson", filename: "gotm.epub" },
    });

    const body = await readBody();
    expect(body.currently_reading).toHaveLength(1);
    expect(body.currently_reading[0]).toMatchObject({ title: "Gardens of the Moon", percent: 42 });
  });

  it("renders a hash with no catalogue row as Unknown rather than hiding it", async () => {
    await register();
    await push({ document: DOC, progress: "1", percentage: 0.05, device: "Pixel", device_id: "pixel-1" });

    const body = await readBody();
    expect(body.currently_reading[0]!.title).toBe("Unknown");
  });

  it("never exposes device identity, raw progress, or usernames", async () => {
    await register();
    await push({
      document: DOC,
      progress: "/body/DocFragment[3]/body/p[17]",
      percentage: 0.42,
      device: "Kirtan's Pixel 9 Pro",
      device_id: "secret-device-id-42",
      metadata: { title: "Gardens of the Moon", authors: "Steven Erikson", filename: "gotm.epub" },
    });

    const raw = await (await readingApi()).text();
    expect(raw).not.toContain("secret-device-id-42");
    expect(raw).not.toContain("Kirtan's Pixel 9 Pro");
    expect(raw).not.toContain("DocFragment");
    expect(raw).not.toContain("spine-test");
    // Nor the full document hash.
    expect(raw).not.toContain(DOC);
  });
});

describe("finished detection", () => {
  beforeEach(async () => {
    await register();
  });

  const pushPercent = (percentage: number, progress = "1") =>
    push({ document: DOC, progress, percentage, device: "Pixel", device_id: "pixel-1" });

  it("marks a book finished when the resolved percentage crosses the threshold", async () => {
    await pushPercent(0.5);
    await pushPercent(0.99);

    const row = await env.DB.prepare(
      "SELECT finished_at, last_finished_at, finish_count FROM book_status WHERE doc_hash = ?1",
    )
      .bind(DOC)
      .first<{ finished_at: number; last_finished_at: number; finish_count: number }>();
    expect(row!.finished_at).toBeGreaterThan(0);
    expect(row!.finish_count).toBe(1);

    const body = await readBody();
    expect(body.recently_finished).toHaveLength(1);
    expect(body.currently_reading).toHaveLength(0);
  });

  it("counts a re-read without moving the original finish date", async () => {
    await pushPercent(0.99);
    const first = await env.DB.prepare("SELECT finished_at FROM book_status WHERE doc_hash = ?1")
      .bind(DOC)
      .first<{ finished_at: number }>();

    await pushPercent(0.02); // started it again
    await pushPercent(0.99); // and finished it again

    const row = await env.DB.prepare(
      "SELECT finished_at, finish_count FROM book_status WHERE doc_hash = ?1",
    )
      .bind(DOC)
      .first<{ finished_at: number; finish_count: number }>();
    expect(row!.finish_count).toBe(2);
    expect(row!.finished_at).toBe(first!.finished_at);
    expect((await readBody()).recently_finished[0]!.times_read).toBe(2);
  });

  it("does not re-count a book that stays above the threshold", async () => {
    await pushPercent(0.99);
    await pushPercent(0.995);
    await pushPercent(1);

    const row = await env.DB.prepare("SELECT finish_count FROM book_status WHERE doc_hash = ?1")
      .bind(DOC)
      .first<{ finish_count: number }>();
    expect(row!.finish_count).toBe(1);
  });
});

describe("nightly rollup", () => {
  it("buckets daily totals on IST dates", async () => {
    // 2026-07-26 19:00 UTC is 2026-07-27 00:30 IST. Bucketing on UTC would file
    // this under the 26th.
    await seedSession({
      id: "s1",
      doc_hash: DOC,
      started_at: Date.parse("2026-07-26T19:00:00Z") / 1000,
      duration_s: 1800,
      pages: 20,
      confidence: "exact",
    });
    // 23:30 IST on the 26th — same IST day as an evening session.
    await seedSession({
      id: "s2",
      doc_hash: DOC,
      started_at: Date.parse("2026-07-26T18:00:00Z") / 1000,
      duration_s: 600,
      pages: 8,
      confidence: "exact",
    });

    await runRollup(env);

    const { results } = await env.DB.prepare(
      "SELECT day, seconds, pages FROM daily_stats WHERE confidence = 'exact' ORDER BY day",
    ).all<{ day: string; seconds: number; pages: number }>();
    expect(results).toEqual([
      { day: "2026-07-26", seconds: 600, pages: 8 },
      { day: "2026-07-27", seconds: 1800, pages: 20 },
    ]);
  });

  it("keeps measured and modelled time in separate rows", async () => {
    const day = istDayStart("2026-07-20") + 3600;
    await seedSession({ id: "m1", doc_hash: DOC, started_at: day, duration_s: 3600, confidence: "exact" });
    await seedSession({
      id: "e1",
      doc_hash: DOC2,
      started_at: day,
      duration_s: 9999,
      confidence: "inferred",
      source: "wellbeing",
      method: "wellbeing-proportional/monthly",
    });

    await runRollup(env);

    const { results } = await env.DB.prepare(
      "SELECT confidence, seconds FROM daily_stats WHERE day = '2026-07-20' ORDER BY confidence",
    ).all<{ confidence: string; seconds: number }>();
    expect(results).toEqual([
      { confidence: "exact", seconds: 3600 },
      { confidence: "inferred", seconds: 9999 },
    ]);

    // And the public API keeps them apart too.
    const body = await readBody();
    expect(body.stats.measured!.total_seconds).toBe(3600);
    expect(body.stats.estimated!.total_seconds).toBe(9999);
  });

  it("is idempotent — running twice does not double any total", async () => {
    await seedSession({
      id: "s1",
      doc_hash: DOC,
      started_at: istDayStart("2026-07-20") + 3600,
      duration_s: 1200,
      pages: 10,
      confidence: "exact",
    });

    await runRollup(env);
    await runRollup(env);

    const row = await env.DB.prepare(
      "SELECT SUM(seconds) AS s, SUM(sessions) AS n FROM daily_stats",
    ).first<{ s: number; n: number }>();
    expect(row).toEqual({ s: 1200, n: 1 });
  });

  it("returns only measured time when filtered to exact", async () => {
    const day = istDayStart("2026-07-20") + 3600;
    await seedSession({ id: "m1", started_at: day, duration_s: 3600, confidence: "exact" });
    await seedSession({ id: "a1", started_at: day, duration_s: 100, confidence: "approx" });
    await seedSession({ id: "i1", started_at: day, duration_s: 5000, confidence: "inferred" });

    const measured = await env.DB.prepare(
      "SELECT COALESCE(SUM(duration_s), 0) AS total FROM sessions WHERE confidence = 'exact'",
    ).first<{ total: number }>();
    expect(measured!.total).toBe(3600);
  });

  it("records streaks split by confidence", async () => {
    for (const [i, day] of ["2026-07-25", "2026-07-26", "2026-07-27"].entries()) {
      await seedSession({
        id: `s${i}`,
        doc_hash: DOC,
        started_at: istDayStart(day) + 3600,
        duration_s: 600,
        confidence: "exact",
      });
    }

    const summary = await runRollup(env);
    expect(summary.streaks.exact!.longest).toBe(3);

    const meta = await env.DB.prepare("SELECT value FROM rollup_meta WHERE key = 'streaks'").first<{
      value: string;
    }>();
    expect(JSON.parse(meta!.value)).toHaveProperty("exact");
  });
});
