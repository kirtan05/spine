/**
 * Runtime configuration, read from wrangler `vars` on every request.
 *
 * Everything here is deliberately a plain env var rather than a build-time
 * constant: the conflict policy in particular is meant to be switchable after
 * living with the system for a while (PRD §6).
 */

export type ConflictPolicy = "newest" | "furthest";

export interface Config {
  /** How to pick a winner among per-device progress rows. */
  conflictPolicy: ConflictPolicy;
  /** Re-open registration after the first account exists. */
  allowRegistration: boolean;
  /** Resolved percentage at which a book counts as finished. */
  finishedThreshold: number;
  /** How far back the public route exposes finished books. */
  recentWindowDays: number;
}

function asBool(value: unknown, fallback: boolean): boolean {
  if (typeof value !== "string") return fallback;
  const v = value.trim().toLowerCase();
  if (v === "true" || v === "1" || v === "yes") return true;
  if (v === "false" || v === "0" || v === "no") return false;
  return fallback;
}

function asNumber(value: unknown, fallback: number): number {
  if (typeof value === "number") return Number.isFinite(value) ? value : fallback;
  if (typeof value !== "string") return fallback;
  const n = Number(value.trim());
  return Number.isFinite(n) ? n : fallback;
}

export function readConfig(env: Env): Config {
  // `wrangler types` gives vars their literal value from wrangler.jsonc as their
  // type, so these have to be widened before they can be compared against
  // anything the deployed config might actually be set to.
  const policy = String(env.CONFLICT_POLICY ?? "").trim().toLowerCase();
  return {
    // Anything unrecognised falls back to `newest`, the PRD default. A typo in a
    // var should not silently change resolution semantics to the other policy.
    conflictPolicy: policy === "furthest" ? "furthest" : "newest",
    allowRegistration: asBool(env.ALLOW_REGISTRATION, false),
    finishedThreshold: asNumber(env.FINISHED_THRESHOLD, 0.98),
    recentWindowDays: asNumber(env.RECENT_WINDOW_DAYS, 90),
  };
}
