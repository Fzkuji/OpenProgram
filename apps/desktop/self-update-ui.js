"use strict";

const fs = require("node:fs");
const crypto = require("node:crypto");
const { runScroll } = require("./self-update-ui-scroll");
const { runTestObject } = require("./self-update-ui-test-object");

const APP = "/Applications/OpenProgram.app";
const NONCE = /^[0-9a-f]{64}$/;
const REASONS = new Set(["installation_unavailable", "invalid_capture_contract", "main_window_changed",
  "original_session_not_visible", "installation_changed", "renderer_unavailable", "debugger_in_use",
  "capture_interrupted", "target_unavailable", "accessibility_unavailable", "screenshot_unavailable",
  "capture_cleanup_failed", "capture_output_limit", "capture_not_accepted"]);

function installedIdentity(app) {
  const marker = `${APP}/Contents/Resources/openprogram-source-revision`;
  if (!app.isPackaged || app.getAppPath() !== `${APP}/Contents/Resources/app.asar` ||
      process.execPath !== `${APP}/Contents/MacOS/OpenProgram` ||
      fs.realpathSync(process.execPath) !== process.execPath || fs.realpathSync(marker) !== marker ||
      !fs.statSync(marker).isFile() || fs.statSync(marker).size > 80) throw new Error("installation_unavailable");
  const revision = fs.readFileSync(marker, "ascii").trim();
  if (!/^[0-9a-f]{40}$/.test(revision)) throw new Error("installation_unavailable");
  return { app_path: APP, app_pid: process.pid, candidate_sha: revision };
}

function validateContract(value, nonce) {
  const keys = ["schema", "nonce", "update_id", "attempt", "session_id", "candidate_sha",
    "worker_pid", "check_id", "deadline", "max_output_bytes", "action"];
  if (value && Object.hasOwn(value, "interaction")) {
    keys.push("interaction");
    const step = value.interaction;
    const scroll = step && Object.keys(step).sort().join() === "delta_y,kind" && step.kind === "scroll" &&
      Number.isInteger(step.delta_y) && step.delta_y !== 0 && Math.abs(step.delta_y) <= 1200;
    const view = step && Object.keys(step).sort().join() === "kind,target" && step.kind === "view" &&
      ["session", "dag"].includes(step.target);
    const fixture = step && Object.keys(step).sort().join() === "action,cleanup,initial_title,kind,object_id,title" &&
      step.kind === "test_object" && step.action === "rename" && step.cleanup === "restore-and-remove" &&
      typeof step.object_id === "string" && /^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$/.test(step.object_id) &&
      [step.initial_title, step.title].every(value => typeof value === "string" && value.length >= 1 &&
        [...value].length <= 120 && value.trim() === value && !/[\u0000-\u001f]/.test(value)) && step.initial_title !== step.title;
    if (!scroll && !view && !fixture) {
      throw new Error("invalid_capture_contract");
    }
  }
  if (!value || Object.keys(value).sort().join() !== keys.sort().join() || value.schema !== 1 || value.nonce !== nonce ||
      ["update_id", "session_id", "candidate_sha", "check_id"].some((key) => typeof value[key] !== "string") ||
      !/^su_[A-Za-z0-9][A-Za-z0-9_-]{0,63}$/.test(value.update_id) ||
      !/^[A-Za-z0-9_-]{1,256}$/.test(value.session_id) || !/^[0-9a-f]{40}$/.test(value.candidate_sha) ||
      !Number.isInteger(value.attempt) || value.attempt < 1 || value.attempt > 3 ||
      !Number.isInteger(value.worker_pid) || value.worker_pid <= 0 ||
      !/^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$/.test(value.check_id) || value.action !== "capture" ||
      !Number.isFinite(value.deadline) || value.deadline <= Date.now() / 1000 || value.deadline > Date.now() / 1000 + 60 ||
      !Number.isInteger(value.max_output_bytes) || value.max_output_bytes < 1 || value.max_output_bytes > 1572864) {
    throw new Error("invalid_capture_contract");
  }
}

function registerUiVerificationIpc({ ipcMain, windows, origin, app, request,
  guard, installation = () => installedIdentity(app) }) {
  let busy = false;
  ipcMain.handle("self-update:ui-capture", async (event, nonce) => {
    const ctx = windows.get("main");
    const wc = ctx?.win.webContents;
    if (busy || !ctx || ctx.win.isDestroyed() || !wc || wc.isDestroyed() ||
        event.sender !== wc || !event.senderFrame || event.senderFrame !== wc.mainFrame ||
        typeof nonce !== "string" || !NONCE.test(nonce)) return { ok: false };
    try {
      const base = new URL(origin);
      if (base.protocol !== "http:" || base.port !== "18100" ||
          !["127.0.0.1", "localhost", "[::1]"].includes(base.hostname) ||
          new URL(event.senderFrame.url).origin !== origin) return { ok: false };
    } catch { return { ok: false }; }
    busy = true;
    const controller = new AbortController();
    let timer = setTimeout(() => controller.abort(), 5000);
    let attachedHere = false;
    let releaseGuard;
    let scrollCommand;
    let runInteraction = runScroll;
    let bounded;
    const listeners = [];
    const stop = () => controller.abort(); // Never suppress the user's input.
    const listen = (emitter, name) => { emitter.on(name, stop); listeners.push([emitter, name]); };
    try {
      // Only a live server-owned verifier operation can resolve this nonce.
      // Reopen authentication transports the request, but is not its authority.
      const contract = await request(nonce, null, controller.signal);
      validateContract(contract, nonce);
      clearTimeout(timer);
      timer = setTimeout(stop, Math.max(1, contract.deadline * 1000 - Date.now()));
      for (const name of ["before-input-event", "before-mouse-event", "did-start-navigation",
        "did-navigate-in-page", "render-process-gone", "destroyed"]) listen(wc, name);
      for (const name of ["close", "hide", "minimize", "resize", "move"]) listen(ctx.win, name);

      function identity() {
        if (controller.signal.aborted || Date.now() / 1000 >= contract.deadline ||
            windows.get("main") !== ctx || ctx.win.isDestroyed() || wc.isDestroyed() ||
            !ctx.win.isVisible() || ctx.win.isMinimized() || ctx.visibleViewIds.size || ctx.mainMenuView ||
            ctx.win.webContents !== wc) throw new Error("main_window_changed");
        const url = new URL(wc.getURL());
        if (url.origin !== origin || url.pathname !== `/s/${contract.session_id}` || url.search || url.hash) {
          throw new Error("original_session_not_visible");
        }
        const native = installation();
        if (native.app_path !== APP || native.candidate_sha !== contract.candidate_sha ||
            !Number.isInteger(native.app_pid) || native.app_pid <= 0) throw new Error("installation_changed");
        const renderer = wc.getOSProcessId();
        if (!Number.isInteger(renderer) || renderer <= 0) throw new Error("renderer_unavailable");
        return { ...native, window_id: ctx.win.id, web_contents_id: wc.id, renderer_pid: renderer,
          route: url.pathname, bounds: ctx.win.getBounds() };
      }
      const before = identity();
      if (typeof guard?.acquire !== "function") throw new Error("installation_unavailable");
      releaseGuard = guard.acquire(wc, nonce);
      if (wc.debugger.isAttached()) throw new Error("debugger_in_use");
      wc.debugger.attach("1.3");
      attachedHere = true;
      bounded = (promise) => new Promise((resolve, reject) => {
        const abort = () => reject(new Error("capture_interrupted"));
        // Observe rejection even when cancellation arrived before this call.
        promise.then(resolve, reject).finally(() => controller.signal.removeEventListener("abort", abort));
        if (controller.signal.aborted) return abort();
        controller.signal.addEventListener("abort", abort, { once: true });
      });
      const target = await bounded(wc.debugger.sendCommand("Target.getTargetInfo"));
      const targetId = target?.targetInfo?.targetId;
      if (typeof targetId !== "string" || !targetId || targetId.length > 128) throw new Error("target_unavailable");
      let interaction;
      if (contract.interaction) {
        runInteraction = contract.interaction.kind === "test_object" ? runTestObject : runScroll;
        scrollCommand = { nonce, session_id: contract.session_id, deadline: contract.deadline,
          ...contract.interaction };
        const moved = await bounded(runInteraction(wc, { ...scrollCommand, mode: "start" }));
        identity();
        interaction = { ...contract.interaction, ...moved };
      }
      const accessibility = await bounded(wc.debugger.sendCommand("Accessibility.getFullAXTree"));
      if (!Array.isArray(accessibility?.nodes) || !accessibility.nodes.length) throw new Error("accessibility_unavailable");
      const image = await bounded(wc.capturePage());
      if (image.isEmpty()) throw new Error("screenshot_unavailable");
      const png = image.toPNG();
      const size = image.getSize();
      const observedAt = Date.now() / 1000;
      if (!png.length || size.width < 1 || size.height < 1) throw new Error("screenshot_unavailable");
      const afterTarget = await bounded(wc.debugger.sendCommand("Target.getTargetInfo"));
      if (JSON.stringify(identity()) !== JSON.stringify(before) || afterTarget?.targetInfo?.targetId !== targetId) {
        throw new Error("main_window_changed");
      }
      if (interaction) {
        interaction.restored = await bounded(runInteraction(wc, { ...scrollCommand, mode: "finish" }));
        if (JSON.stringify(interaction.restored) !== JSON.stringify(interaction.before)) throw new Error("capture_cleanup_failed");
        identity();
        scrollCommand = null;
      }
      // Detach and release listeners before publishing evidence; failure is not pass.
      try { wc.debugger.detach(); } catch { throw new Error("capture_cleanup_failed"); }
      attachedHere = false;
      if (wc.debugger.isAttached()) throw new Error("capture_cleanup_failed");
      for (const [emitter, name] of listeners.splice(0)) emitter.removeListener(name, stop);
      releaseGuard();
      releaseGuard = null;
      const body = { schema: 1, nonce, update_id: contract.update_id, attempt: contract.attempt,
        check_id: contract.check_id, worker_pid: contract.worker_pid,
        identity: { ...before, target_id: targetId }, observed_at: observedAt,
        screenshot: { mime_type: "image/png", width: size.width, height: size.height,
          sha256: crypto.createHash("sha256").update(png).digest("hex"), data: png.toString("base64") },
        accessibility, cleanup_complete: true };
      if (interaction) body.interaction = interaction;
      if (Buffer.byteLength(JSON.stringify(body)) > contract.max_output_bytes) throw new Error("capture_output_limit");
      identity();
      const ack = await request(nonce, body, controller.signal);
      if (controller.signal.aborted || ack?.ok !== true || ack.nonce !== nonce) throw new Error("capture_not_accepted");
      return { ok: true }; // Image, AX tree and credentials never cross into the renderer.
    } catch (error) { return { ok: false, reason: REASONS.has(error?.message) ? error.message : "capture_unavailable" }; }
    finally {
      if (scrollCommand && !wc.isDestroyed()) {
        // Restore only while our operation still owns the view and its budget.
        // User interruption must not be overwritten by automated restoration.
        try {
          if (!controller.signal.aborted) await bounded(runInteraction(wc, { ...scrollCommand, mode: "finish" }));
        } catch { /* failed cleanup cannot publish a successful receipt */ }
        void runInteraction(wc, { ...scrollCommand, mode: "abandon" }).catch(() => {});
      }
      clearTimeout(timer);
      controller.abort();
      for (const [emitter, name] of listeners) emitter.removeListener(name, stop);
      if (attachedHere) {
        try { if (wc.debugger.isAttached()) wc.debugger.detach(); } catch { /* no successful receipt */ }
      }
      if (releaseGuard) releaseGuard();
      busy = false;
    }
  });
}

module.exports = { registerUiVerificationIpc };
