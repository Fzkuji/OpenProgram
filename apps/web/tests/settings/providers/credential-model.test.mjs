// What a credential says on screen: its status badge, the word the model
// menu shows after it, whether a request could use it now, the line that
// says why it stopped, today's usage, a provider card's count, and which
// credential a card's fix opens.
import assert from "node:assert/strict";
import { test } from "node:test";
import { installLoader } from "../../_support/load.mjs";
import { credential } from "../../_support/provider-fixtures.mjs";

installLoader({});
const M = await import("../../../components/settings/providers/detail/credential-model.ts");

const en = (english) => english;
const NOW = 1_800_000_000;
const later = NOW + 60;

test("the status badge names each state with its tone", () => {
  const badge = (fields) => M.credentialBadge(credential("a1b2", fields), NOW, en);
  assert.deepEqual(badge({}), { tone: "success", label: "Ready" });
  assert.equal(badge({ status: "cooling", cooldown_until: later }).tone, "warning");
  assert.match(badge({ status: "cooling", cooldown_until: later }).label, /^Back at \d\d:\d\d$/);
  assert.deepEqual(badge({ status: "cooling", cooldown_until: NOW - 1 }), { tone: "success", label: "Ready" }, "a wait that is over reads Ready");
  assert.deepEqual(badge({ status: "cooling", cooldown_until: null }), { tone: "warning", label: "Rate limited" });
  assert.deepEqual(badge({ status: "quota" }), { tone: "destructive", label: "Out of quota" });
  assert.deepEqual(badge({ status: "invalid_key" }), { tone: "destructive", label: "Invalid key" });
  assert.deepEqual(badge({ status: "needs_login" }), { tone: "warning", label: "Sign in again" });
  assert.deepEqual(badge({ enabled: false, status: "invalid_key" }), { tone: "secondary", label: "Not in use" }, "set aside wins over the state");
});

test("the model menu's word is the masked tail when ready and the problem otherwise", () => {
  const word = (fields) => M.credentialWord(credential("a1b2", fields), NOW, en);
  assert.equal(word({ key_tail: "a1b2" }), "····a1b2");
  assert.equal(word({ status: "invalid_key" }), "invalid");
  assert.match(word({ status: "cooling", cooldown_until: later }), /^back \d\d:\d\d$/);
  assert.equal(word({ status: "cooling", cooldown_until: NOW - 1, key_tail: "a1b2" }), "····a1b2", "a wait that is over reads as ready");
  assert.equal(word({ enabled: false }), "not in use");
});

test("a credential is usable when ready or once its wait is over", () => {
  const usable = (fields) => M.usableNow(credential("a1b2", fields), NOW);
  assert.equal(usable({}), true);
  assert.equal(usable({ status: "cooling", cooldown_until: later }), false);
  assert.equal(usable({ status: "cooling", cooldown_until: NOW }), true);
  assert.equal(usable({ status: "quota", cooldown_until: null }), false);
  assert.equal(usable({ status: "invalid_key" }), false);
  assert.equal(usable({ enabled: false }), false);
});

test("the failure line names the cause with the HTTP status", () => {
  const line = (fields) => M.failureLine(credential("a1b2", fields), "OpenAI", en, NOW);
  assert.equal(line({}), "");
  assert.equal(line({ status: "invalid_key", last_error: { message: "HTTP 401 Unauthorized" } }), "Rejected by OpenAI (401)");
  assert.match(line({ status: "cooling", last_error: { code: "429", at: NOW } }), /^Rate limited \(429\) at \d\d:\d\d$/);
  assert.equal(line({ status: "needs_login", last_error: { message: "expired" } }), "The sign-in expired");
  assert.equal(line({ status: "invalid_key", last_error: null }), "", "no error, no line");
  assert.equal(line({ status: "cooling", cooldown_until: NOW - 1, last_error: { code: "429", at: NOW - 30 } }), "", "a wait that is over says nothing");
});

test("today's usage gives cost and requests for a key, requests only for a subscription", () => {
  const today = (calls, cost_usd, subscription = false) => M.todayLine(credential("a1b2", { today: { calls, cost_usd } }), subscription, en);
  assert.deepEqual(today(0, 0), []);
  assert.deepEqual(today(412, 3.12), ["$3.12 today", "412 requests"]);
  assert.deepEqual(today(1, 0.5, true), ["1 request today"]);
});

test("a card's count names the failing parts, and one key what tells it apart", () => {
  const key = (status, fields = {}) => ({ credential_id: status, kind: "api_key", label: "", status, cooldown_until: null, ...fields });
  const account = (fields = {}) => key("ready", { kind: "oauth", ...fields });
  assert.deepEqual(M.credentialCount([], true, NOW, en), [{ label: "0 keys" }], "a provider that takes keys counts keys");
  assert.deepEqual(M.credentialCount([], false, NOW, en), [{ label: "0 accounts" }], "a sign-in provider counts accounts");
  assert.deepEqual(M.credentialCount([key("ready", { key_tail: "a1b2" })], true, NOW, en), [{ label: "1 key" }, { label: "····a1b2" }]);
  assert.deepEqual(M.credentialCount([key("ready", { label: "Work", key_tail: "a1b2" })], true, NOW, en), [{ label: "1 key" }, { label: "Work" }]);
  assert.deepEqual(M.credentialCount([key("ready", { env_var: "OPENAI_API_KEY" })], true, NOW, en), [{ label: "1 key" }, { label: "$OPENAI_API_KEY" }]);
  assert.deepEqual(M.credentialCount([account({ email: "a@example.com" })], false, NOW, en), [{ label: "1 account" }, { label: "a@example.com" }]);
  assert.deepEqual(M.credentialCount([account({ signin_name: "Qwen" })], false, NOW, en), [{ label: "1 account" }, { label: "Qwen sign-in" }]);
  assert.deepEqual(M.credentialCount([account(), account()], true, NOW, en), [{ label: "2 accounts" }], "the rows decide, not the provider");
  assert.deepEqual(
    M.credentialCount([key("ready"), key("cooling", { cooldown_until: later }), key("invalid_key")], true, NOW, en),
    [{ label: "3 keys" }, { label: "1 cooling", tone: "warning" }, { label: "1 invalid", tone: "danger" }],
  );
});

test("a card counts the keys not in use, and their trouble is not counted", () => {
  const key = (status, fields = {}) => ({ credential_id: status, kind: "api_key", status, cooldown_until: null, enabled: true, ...fields });
  assert.deepEqual(
    M.credentialCount([key("ready"), key("invalid_key", { enabled: false })], true, NOW, en),
    [{ label: "2 keys" }, { label: "1 not in use" }],
  );
});

test("Add key or Add account follows what the provider takes when it has nothing yet", () => {
  assert.equal(M.countsKeys([], true), true);
  assert.equal(M.countsKeys([], false), false);
  assert.equal(M.countsKeys([{ kind: "api_key" }], false), true);
  assert.equal(M.countsKeys([{ kind: "oauth" }, { kind: "api_key" }], true), false);
});

test("a row is named by its label, then its email, then what it is", () => {
  const name = (fields) => M.credentialName(credential("a1b2", fields), en);
  assert.equal(name({ label: "Personal", identity: { email: "a@example.com" } }), "Personal");
  assert.equal(name({ identity: { email: "a@example.com" }, kind: "oauth" }), "a@example.com");
  assert.equal(name({ env_var: "OPENAI_API_KEY", source: "env" }), "Environment key");
  assert.equal(name({}), "API key");
  assert.equal(name({ kind: "oauth", signin_name: "Qwen" }), "Qwen sign-in");
  assert.equal(name({ kind: "oauth" }), "Sign-in");
  assert.equal(M.credentialName(credential("a1b2", { kind: "oauth", signin_name: "Qwen" }), (_e, zh) => zh), "Qwen 登录");
});

test("out of quota names a reset only for a limit with a window", () => {
  const badge = (limits) => M.credentialBadge(credential("a1b2", { status: "quota", cooldown_until: NOW + 3600, limits }), NOW, en).label;
  assert.equal(badge([]), "Out of quota", "a spent balance has no reset to name");
  assert.equal(badge([{ label: "Credits", window_minutes: 0 }]), "Out of quota");
  assert.match(badge([{ label: "Weekly", window_minutes: 10080 }]), /^Out of quota · .+/);
});

test("a provider not set up here reads so on its rows", () => {
  assert.deepEqual(M.credentialBadge(credential("a1b2", { status: "not_set_up" }), NOW, en), { tone: "secondary", label: "Provider not set up" });
});

test("an imported sign-in whose file is gone says so", () => {
  const row = credential("", { kind: "import", status: "no_file" });
  assert.deepEqual(M.credentialBadge(row, NOW, en), { tone: "warning", label: "Sign-in file not found" });
  assert.match(M.failureLine(row, "Gemini", en), /not there/);
});

test("the list's next change by itself is the soonest wait that runs out", () => {
  const rows = [
    credential("a", { status: "cooling", cooldown_until: NOW + 90 }),
    credential("b", { status: "quota", cooldown_until: NOW + 30 }),
    credential("c", { status: "cooling", cooldown_until: NOW + 5, enabled: false }),
    credential("d", { status: "cooling", cooldown_until: NOW - 5 }),
  ];
  assert.equal(M.nextExpiry(rows, NOW), NOW + 30);
  assert.equal(M.nextExpiry([credential("e")], NOW), null);
});

test("a card's fix opens the first credential that needs it", () => {
  const keys = [{ credential_id: "a", status: "ready" }, { credential_id: "b", status: "invalid_key" }, { credential_id: "c", status: "needs_login" }];
  assert.equal(M.failingCredential(keys, "replace_key"), "b");
  assert.equal(M.failingCredential(keys, "sign_in"), "c");
  assert.equal(M.failingCredential(keys, "top_up"), null);
});

test("ids are encoded into the credential URL, an environment one included", () => {
  assert.equal(M.credentialUrl("openai", "env:OPENAI_API_KEY"), "/api/providers/openai/credentials/env%3AOPENAI_API_KEY");
  assert.equal(M.strategyLabel("least_used", en), "Least used");
  assert.equal(M.isSignIn({ kind: "api_key" }), false);
  assert.equal(M.isSignIn({ kind: "oauth" }), true);
});
