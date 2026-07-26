// `env` from "cloudflare:test" is typed as `Cloudflare.Env`, which `wrangler types`
// generates from wrangler.jsonc. TEST_MIGRATIONS is injected by vitest.config.ts and
// exists only under test, so it is declared here rather than in the wrangler config.
declare namespace Cloudflare {
  interface Env {
    TEST_MIGRATIONS: import("@cloudflare/vitest-pool-workers").D1Migration[];
  }
}
