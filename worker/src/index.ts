/**
 * spine — a kosync-compatible reading-progress server.
 *
 * The only always-on component in the system. Everything else (library files,
 * ingest pipeline, statistics importers) may be offline indefinitely without
 * blocking reading, which is the whole point of the split.
 *
 * Routing is hand-rolled rather than framework-based on purpose: six routes do
 * not justify a router, and auth verification has to stay inside the free-tier
 * CPU budget alongside an HMAC.
 */

import { readConfig } from "./config";
import { json, notFound } from "./http";
import { readingJson } from "./routes/reading";
import { getProgress, putProgress } from "./routes/syncs";
import { authorizeUser, createUser } from "./routes/users";
import { runRollup } from "./rollup";

const PROGRESS_PREFIX = "/syncs/progress/";

async function handle(request: Request, env: Env): Promise<Response> {
  const url = new URL(request.url);
  const path = url.pathname.length > 1 ? url.pathname.replace(/\/+$/, "") : url.pathname;
  const method = request.method.toUpperCase();
  const config = readConfig(env);

  if (method === "POST" && path === "/users/create") return createUser(request, env, config);
  if (method === "GET" && path === "/users/auth") return authorizeUser(request, env);

  if (method === "PUT" && path === "/syncs/progress") return putProgress(request, env, config);
  if (method === "GET" && path.startsWith(PROGRESS_PREFIX)) {
    const document = decodeURIComponent(path.slice(PROGRESS_PREFIX.length));
    return getProgress(request, env, config, document);
  }

  if (method === "GET" && path === "/api/reading") return readingJson(env, config);

  if (method === "GET" && (path === "/api/healthcheck" || path === "/healthcheck")) {
    return json({ state: "OK" });
  }

  // Proves Workers routes take precedence over the Pages site on this hostname.
  // Deleting this route once verified is safe — see DEPLOY.md.
  if (method === "GET" && path === "/spine-canary/ping") {
    return json({ served_by: "spine-worker" });
  }

  return notFound();
}

export default {
  async fetch(request: Request, env: Env): Promise<Response> {
    try {
      return await handle(request, env);
    } catch (err) {
      // No passThroughOnException: a swallowed error here looks like an auth
      // failure on-device and is close to undiagnosable from the reading side.
      console.error(
        JSON.stringify({
          msg: "unhandled_error",
          path: new URL(request.url).pathname,
          method: request.method,
          error: err instanceof Error ? err.message : String(err),
        }),
      );
      return json({ message: "Internal error" }, 500);
    }
  },

  async scheduled(_controller: ScheduledController, env: Env): Promise<void> {
    const summary = await runRollup(env);
    console.log(JSON.stringify({ msg: "rollup_complete", ...summary }));
  },
} satisfies ExportedHandler<Env>;
