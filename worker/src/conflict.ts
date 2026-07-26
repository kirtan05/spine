/**
 * Conflict resolution.
 *
 * `progress` stores one row per (doc_hash, device_id) rather than one row per
 * document. That is what makes the policy switchable after the fact and what
 * makes a ping-pong between two devices debuggable instead of invisible —
 * collapsing to a single row destroys the evidence at write time.
 *
 * Resolution is therefore a pure function over those rows, with no I/O and no
 * clock access, which also makes it the easiest part of the system to test.
 */

import type { ConflictPolicy } from "./config";

export interface ProgressRow {
  doc_hash: string;
  device_id: string;
  device: string | null;
  progress: string;
  percentage: number;
  /** Server clock at push time. Never the device's — device clocks skew. */
  updated_at: number;
}

/**
 * Pick the winning row.
 *
 * - `newest`: most recent push wins. Correct for re-reads and for deliberately
 *   skipping backwards, which `furthest` would silently undo.
 * - `furthest`: highest percentage wins. Safer for straightforwardly linear
 *   reading, where a stale device pushing an old position is the failure to fear.
 *
 * Ties break on the other field, then on device_id, so the result is total and
 * stable rather than dependent on row order out of SQLite.
 */
export function resolve(rows: readonly ProgressRow[], policy: ConflictPolicy): ProgressRow | null {
  let best: ProgressRow | null = null;
  for (const row of rows) {
    if (best === null || beats(row, best, policy)) best = row;
  }
  return best;
}

function beats(candidate: ProgressRow, incumbent: ProgressRow, policy: ConflictPolicy): boolean {
  const [primary, secondary] =
    policy === "furthest"
      ? [candidate.percentage - incumbent.percentage, candidate.updated_at - incumbent.updated_at]
      : [candidate.updated_at - incumbent.updated_at, candidate.percentage - incumbent.percentage];

  if (primary !== 0) return primary > 0;
  if (secondary !== 0) return secondary > 0;
  return candidate.device_id > incumbent.device_id;
}
