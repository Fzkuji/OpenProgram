// Saved credentials are write-only from the browser: reading a key returns a
// masked preview over one fixed request, and a replacement is accepted only
// when it is a real new value, never the mask itself.
import assert from "node:assert/strict";
import { test } from "node:test";
import { installLoader } from "../_support/load.mjs";

installLoader();

const { api } = await import("../../lib/net/api.ts");
const { normalizeSecretReplacement } = await import("../../lib/net/auth/secret-replacement.ts");

test("getKey asks only for the masked preview, even when an old caller passes a reveal flag", async () => {
  const calls = [];
  const realFetch = globalThis.fetch;
  globalThis.fetch = async (url, init) => {
    calls.push({ url, init });
    return { ok: true, status: 200, text: async () => JSON.stringify({ has_value: true, masked: "sk-…abc4" }) };
  };
  try {
    assert.deepEqual(await api.getKey("OPENAI_API_KEY", true), { has_value: true, masked: "sk-…abc4" });
    assert.deepEqual(calls, [{ url: "/api/config/key/OPENAI_API_KEY", init: { headers: { "Content-Type": "application/json" } } }]);
  } finally {
    globalThis.fetch = realFetch;
  }
});

test("a replacement secret is trimmed, and the mask, blanks, multi-line and non-ASCII values are refused", () => {
  assert.equal(normalizeSecretReplacement("  sk-new-secret  ", "sk-…abc4"), "sk-new-secret");
  assert.equal(normalizeSecretReplacement("sk-…abc4", "sk-…abc4"), null);
  assert.equal(normalizeSecretReplacement("••••••••", "••••••••"), null);
  assert.equal(normalizeSecretReplacement(""), null);
  assert.equal(normalizeSecretReplacement("secret\nsecond-line"), null);
  assert.equal(normalizeSecretReplacement("密钥"), null);
});
