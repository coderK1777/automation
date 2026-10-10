import { test, afterEach } from "node:test";
import assert from "node:assert/strict";
import { api, ApiError, setCsrfToken, format } from "../lib/api";
const originalFetch = globalThis.fetch;
afterEach(() => {
  globalThis.fetch = originalFetch;
  setCsrfToken(undefined);
});
test("writes carry csrf and same origin session without exposing credentials in URL", async () => {
  let calls = 0;
  globalThis.fetch = async (input, options) => {
    calls++;
    assert.equal(input, "/api/v1/strategies");
    assert.equal(options?.credentials, "same-origin");
    assert.equal(
      (options?.headers as Record<string, string>)["X-CSRF-Token"],
      "session-csrf",
    );
    assert.equal(options?.method, "POST");
    assert.equal(options?.body, JSON.stringify({ name: "test" }));
    return Response.json({ id: 1 });
  };
  setCsrfToken("session-csrf");
  assert.deepEqual(
    await api("/strategies", { method: "POST", body: { name: "test" } }),
    { id: 1 },
  );
  assert.equal(calls, 1);
});
test("a failed mutation is not automatically retried", async () => {
  let calls = 0;
  globalThis.fetch = async () => {
    calls++;
    throw new TypeError("connection lost");
  };
  await assert.rejects(
    api("/positions/1/close", { method: "POST", body: {} }),
    /connection lost/,
  );
  assert.equal(calls, 1);
});
test("authorization failures retain their status for session handling", async () => {
  globalThis.fetch = async () =>
    Response.json({ detail: "Authentication required" }, { status: 401 });
  await assert.rejects(
    api("/auth/me"),
    (error: unknown) =>
      error instanceof ApiError &&
      error.status === 401 &&
      error.message === "Authentication required",
  );
});
test("backend validation errors remain readable", async () => {
  globalThis.fetch = async () =>
    Response.json(
      {
        detail: [
          { loc: ["body", "quantity"], msg: "Quantity must be positive" },
        ],
      },
      { status: 422 },
    );
  await assert.rejects(
    api("/orders", { method: "POST", body: {} }),
    /Quantity must be positive/,
  );
});
test("unavailable balances and statistics remain absent, not zero", () => {
  assert.equal(format.money(null), "—");
  assert.equal(format.money(undefined), "—");
  assert.equal(format.number(Number.NaN), "—");
  assert.equal(format.money(0), "0.00 USD");
  assert.equal(format.money("10", "INR"), "10.00 INR");
});
