import { SELF } from "cloudflare:test";
import { beforeEach, describe, expect, it } from "vitest";
import { authHeaders, authorize, pull, push, register, KEY, USER } from "./helpers";

/**
 * The kosync contract, verified against the shapes KOReader actually branches on.
 *
 * KOSyncClient.lua reports register success as `res.status == 201` and auth as
 * `== 200`, and Spore raises a Lua error for any status outside api.json's
 * expected list — which the user sees as "an error occurred" with no message.
 * So the codes here are the API, not decoration.
 */

describe("POST /users/create", () => {
  it("returns 201 and the username on the first account", async () => {
    const res = await register();
    expect(res.status).toBe(201);
    expect(await res.json()).toEqual({ username: USER });
  });

  it("closes itself after the first account, with a readable 402", async () => {
    await register();
    const res = await register("someone-else", "ffffffffffffffffffffffffffffffff");

    // 402 rather than 403: 403 is outside api.json's expected_status and would
    // surface on-device as an unexplained error instead of this message.
    expect(res.status).toBe(402);
    expect(await res.json<{ message: string }>()).toHaveProperty("message");
  });

  it("reports a duplicate username as 402", async () => {
    await register();
    const res = await register(USER, "ffffffffffffffffffffffffffffffff");
    expect(res.status).toBe(402);
  });

  it("rejects a body with no username or password", async () => {
    const res = await SELF.fetch("https://spine.test/users/create", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ username: "" }),
    });
    expect(res.status).toBe(400);
  });
});

describe("GET /users/auth", () => {
  beforeEach(async () => {
    await register();
  });

  it("returns 200 {authorized: OK} for valid credentials", async () => {
    const res = await authorize();
    expect(res.status).toBe(200);
    expect(await res.json()).toEqual({ authorized: "OK" });
  });

  it("returns 401 for a wrong key", async () => {
    const res = await authorize(authHeaders(USER, "00000000000000000000000000000000"));
    expect(res.status).toBe(401);
    expect(await res.json<{ message: string }>()).toHaveProperty("message");
  });

  it("returns 401 for an unknown user", async () => {
    expect((await authorize(authHeaders("nobody", KEY))).status).toBe(401);
  });

  it("returns 401 when the auth headers are missing entirely", async () => {
    const res = await SELF.fetch("https://spine.test/users/auth");
    expect(res.status).toBe(401);
  });
});

describe("authentication is required for sync routes", () => {
  beforeEach(async () => {
    await register();
  });

  it("rejects an unauthenticated push", async () => {
    const res = await push(
      { document: "abc", progress: "10", percentage: 0.1, device: "x", device_id: "x1" },
      { accept: "application/vnd.koreader.v1+json" },
    );
    expect(res.status).toBe(401);
  });

  it("rejects an unauthenticated pull", async () => {
    const res = await pull("abc", { accept: "application/vnd.koreader.v1+json" });
    expect(res.status).toBe(401);
  });

  it("rejects a push with a valid user but a wrong key", async () => {
    const res = await push(
      { document: "abc", progress: "10", percentage: 0.1, device: "x", device_id: "x1" },
      authHeaders(USER, "deadbeefdeadbeefdeadbeefdeadbeef"),
    );
    expect(res.status).toBe(401);
  });
});

describe("misc routes", () => {
  it("answers the healthcheck", async () => {
    const res = await SELF.fetch("https://spine.test/api/healthcheck");
    expect(res.status).toBe(200);
    expect(await res.json()).toEqual({ state: "OK" });
  });

  it("answers the route-precedence canary", async () => {
    const res = await SELF.fetch("https://spine.test/spine-canary/ping");
    expect(res.status).toBe(200);
    expect(await res.json()).toEqual({ served_by: "spine-worker" });
  });

  it("404s an unknown path", async () => {
    expect((await SELF.fetch("https://spine.test/nope")).status).toBe(404);
  });
});
