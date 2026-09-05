import assert from "node:assert/strict";
import { existsSync, readFileSync } from "node:fs";
import { registerHooks } from "node:module";
import test from "node:test";
import { fileURLToPath } from "node:url";
import ts from "typescript";
import { parseHTML } from "linkedom";

const webRoot = new URL("../", import.meta.url);
registerHooks({
  resolve(specifier, context, nextResolve) {
    if (specifier.endsWith(".module.css")) {
      return { url: "data:text/javascript,export default {}", shortCircuit: true };
    }
    const base = specifier.startsWith("@/")
      ? new URL(specifier.slice(2), webRoot).href
      : specifier.startsWith(".") && !/\.[a-z]+$/i.test(specifier)
        ? new URL(specifier, context.parentURL).href : null;
    if (base) {
      for (const suffix of [".ts", ".tsx", "/index.ts", "/index.tsx"]) {
        if (existsSync(fileURLToPath(base + suffix))) {
          return { url: base + suffix, shortCircuit: true };
        }
      }
    }
    return nextResolve(specifier, context);
  },
  load(url, context, nextLoad) {
    if (url.endsWith(".tsx")) {
      return {
        format: "module", shortCircuit: true,
        source: ts.transpileModule(readFileSync(fileURLToPath(url), "utf8"), {
          compilerOptions: { jsx: ts.JsxEmit.ReactJSX, module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 },
        }).outputText,
      };
    }
    return nextLoad(url, context);
  },
});
const { window } = parseHTML("<!doctype html><html><body></body></html>");
globalThis.window = window;
globalThis.document = window.document;
globalThis.Event = window.Event;
globalThis.CustomEvent = window.CustomEvent;
// Text assertions select a browser preference, independent of the host OS.
globalThis.localStorage = { getItem(key) { return key === "agentic_locale" ? "en" : null; }, setItem() {}, removeItem() {} };
globalThis.IS_REACT_ACT_ENVIRONMENT = true;
window.location = { pathname: "/chat", hash: "", search: "" };
window.history = { replaceState() {}, pushState() {} };
window.matchMedia = () => ({ matches: false, addEventListener() {}, removeEventListener() {} });
let respond;
globalThis.fetch = (...args) => respond(...args);
const { act, createElement } = await import("react");
const { createRoot } = await import("react-dom/client");
const { RunningPanel } = await import("../components/right-sidebar/running-panel.tsx");
const { SelfUpdateCard, SelfUpdateHistory } = await import("../components/chat/messages/self-update-card.tsx");
const { SelfUpdateReopenNotice } = await import("../components/self-update-reopen-notice.tsx");
const { useSessionStore } = await import("../lib/session-store/index.ts");

const update = {
  update_id: "update-one", session_id: "session-one", root_id: "update-one", parent_id: null,
  origin_assistant_id: "assistant-one", phase: "verifying", attempt: 1,
  state_revision: 5, snapshot_id: "snapshot-one", created_at: 100, updated_at: 200,
  candidate_revision: "a".repeat(40), changed_paths: ["example.py"], target_app: "/Applications/OpenProgram.app",
  last_verified_runtime: null, rollback_available: true, verifier_verdict: null,
  verifier: null, diagnosis: null, source_repair_result: null, iteration: null,
};
function response(payload, status = 200) {
  return { ok: status === 200, status, async json() { return payload; }, async text() { return JSON.stringify(payload); } };
}
async function mount(Component, props, check) {
  const host = document.createElement("div");
  document.body.append(host);
  const root = createRoot(host);
  try {
    await act(async () => { root.render(createElement(Component, props)); });
    await check(host, root);
  } finally {
    await act(async () => root.unmount());
    host.remove();
  }
}
test("reopen notice preserves a safe link without navigating and can be dismissed", async () => {
  let listener;
  let stopped = false;
  window.openprogramDesktop = { windowId: "main", selfUpdateReopen: {
    getState: async () => ({ status: "manual_navigation", sessionId: "p1", updateId: "su_test", reason: null }),
    onState: (fn) => { listener = fn; return () => { stopped = true; }; },
  } };
  try {
    await mount(SelfUpdateReopenNotice, {}, async (host) => {
      assert.match(host.textContent, /You changed pages/);
      assert.equal(host.querySelector("a").getAttribute("href"), "/s/p1");
      assert.equal(window.location.pathname, "/chat");
      await act(async () => listener({ status: "unavailable", reason: "session_missing", sessionId: null }));
      assert.match(host.textContent, /no longer exists/);
      assert.equal(host.querySelector("a"), null);
      await act(async () => host.querySelector("button").click());
      assert.equal(host.textContent, "");
    });
    assert.equal(stopped, true);
  } finally { delete window.openprogramDesktop; }
});

test("reopen notice ignores a stale initial reply after a live state event", async () => {
  let reply;
  let listener;
  window.openprogramDesktop = { windowId: "main", selfUpdateReopen: {
    getState: () => new Promise((resolve) => { reply = resolve; }),
    onState: (fn) => { listener = fn; return () => {}; },
  } };
  try {
    await mount(SelfUpdateReopenNotice, {}, async (host) => {
      await act(async () => listener({ status: "acknowledged", sessionId: "p1" }));
      await act(async () => reply({ status: "pending", sessionId: "p1" }));
      assert.match(host.textContent, /Update verification is separate/);
      await act(async () => listener({ status: "unavailable", sessionId: "https://example.com", reason: "PRIVATE_TOKEN" }));
      assert.equal(host.querySelector("a"), null);
      assert.doesNotMatch(host.textContent, /PRIVATE_TOKEN/);
    });
  } finally { delete window.openprogramDesktop; }
});

function fact(host, label) {
  const term = [...host.querySelectorAll("dt")].find((node) => node.textContent === label);
  assert.ok(term, `missing update fact: ${label}`);
  assert.equal(term.nextElementSibling?.tagName, "DD");
  return term.nextElementSibling;
}

test("Running renders the target separately from unknown verified runtime", async () => {
  respond = async () => response({ now: 210, items: [{
    kind: "self_update", id: update.update_id, session_id: update.session_id,
    label: "Self-update", status: "verifying", started_at: 100, update,
  }] });
  await mount(RunningPanel, { active: true }, async (host) => {
    assert.match(host.textContent, /Target revision/);
    assert.equal(fact(host, "Verified runtime").textContent, "Unknown");
    assert.match(host.textContent, /Verifying/);
    assert.ok(host.textContent.includes(update.candidate_revision));
    assert.doesNotMatch(host.textContent, /Process/);
  });
});

test("persisted history groups root attempts and loads a stable older cursor", async () => {
  const calls = [];
  const second = { ...update, update_id: "update-two", attempt: 2, created_at: 300, phase: "rolled_back", snapshot_id: "two" };
  respond = async (url) => {
    calls.push(String(url));
    return response(String(url).includes("cursor=")
      ? { items: [update], next_cursor: null }
      : { items: [second], next_cursor: "opaque+/=" });
  };
  await mount(SelfUpdateHistory, { sessionId: update.session_id }, async (host) => {
    assert.match(host.textContent, /Rolled back/);
    const more = [...host.querySelectorAll("button")].find((button) => button.textContent === "Load older updates");
    await act(async () => more.click());
    assert.equal(host.querySelectorAll("[data-update-root]").length, 1);
    assert.deepEqual([...host.querySelectorAll("article")].map((article) => article.getAttribute("data-update-id")), [update.update_id, second.update_id]);
    assert.equal(new URL(calls[1], "http://localhost").searchParams.get("cursor"), "opaque+/=");
    assert.doesNotMatch(host.textContent, /Load older updates/);
  });
});

test("session changes abort requests and discard late replies even if fetch ignores abort", async () => {
  let finishOld;
  let oldSignal;
  respond = async (url, init) => {
    if (String(url).includes("session-one")) {
      oldSignal = init.signal;
      return new Promise((resolve) => { finishOld = resolve; });
    }
    return response({ items: [{ ...update, update_id: "other-update", session_id: "session-two", candidate_revision: "b".repeat(40) }], next_cursor: null });
  };
  await mount(SelfUpdateHistory, { sessionId: "session-one" }, async (host, root) => {
    await act(async () => root.render(createElement(SelfUpdateHistory, { sessionId: "session-two" })));
    assert.equal(oldSignal.aborted, true);
    assert.ok(host.textContent.includes("b".repeat(40)));
    await act(async () => finishOld(response({ items: [update], next_cursor: null })));
    assert.ok(!host.textContent.includes(update.candidate_revision));
    assert.ok(host.textContent.includes("b".repeat(40)));
  });
});

test("history keeps stale evidence offline and accepts a new snapshot at the same state revision", async () => {
  let fail = false;
  let item = update;
  respond = async () => {
    if (fail) throw new Error("offline");
    return response({ items: [item], next_cursor: null });
  };
  await mount(SelfUpdateHistory, { sessionId: update.session_id }, async (host) => {
    fail = true;
    await act(async () => window.dispatchEvent(new Event("online")));
    assert.match(host.textContent, /may be stale/);
    assert.match(host.textContent, /Last sync/);
    assert.ok(host.textContent.includes(update.candidate_revision));
    fail = false;
    item = { ...update, snapshot_id: "late", diagnosis: { status: "completed", at: 400 } };
    await act(async () => window.dispatchEvent(new Event("online")));
    assert.equal(fact(host, "Diagnosis").textContent, "completed");
    assert.doesNotMatch(host.textContent, /may be stale/);
  });
});

test("history and Running never overlap slow polls and abort them on unmount", async (t) => {
  t.mock.timers.enable({ apis: ["setTimeout"] });
  for (const Component of [SelfUpdateHistory, RunningPanel]) {
    let requests = 0;
    let signal;
    respond = async (_url, init) => {
      requests++;
      signal = init.signal;
      return new Promise(() => {});
    };
    await mount(Component, { sessionId: update.session_id, active: true }, async () => {
      await act(async () => {
        t.mock.timers.tick(3100);
        window.dispatchEvent(new Event("online"));
        window.dispatchEvent(new Event("online"));
      });
      assert.equal(requests, 1);
    });
    assert.equal(signal.aborted, true);
  }
});

test("initial failures are not displayed as empty history or Nothing running", async () => {
  respond = async () => response({ error: "denied" }, 403);
  for (const Component of [SelfUpdateHistory, RunningPanel]) {
    await mount(Component, { sessionId: update.session_id, active: true }, async (host) => {
      assert.match(host.textContent, /unavailable/);
      assert.doesNotMatch(host.textContent, /Nothing is running|Loading/);
    });
  }
});

test("evidence stays literal text and requests only the projected authorized evidence ID", async () => {
  const calls = [];
  const verified = { ...update, verifier_verdict: "pass", verifier: {
    verdict: "pass", evidence_id: "aggregate:one", assertions: [{ id: "title", status: "pass", evidence_refs: ["obs:one"] }],
  } };
  respond = async (url, init) => {
    calls.push({ url, init });
    return response({ observations: [{ body: "<script>alert(1)</script><b>unsafe</b>" }] });
  };
  await mount(SelfUpdateCard, { update: verified }, async (host) => {
    const summary = host.querySelector("summary");
    assert.equal(summary.parentElement.tagName, "DETAILS");
    await act(async () => host.querySelector("button").click());
    assert.equal(calls.length, 1);
    const query = new URL(calls[0].url, "http://localhost").searchParams;
    assert.equal(query.get("session_id"), update.session_id);
    assert.equal(query.get("evidence_id"), verified.verifier.evidence_id);
    assert.equal(calls[0].init.cache, "no-store");
    assert.match(host.querySelector("pre").textContent, /<script>/);
    assert.equal(host.querySelector("script"), null);
    assert.equal(host.querySelector("b"), null);
  });
});

test("control preparation preserves the original draft and does not send, install, or approve", async () => {
  useSessionStore.getState().setComposerInputFor(update.session_id, "Existing draft");
  useSessionStore.getState().setComposerInputFor("other-session", "Other draft");
  let requests = 0;
  respond = async () => { requests++; throw new Error("must not submit"); };
  await mount(SelfUpdateCard, { update: { ...update, phase: "ready" } }, async (host) => {
    const button = [...host.querySelectorAll("button")].find((item) => item.textContent === "Prepare cancellation request");
    await act(async () => button.click());
    const drafts = useSessionStore.getState().composerDrafts;
    assert.ok(drafts[update.session_id].startsWith("Existing draft\n\n"));
    assert.ok(drafts[update.session_id].includes('self_update_cancel(update_id="update-one")'));
    assert.equal(drafts["other-session"], "Other draft");
    assert.equal(requests, 0);
    assert.match(host.textContent, /not submitted/);
  });
});

test("manual recovery is explicit and an old verified revision never becomes the target", async () => {
  const manual = { ...update, phase: "needs_manual_recovery", last_verified_runtime: {
    candidate_sha: "c".repeat(40), worker_pid: 1234, verified_at: 100, source: "owner_repair",
  } };
  await mount(SelfUpdateCard, { update: manual }, async (host) => {
    assert.match(host.textContent, /Manual recovery required/);
    assert.equal(fact(host, "Worker PID").textContent, "1234");
    const expected = [update.candidate_revision, manual.last_verified_runtime.candidate_sha];
    const versions = ["Target revision", "Verified runtime"].map((label) => fact(host, label).querySelector("code"));
    assert.deepEqual(versions.map((node) => node.textContent), expected.map((sha) => sha.slice(0, 8)));
    assert.deepEqual(versions.map((node) => node.title), expected);
    assert.deepEqual(["Full revision", "Runtime revision"].map((label) => fact(host, label).textContent), expected);
    assert.doesNotMatch(host.textContent, /Update committed/);
  });
});

test("Running retains the prior update on a partial self-update projection error", async () => {
  let partial = false;
  respond = async () => response(partial ? { now: 220, items: [], self_update_error: "unavailable" } : {
    now: 210, items: [{ kind: "self_update", id: update.update_id, session_id: update.session_id, update }],
  });
  await mount(RunningPanel, { active: true }, async (host) => {
    partial = true;
    await act(async () => window.dispatchEvent(new Event("online")));
    assert.match(host.textContent, /may be stale/);
    assert.ok(host.textContent.includes(update.candidate_revision));
    assert.doesNotMatch(host.textContent, /Nothing is running/);
  });
});

test("repair and child identities stay distinct from the failed original target", async () => {
  const repaired = "d".repeat(40);
  const child = "submitted-child-two";
  const item = { ...update, phase: "rolled_back", verifier_verdict: "fail",
    source_repair_result: { status: "candidate_ready", at: 300, candidate_sha: repaired },
    iteration: { root_id: update.root_id, parent_id: null, attempt: 1, max_attempts: 3,
      deadline: 9999999999, stopped: false, submission: { status: "submitted", child_id: child, at: 310 } },
  };
  await mount(SelfUpdateCard, { update: item }, async (host, root) => {
    assert.ok(host.textContent.includes(repaired));
    assert.ok(host.textContent.includes(child));
    assert.ok(host.textContent.includes(update.candidate_revision));
    assert.equal(fact(host, "Verification").textContent, "fail");
    assert.equal(fact(host, "Repaired revision").textContent, repaired);
    assert.equal(fact(host, "Next update ID").textContent, child);
    assert.match(host.textContent, /Rolled back/);
    assert.doesNotMatch(host.textContent, /Update committed/);
    await act(async () => root.render(createElement(SelfUpdateCard, { update: {
      ...item, source_repair_result: { status: "cancelled", at: 320 },
      iteration: { ...item.iteration, submission: { status: "failed", child_id: null, at: 320 } },
    } })));
    assert.ok(!host.textContent.includes(repaired));
    assert.ok(!host.textContent.includes(child));
    assert.doesNotMatch(host.textContent, /Repaired revision|Next update ID/);
  });
});
