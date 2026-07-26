import { env } from "cloudflare:test";
import { beforeEach, describe, expect, it } from "vitest";
import { authHeaders, pull, push, register } from "./helpers";
import { seedProgress } from "./helpers";

const DOC = "9f2c1b7e4a3d5c6f8091a2b3c4d5e6f7";

interface PullBody {
  document: string;
  progress?: string;
  percentage?: number;
  device?: string | null;
  device_id?: string;
  timestamp?: number;
}

beforeEach(async () => {
  await register();
});

describe("PUT /syncs/progress", () => {
  it("returns exactly 200 — not 202", async () => {
    // api.json lists 202 as expected, but KOSyncClient.lua:137 reports success as
    // `res.status == 200`, so a 202 is a silent failure that gets queued for retry.
    const res = await push({
      document: DOC,
      progress: "142",
      percentage: 0.42,
      device: "Pixel",
      device_id: "pixel-1",
    });
    expect(res.status).toBe(200);
    const body = await res.json<{ document: string; timestamp: number }>();
    expect(body.document).toBe(DOC);
    expect(body.timestamp).toBeGreaterThan(0);
  });

  it("assigns updated_at from the server clock and ignores unknown fields", async () => {
    const before = Math.floor(Date.now() / 1000);
    await push({
      document: DOC,
      progress: "142",
      percentage: 0.42,
      device: "Pixel",
      device_id: "pixel-1",
      // A device with a badly wrong clock cannot influence resolution: the
      // protocol carries no client timestamp, and anything extra is ignored.
      timestamp: 1,
      updated_at: 1,
      nonsense: { deeply: "nested" },
    });
    const after = Math.floor(Date.now() / 1000);

    const row = await env.DB.prepare("SELECT updated_at FROM progress WHERE doc_hash = ?1")
      .bind(DOC)
      .first<{ updated_at: number }>();
    expect(row!.updated_at).toBeGreaterThanOrEqual(before);
    expect(row!.updated_at).toBeLessThanOrEqual(after);
  });

  it("keeps one row per device rather than collapsing to one per document", async () => {
    await push({ document: DOC, progress: "10", percentage: 0.1, device: "Pixel", device_id: "pixel-1" });
    await push({ document: DOC, progress: "80", percentage: 0.8, device: "Tab S11", device_id: "tab-1" });

    const { results } = await env.DB.prepare("SELECT device_id FROM progress WHERE doc_hash = ?1")
      .bind(DOC)
      .all<{ device_id: string }>();
    expect(results!.map((r) => r.device_id).sort()).toEqual(["pixel-1", "tab-1"]);
  });

  it("clamps a percentage outside 0..1", async () => {
    await push({ document: DOC, progress: "1", percentage: 1.7, device: "Pixel", device_id: "pixel-1" });
    const row = await env.DB.prepare("SELECT percentage FROM progress WHERE doc_hash = ?1")
      .bind(DOC)
      .first<{ percentage: number }>();
    expect(row!.percentage).toBe(1);
  });

  it("rejects a push missing required fields", async () => {
    const res = await push({ document: DOC, percentage: 0.5, device_id: "pixel-1" });
    expect(res.status).toBe(400);
  });
});

describe("GET /syncs/progress/:document", () => {
  it("returns the position pushed from a different device", async () => {
    await push({
      document: DOC,
      progress: "/body/DocFragment[3]/body/p[17]",
      percentage: 0.61,
      device: "Tab S11",
      device_id: "tab-1",
    });

    const body = await (await pull(DOC)).json<PullBody>();
    expect(body.progress).toBe("/body/DocFragment[3]/body/p[17]");
    expect(body.percentage).toBeCloseTo(0.61);
    // The client needs these to recognise its own push and to name the other
    // device in the sync prompt.
    expect(body.device).toBe("Tab S11");
    expect(body.device_id).toBe("tab-1");
    expect(body.timestamp).toBeGreaterThan(0);
  });

  it("returns 200 with no percentage for a document nobody has read", async () => {
    const res = await pull("never-seen-hash");
    expect(res.status).toBe(200);
    // The client checks for a missing `percentage` and shows
    // "No progress found for this document." An error status would read as a
    // sync failure instead.
    expect((await res.json<PullBody>()).percentage).toBeUndefined();
  });

  it("reconciles three devices that were all offline (default policy: newest)", async () => {
    await seedProgress({ doc_hash: DOC, device_id: "pixel-1", percentage: 0.30, updated_at: 1_700_000_300 });
    await seedProgress({ doc_hash: DOC, device_id: "tab-1", percentage: 0.90, updated_at: 1_700_000_100 });
    await seedProgress({ doc_hash: DOC, device_id: "tab-2", percentage: 0.55, updated_at: 1_700_000_200 });

    const body = await (await pull(DOC)).json<PullBody>();
    expect(body.device_id).toBe("pixel-1");
    expect(body.timestamp).toBe(1_700_000_300);
  });

  it("a live push wins over older offline rows", async () => {
    await seedProgress({ doc_hash: DOC, device_id: "tab-1", percentage: 0.90, updated_at: 1_700_000_100 });
    await push({ document: DOC, progress: "12", percentage: 0.12, device: "Pixel", device_id: "pixel-1" });

    const body = await (await pull(DOC)).json<PullBody>();
    expect(body.device_id).toBe("pixel-1");
    expect(body.percentage).toBeCloseTo(0.12);
  });
});

describe("document metadata", () => {
  it("upserts a title from a push that carries metadata", async () => {
    await push({
      document: DOC,
      progress: "1",
      percentage: 0.01,
      device: "Pixel",
      device_id: "pixel-1",
      metadata: { filename: "gardens-of-the-moon.epub", title: "Gardens of the Moon", authors: "Steven Erikson" },
    });

    const row = await env.DB.prepare("SELECT title, authors, filename FROM documents WHERE doc_hash = ?1")
      .bind(DOC)
      .first<{ title: string; authors: string; filename: string }>();
    expect(row).toMatchObject({
      title: "Gardens of the Moon",
      authors: "Steven Erikson",
      filename: "gardens-of-the-moon.epub",
    });
  });

  it("does not clobber a curated catalogue title", async () => {
    // The ingest pipeline publishes comic titles from ComicInfo.xml. A metadata
    // push arrives every 25 seconds; if it overwrote, the curated title would
    // survive for exactly one sync.
    await env.DB.prepare(
      `INSERT INTO documents (doc_hash, title, authors, series, series_index, first_seen)
       VALUES (?1, 'Saga #12', 'Brian K. Vaughan', 'Saga', 12, 0)`,
    )
      .bind(DOC)
      .run();

    await push({
      document: DOC,
      progress: "1",
      percentage: 0.01,
      device: "Pixel",
      device_id: "pixel-1",
      metadata: { filename: "saga12.cbz", title: "saga12", authors: "unknown" },
    });

    const row = await env.DB.prepare("SELECT title, series, series_index, filename FROM documents WHERE doc_hash = ?1")
      .bind(DOC)
      .first<{ title: string; series: string; series_index: number; filename: string }>();
    expect(row!.title).toBe("Saga #12");
    expect(row!.series).toBe("Saga");
    expect(row!.series_index).toBe(12);
    // A gap the catalogue left empty is still fillable.
    expect(row!.filename).toBe("saga12.cbz");
  });

  it("leaves no documents row when the device sends no metadata", async () => {
    // "Send document metadata" is off by default in KOReader. A hash with no
    // catalogue row is the signal that a device or pipeline step was missed —
    // writing a blank row would erase that signal.
    await push({ document: DOC, progress: "1", percentage: 0.01, device: "Pixel", device_id: "pixel-1" });
    const row = await env.DB.prepare("SELECT doc_hash FROM documents WHERE doc_hash = ?1")
      .bind(DOC)
      .first();
    expect(row).toBeNull();
  });
});
