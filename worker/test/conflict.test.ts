import { describe, expect, it } from "vitest";
import { resolve, type ProgressRow } from "../src/conflict";

function row(
  device_id: string,
  percentage: number,
  updated_at: number,
): ProgressRow {
  return {
    doc_hash: "doc",
    device_id,
    device: device_id,
    progress: String(Math.round(percentage * 100)),
    percentage,
    updated_at,
  };
}

describe("resolve", () => {
  it("returns null when no device has reported", () => {
    expect(resolve([], "newest")).toBeNull();
    expect(resolve([], "furthest")).toBeNull();
  });

  it("newest honours a deliberate skip backwards", () => {
    // The Pixel jumped back to re-read a chapter after the tablet had gone further.
    const rows = [row("tablet", 0.8, 100), row("pixel", 0.3, 200)];
    expect(resolve(rows, "newest")?.device_id).toBe("pixel");
  });

  it("furthest keeps the highest position regardless of recency", () => {
    const rows = [row("tablet", 0.8, 100), row("pixel", 0.3, 200)];
    expect(resolve(rows, "furthest")?.device_id).toBe("tablet");
  });

  it("newest breaks a timestamp tie on percentage", () => {
    const rows = [row("a", 0.4, 500), row("b", 0.6, 500)];
    expect(resolve(rows, "newest")?.device_id).toBe("b");
  });

  it("furthest breaks a percentage tie on recency", () => {
    const rows = [row("a", 0.5, 100), row("b", 0.5, 900)];
    expect(resolve(rows, "furthest")?.device_id).toBe("b");
  });

  it("is a total order — row order out of SQLite cannot change the winner", () => {
    const rows = [row("a", 0.5, 100), row("b", 0.5, 100), row("c", 0.5, 100)];
    for (const policy of ["newest", "furthest"] as const) {
      const winner = resolve(rows, policy)?.device_id;
      expect(resolve([...rows].reverse(), policy)?.device_id).toBe(winner);
      expect(resolve([rows[1]!, rows[2]!, rows[0]!], policy)?.device_id).toBe(winner);
    }
  });

  it("handles the three-device case both ways", () => {
    const rows = [row("pixel", 0.55, 300), row("tab-s11", 0.91, 200), row("tab-2", 0.12, 100)];
    expect(resolve(rows, "newest")?.device_id).toBe("pixel");
    expect(resolve(rows, "furthest")?.device_id).toBe("tab-s11");
  });
});
