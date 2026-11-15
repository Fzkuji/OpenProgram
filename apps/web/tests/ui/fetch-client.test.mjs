import assert from "node:assert/strict";
import { afterEach, describe, test } from "node:test";

// Loaded without a window, as the module is in a Node test: no owner token to
// exchange, so requests go straight to fetch.
const { jsonFetch, HttpError } = await import("../../lib/net/fetch-client.ts");

const realFetch = globalThis.fetch;
let calls = [];
function serve(...responses) {
  calls = [];
  globalThis.fetch = async (url, init) => {
    calls.push({ url, init });
    const next = responses.shift();
    return typeof next === "function" ? next(url, init) : next;
  };
}
const json = (body, status = 200) => new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });

afterEach(() => {
  globalThis.fetch = realFetch;
  delete globalThis.window;
});

describe("jsonFetch", () => {
  test("returns the parsed body and sends JSON headers merged with the caller's", async () => {
    serve(json({ ok: 1 }));
    const body = await jsonFetch("/api/x", { method: "POST", headers: { "X-Trace": "t" }, body: "{}" });
    assert.deepEqual(body, { ok: 1 });
    assert.equal(calls[0].url, "/api/x");
    assert.equal(calls[0].init.method, "POST");
    assert.deepEqual(calls[0].init.headers, { "Content-Type": "application/json", "X-Trace": "t" });
  });

  test("an empty 2xx body reads as an empty object", async () => {
    serve(new Response("", { status: 200 }));
    assert.deepEqual(await jsonFetch("/api/x"), {});
  });

  test("a FastAPI detail becomes the error message, with status and body kept", async () => {
    serve(json({ detail: "server did not start" }, 502));
    await assert.rejects(jsonFetch("/api/mcp/servers/broken/restart", { method: "POST" }), (error) => {
      assert.ok(error instanceof HttpError);
      assert.equal(error.name, "HttpError");
      assert.equal(error.status, 502);
      assert.equal(error.message, "server did not start");
      assert.equal(error.body, JSON.stringify({ detail: "server did not start" }));
      return true;
    });
  });

  test("an `error` field wins over `detail` and its `code` is kept", async () => {
    serve(json({ error: "name taken", detail: "ignored", code: "CONFLICT" }, 409));
    await assert.rejects(jsonFetch("/api/x"), { status: 409, message: "name taken", code: "CONFLICT" });
  });

  test("a plain-text error body is the message; a JSON body without a message is quoted after the status", async () => {
    serve(new Response("Bad Gateway", { status: 502 }));
    await assert.rejects(jsonFetch("/api/x"), { status: 502, message: "Bad Gateway" });

    const unhelpful = JSON.stringify({ trace: "x".repeat(400) });
    serve(new Response(unhelpful, { status: 500 }));
    await assert.rejects(jsonFetch("/api/x"), (error) => {
      assert.equal(error.message, `HTTP 500: ${unhelpful.slice(0, 300)}`);
      assert.equal(error.code, undefined);
      return true;
    });

    serve(new Response("", { status: 500 }));
    await assert.rejects(jsonFetch("/api/x"), { message: "HTTP 500" });
  });

  test("a 401 in the desktop app refreshes the owner credential and retries once", async () => {
    let refreshes = 0;
    globalThis.window = { openprogramDesktop: { refreshOwnerAuth: async () => { refreshes += 1; return "t".repeat(43); } } };
    serve(json({ error: "unauthorized" }, 401), json({ ok: true }));
    assert.deepEqual(await jsonFetch("/api/x"), { ok: true });
    assert.equal(refreshes, 1);
    assert.equal(calls.length, 2);

    serve(json({ error: "unauthorized" }, 401), json({ error: "still unauthorized" }, 401));
    await assert.rejects(jsonFetch("/api/x"), { status: 401, message: "still unauthorized" });
    assert.equal(calls.length, 2, "the retry is not retried again");
  });

  test("a 401 outside the desktop app is reported without a retry", async () => {
    serve(json({ error: "unauthorized" }, 401));
    await assert.rejects(jsonFetch("/api/x"), { status: 401 });
    assert.equal(calls.length, 1);
  });
});
