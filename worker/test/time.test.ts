import { describe, expect, it } from "vitest";
import { computeStreak } from "../src/rollup";
import { daysBetween, istDay, istDayStart } from "../src/time";

describe("IST bucketing", () => {
  it("files a 23:30 IST session under that IST date, not the UTC one", () => {
    // 2026-07-26 23:30 IST is 2026-07-26 18:00 UTC — same date either way.
    expect(istDay(Date.parse("2026-07-26T18:00:00Z") / 1000)).toBe("2026-07-26");
  });

  it("files a session after 18:30 UTC under the next IST day", () => {
    // 2026-07-26 19:00 UTC is 2026-07-27 00:30 IST. Bucketing on UTC would put
    // this reading on the wrong day, permanently.
    expect(istDay(Date.parse("2026-07-26T19:00:00Z") / 1000)).toBe("2026-07-27");
  });

  it("keeps 00:30 IST on the day that just started", () => {
    expect(istDay(Date.parse("2026-07-26T19:30:00Z") / 1000)).toBe("2026-07-27");
  });

  it("round-trips day boundaries", () => {
    const start = istDayStart("2026-07-27");
    expect(istDay(start)).toBe("2026-07-27");
    expect(istDay(start + 86_399)).toBe("2026-07-27");
    expect(istDay(start + 86_400)).toBe("2026-07-28");
  });

  it("measures whole days between IST dates", () => {
    expect(daysBetween("2026-07-01", "2026-07-31")).toBe(30);
    expect(daysBetween("2026-02-28", "2026-03-01")).toBe(1);
  });
});

describe("computeStreak", () => {
  it("is zero with no reading days", () => {
    expect(computeStreak([], "2026-07-27")).toEqual({ current: 0, longest: 0 });
  });

  it("counts a run ending today", () => {
    const days = ["2026-07-25", "2026-07-26", "2026-07-27"];
    expect(computeStreak(days, "2026-07-27")).toEqual({ current: 3, longest: 3 });
  });

  it("keeps the streak alive when today has not been read yet", () => {
    // The cron fires at 01:00 IST. A streak that resets every night at one in the
    // morning, before you have had a chance to read, would be useless.
    const days = ["2026-07-25", "2026-07-26"];
    expect(computeStreak(days, "2026-07-27")).toEqual({ current: 2, longest: 2 });
  });

  it("ends the streak after a full missed day", () => {
    const days = ["2026-07-24", "2026-07-25"];
    expect(computeStreak(days, "2026-07-27").current).toBe(0);
  });

  it("reports the longest historical run even when the current one is shorter", () => {
    const days = [
      "2026-06-01", "2026-06-02", "2026-06-03", "2026-06-04",
      "2026-07-26", "2026-07-27",
    ];
    expect(computeStreak(days, "2026-07-27")).toEqual({ current: 2, longest: 4 });
  });

  it("ignores duplicate and unsorted input", () => {
    const days = ["2026-07-27", "2026-07-25", "2026-07-26", "2026-07-26"];
    expect(computeStreak(days, "2026-07-27")).toEqual({ current: 3, longest: 3 });
  });
});
