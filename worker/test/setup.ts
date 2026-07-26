import { applyD1Migrations, env } from "cloudflare:test";
import { beforeAll, beforeEach } from "vitest";

/**
 * Migrate once, then hand every test an empty database.
 *
 * vitest-pool-workers v0.18 dropped the automatic per-test storage isolation the
 * older pool API provided, so tests share one D1 instance and leak rows into each
 * other. Truncating explicitly is both the fix and the more legible contract.
 *
 * `d1_migrations` is deliberately absent: clearing it would make applyD1Migrations
 * re-run the schema on the next file and fail on the existing tables.
 */
const TABLES = [
  "progress",
  "documents",
  "sessions",
  "users",
  "book_status",
  "daily_stats",
  "book_stats",
  "rollup_meta",
];

beforeAll(async () => {
  await applyD1Migrations(env.DB, env.TEST_MIGRATIONS);
});

beforeEach(async () => {
  await env.DB.batch(TABLES.map((table) => env.DB.prepare(`DELETE FROM ${table}`)));
});
