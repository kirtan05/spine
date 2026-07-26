import path from "node:path";
import { cloudflareTest, readD1Migrations } from "@cloudflare/vitest-pool-workers";
import { defineConfig } from "vitest/config";

/**
 * Tests run against real D1 inside workerd, not a mock. The parts of this system
 * most likely to break — upsert conflict clauses, `date(..., 'unixepoch')`
 * bucketing, UNIQUE violation messages — are exactly the parts a mock would fake.
 */
export default defineConfig(async () => {
  const migrations = await readD1Migrations(path.join(import.meta.dirname, "migrations"));

  return {
    plugins: [
      cloudflareTest({
        wrangler: { configPath: "./wrangler.jsonc" },
        miniflare: {
          bindings: {
            TEST_MIGRATIONS: migrations,
            AUTH_PEPPER: "test-pepper-not-a-real-secret",
          },
        },
      }),
    ],
    test: {
      setupFiles: ["./test/setup.ts"],
    },
  };
});
