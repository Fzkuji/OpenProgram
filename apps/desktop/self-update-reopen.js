"use strict";

const FLAG = "--openprogram-self-update=";
const REOPEN_PROTOCOL = 1;
const UPDATE_ID = /^su_[A-Za-z0-9][A-Za-z0-9_-]{0,63}$/;
const SESSION_ID = /^[A-Za-z0-9_-]{1,256}$/;
const MAX_BYTES = 8192;
const TIMEOUT_MS = 5000;
const REASONS = new Set([
  "owner_mismatch", "update_missing", "temporarily_unavailable", "intent_missing",
  "intent_invalid", "intent_expired", "activation_not_started", "session_missing",
  "origin_missing", "ack_invalid", "ack_identity_mismatch", "state_invalid",
]);

function launchUpdateId(argv) {
  const args = argv.filter((arg) => arg.startsWith("--openprogram-self-update"));
  if (!args.length) return null;
  if (args.length !== 1 || !args[0].startsWith(FLAG) || !UPDATE_ID.test(args[0].slice(FLAG.length))) {
    throw new Error("launch_argument_invalid");
  }
  return args[0].slice(FLAG.length);
}

async function requestJson(fetchImpl, url, token, body, signal) {
  const response = await fetchImpl(url, {
    method: body ? "POST" : "GET", redirect: "error", credentials: "omit",
    headers: { Authorization: `Bearer ${token}`, ...(body ? { "Content-Type": "application/json" } : {}) },
    ...(body ? { body: JSON.stringify(body) } : {}), signal,
  });
  if (response.status === 401 || response.status === 403) throw new Error("owner_mismatch");
  const reader = response.body?.getReader();
  if (!reader) throw new Error("response_invalid");
  const chunks = [];
  let size = 0;
  try {
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      size += value.byteLength;
      if (size > MAX_BYTES) throw new Error("response_invalid");
      chunks.push(Buffer.from(value));
    }
  } finally {
    try { await reader.cancel(); } catch { /* the request may already be aborted */ }
    reader.releaseLock();
  }
  let value;
  try { value = JSON.parse(Buffer.concat(chunks).toString("utf8")); }
  catch { throw new Error("response_invalid"); }
  if (!response.ok) throw new Error(REASONS.has(value?.reason) ? value.reason : "recovery_unavailable");
  return value;
}

function createSelfUpdateReopen({ argv, origin, fetchImpl = globalThis.fetch }) {
  let updateId = null;
  let reason = null;
  try { updateId = launchUpdateId(argv); } catch (error) { reason = error.message; }
  let intent = null;
  let token = null;
  let routed = false;
  let abandoned = false;
  let resolvePending = null;
  let ackPending = null;
  let activeRequest = null;

  function state() {
    return {
      updateId, sessionId: intent?.session_id ?? null,
      status: abandoned ? "manual_navigation" : reason ? "unavailable" : intent?.status ?? "inactive",
      reason,
    };
  }
  function publish(ctx) {
    if (!ctx.win.isDestroyed()) ctx.win.webContents.send?.("self-update:reopen-state", state());
  }
  function currentPath(ctx) {
    try {
      const url = new URL(ctx.win.webContents.getURL());
      return url.origin === origin ? url.pathname : null;
    } catch { return null; }
  }
  function validResponse(value) {
    return value && Object.keys(value).sort().join() ===
      "attempt,expires_at,launch_kind,reopen_id,schema,session_id,status,update_id" &&
      value.schema === REOPEN_PROTOCOL && value.update_id === updateId &&
      Number.isInteger(value.attempt) && value.attempt >= 1 && value.attempt <= 3 &&
      typeof value.session_id === "string" && SESSION_ID.test(value.session_id) &&
      typeof value.reopen_id === "string" && /^[0-9a-f]{64}$/.test(value.reopen_id) &&
      ["activation", "rollback"].includes(value.launch_kind) &&
      ["pending", "acknowledged"].includes(value.status) &&
      typeof value.expires_at === "number" && Number.isFinite(value.expires_at) &&
      value.expires_at * 1000 > Date.now();
  }
  async function request(body) {
    const controller = new AbortController();
    activeRequest = controller;
    const timer = setTimeout(() => controller.abort(), TIMEOUT_MS);
    try {
      return await requestJson(fetchImpl,
        `${origin}/api/self-updates/${updateId}/desktop-reopen${body ? "/ack" : ""}`,
        token, body, controller.signal);
    } finally {
      clearTimeout(timer);
      controller.abort(); // Also release unread error/unauthorized bodies.
      if (activeRequest === controller) activeRequest = null;
    }
  }
  function failure(error) {
    reason = REASONS.has(error?.message) || ["response_invalid", "owner_auth_unavailable"].includes(error?.message)
      ? error.message : "recovery_unavailable";
  }

  async function resolveStartUrl(ctx, startUrl) {
    if (ctx.id !== "main" || !updateId || abandoned || intent?.status === "acknowledged") return startUrl;
    // Retry after the existing worker error page recovers, but never resolve
    // against file/data/remote or alternate-profile bootstrap URLs.
    let bootstrap;
    try {
      bootstrap = new URL(startUrl);
      if (bootstrap.protocol === "data:") return startUrl;
      const expected = new URL(origin);
      if (bootstrap.origin !== origin || expected.protocol !== "http:" || expected.port !== "18100" ||
          !["127.0.0.1", "localhost", "[::1]"].includes(expected.hostname) ||
          bootstrap.username || bootstrap.password || !/^#token=[A-Za-z0-9_-]{43}$/.test(bootstrap.hash)) {
        throw new Error("owner_auth_unavailable");
      }
      token = bootstrap.hash.slice("#token=".length);
      if (!intent) {
        resolvePending ??= request().then((value) => {
          if (!validResponse(value)) throw new Error("response_invalid");
          intent = value;
        }).finally(() => { resolvePending = null; });
        await resolvePending;
      }
      if (!validResponse(intent)) throw new Error("intent_expired");
      reason = null;
      if (!abandoned && intent.status === "pending") {
        bootstrap.pathname = `/s/${intent.session_id}`;
        bootstrap.search = "";
        routed = true;
        return bootstrap.toString();
      }
    } catch (error) { failure(error); }
    finally { publish(ctx); }
    return startUrl;
  }

  function observeNavigation(ctx, url) {
    if (ctx.id !== "main" || !routed || !intent || abandoned) return;
    try {
      const parsed = new URL(url);
      if (parsed.origin === origin && parsed.pathname !== `/s/${intent.session_id}`) {
        abandoned = true;
        activeRequest?.abort();
        publish(ctx);
      }
    } catch { /* The worker error page is not a user navigation. */ }
  }

  async function sessionLoaded(ctx, sessionId) {
    if (ctx.id !== "main" || !intent || !routed || abandoned || sessionId !== intent.session_id ||
        currentPath(ctx) !== `/s/${intent.session_id}` || intent.status === "acknowledged") return state();
    ackPending ??= (async () => {
      for (let attempt = 0; attempt < 3; attempt++) {
        if (ctx.win.isDestroyed() || abandoned || currentPath(ctx) !== `/s/${intent.session_id}`) break;
        try {
          const value = await request({ session_id: sessionId, reopen_id: intent.reopen_id });
          if (!validResponse(value) || value.reopen_id !== intent.reopen_id || value.session_id !== sessionId ||
              value.attempt !== intent.attempt || value.launch_kind !== intent.launch_kind || value.status !== "acknowledged") {
            throw new Error("response_invalid");
          }
          if (!abandoned) { intent = value; reason = null; }
          break;
        } catch (error) {
          failure(error);
          if (reason !== "recovery_unavailable" && reason !== "temporarily_unavailable") break;
          if (attempt < 2) await new Promise((resolve) => setTimeout(resolve, 1000));
        }
      }
      publish(ctx);
    })().finally(() => { ackPending = null; });
    await ackPending;
    return state();
  }
  return { state, resolveStartUrl, observeNavigation, sessionLoaded };
}

function registerReopenIpc({ ipcMain, windows, recovery, origin }) {
  function mainContext(event) {
    const ctx = windows.get("main");
    if (!ctx || ctx.win.isDestroyed() || event.sender !== ctx.win.webContents ||
        !event.senderFrame || event.senderFrame !== event.sender.mainFrame) return null;
    try {
      if (new URL(event.senderFrame.url).origin !== origin) return null;
    } catch { return null; }
    return ctx;
  }
  ipcMain.handle("self-update:reopen-state", (event) => mainContext(event) ? recovery.state() : null);
  ipcMain.handle("self-update:session-loaded", (event, sessionId) => {
    const ctx = mainContext(event);
    return ctx && typeof sessionId === "string" && SESSION_ID.test(sessionId)
      ? recovery.sessionLoaded(ctx, sessionId) : null;
  });
}

module.exports = { REOPEN_PROTOCOL, createSelfUpdateReopen, launchUpdateId, registerReopenIpc };
