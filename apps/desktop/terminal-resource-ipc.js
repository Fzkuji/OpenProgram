"use strict";

const fs = require("node:fs");
const http = require("node:http");
const { execFileSync } = require("node:child_process");
const { createTerminalResourceManager } = require("./terminal-resource-manager");
const { resolveTerminalCommand, waitForTerminalPid, killWindowsProcessTree } = require("./terminal-command");

/** Redeem a single-use worker intention at the fixed local owner-auth endpoint.
 * Renderer input never supplies an executable, environment, principal or URL.
 */
function nativeTerminalRequest(origin, token, route, payload) {
  let url;
  try { url = new URL(`/api/terminal/${route}`, origin); } catch { return Promise.reject(Error("terminal_authority_unavailable")); }
  if (!["claim", "result"].includes(route) || url.protocol !== "http:"
    || !["127.0.0.1", "localhost", "[::1]"].includes(url.hostname)
    || url.username || url.password || typeof token !== "string" || !token
    || !/^[0-9a-f]{64}$/.test(payload?.ticket || "")) {
    return Promise.reject(Error("terminal_authority_unavailable"));
  }
  return new Promise((resolve, reject) => {
    let settled = false, timer;
    const finish = (error, result) => {
      if (settled) return;
      settled = true; clearTimeout(timer); error ? reject(error) : resolve(result);
    };
    const body = JSON.stringify(payload);
    if (Buffer.byteLength(body) > 260_000) { finish(Error("terminal_result_too_large")); return; }
    const req = http.request(url, { method: "POST", headers: {
      Authorization: `Bearer ${token}`, "Content-Type": "application/json", "Content-Length": Buffer.byteLength(body),
    } }, (res) => {
      let size = 0; const chunks = [];
      res.on("data", (chunk) => {
        size += chunk.length;
        if (size > 100_000) { req.destroy(); finish(Error("terminal_claim_too_large")); return; }
        chunks.push(chunk);
      });
      res.on("aborted", () => finish(Error("terminal_claim_failed")));
      res.on("error", () => finish(Error("terminal_claim_failed")));
      res.on("end", () => {
        try {
          if (res.statusCode !== 200) throw Error("terminal_claim_rejected");
          const result = JSON.parse(Buffer.concat(chunks).toString("utf8"));
          if (result.ok !== true) throw Error("terminal_claim_rejected");
          finish(null, result);
        } catch { finish(Error("terminal_claim_rejected")); }
      });
    });
    timer = setTimeout(() => { req.destroy(); finish(Error("terminal_claim_timeout")); }, 4_000);
    req.on("error", () => finish(Error("terminal_claim_failed")));
    req.end(body);
  });
}

async function redeemTerminalTicket(origin, token, ticket, windowId) {
  const result = await nativeTerminalRequest(origin, token, "claim", { ticket, window_id: windowId });
  if (!result.plan || result.plan.window_id !== windowId) throw Error("terminal_claim_rejected");
  return result.plan;
}

async function acknowledgeTerminalResult(origin, token, ticket, windowId, result) {
  return nativeTerminalRequest(origin, token, "result", { ticket, window_id: windowId, result });
}

function registerTerminalResourceIpc({ ipcMain, app, contextForSender, origin, getOwnerToken,
  spawn = (...args) => require("node-pty").spawn(...args),
  redeem = redeemTerminalTicket, acknowledge = acknowledgeTerminalResult, platform = process.platform,
  env = process.env, fileSystem = fs, kill = process.kill.bind(process),
  listProcesses = () => execFileSync("/bin/ps", ["-axo", "pid=,ppid="], { encoding: "utf8" }),
  windowsKill = killWindowsProcessTree, waitForPid = waitForTerminalPid,
  timers = { setTimeout, clearTimeout, setInterval, clearInterval } } = {}) {
  const registered = new WeakSet();
  const descendants = new WeakMap();
  function stop(pty, signal) {
    const pid = pty.pid;
    if (platform === "win32") { windowsKill(pid); pty.kill(); return; }
    if (!descendants.has(pty)) {
      const all = new Map(), selected = [], seen = new Set([pid]);
      try {
        for (const row of listProcesses().split("\n")) {
          const match = row.trim().match(/^(\d+)\s+(\d+)$/);
          if (!match) continue;
          const child = Number(match[1]), parent = Number(match[2]);
          all.set(parent, [...(all.get(parent) || []), child]);
        }
        const pending = [...(all.get(pid) || [])];
        while (pending.length) {
          const child = pending.pop(); if (seen.has(child)) continue;
          seen.add(child); selected.push(child); pending.push(...(all.get(child) || []));
        }
      } catch { /* group/root cleanup remains available */ }
      descendants.set(pty, selected);
    }
    let group = false;
    if (Number.isSafeInteger(pid) && pid > 0) {
      try { kill(-pid, signal); group = true; } catch { /* fall back to root */ }
    }
    for (const child of descendants.get(pty) || []) { try { kill(child, signal); } catch { /* already exited */ } }
    if (!group) pty.kill(signal);
  }
  const manager = createTerminalResourceManager({ spawn, stop, timers,
    finalizeStop(pty) {
      for (const child of descendants.get(pty) || []) {
        try { kill(child, "SIGKILL"); } catch { /* child may already have exited */ }
      }
      descendants.delete(pty);
    },
    waitForPid: (pty, options) => platform === "win32" ? waitForPid(pty, options) : pty.pid,
    resolveLaunch(request) {
      const cwd = fileSystem.realpathSync(request.cwd || app.getPath("home"));
      if (!fileSystem.statSync(cwd).isDirectory()) throw Error("invalid_cwd");
      return { ...resolveTerminalCommand(request.preset, { platform, env, existsSync: fileSystem.existsSync }),
        cwd, env: { ...env, TERM: "xterm-256color", COLORTERM: "truecolor" } };
    },
    emit(sender, kind, value) {
      if (sender.isDestroyed()) return;
      sender.send(kind === "data" ? "terminal:data" : "terminal:resource", value);
    },
  });
  function owner(event) {
    const ctx = contextForSender(event);
    if (!ctx || event.sender !== ctx.win.webContents) return null;
    if (!event.senderFrame || event.senderFrame !== event.sender.mainFrame) return null;
    try { if (new URL(event.senderFrame.url).origin !== origin) return null; } catch { return null; }
    if (!registered.has(event.sender)) {
      registered.add(event.sender);
      event.sender.once("destroyed", () => manager.releaseHost(event.sender));
    }
    return ctx;
  }
  ipcMain.handle("terminal:start", async (event, request) => {
    const ctx = owner(event);
    if (!ctx) return { ok: false, error: "unauthorized_sender" };
    const preset = request?.preset;
    if (!["shell", "claude"].includes(preset)) return { ok: false, error: "invalid_preset" };
    if (request.id !== `terminal:${ctx.id}:${preset}`) return { ok: false, error: "invalid_terminal" };
    const cols = Number.isInteger(request.cols) ? Math.min(500, Math.max(20, request.cols)) : 80;
    const rows = Number.isInteger(request.rows) ? Math.min(200, Math.max(5, request.rows)) : 24;
    const result = await manager.open({ id: request.id, preset, cwd: request.cwd, shared: true, cols, rows }, event.sender);
    if (result.ok && result.reused) {
      manager.human("resize", { terminal_id: request.id, generation: result.generation, cols, rows }, event.sender);
      // Reattaching a view does not launch or change the shell's cwd.
      let cursor = 0;
      do {
        const read = manager.human("read", { terminal_id: request.id, generation: result.generation, cursor }, event.sender);
        if (!read.ok) break;
        if (read.data && !event.sender.isDestroyed()) event.sender.send("terminal:data", {
          id: request.id, generation: result.generation, data: read.data,
        });
        cursor = read.next_cursor; if (!read.has_more) break;
      } while (!event.sender.isDestroyed());
    }
    return result;
  });
  for (const [channel, action] of [["terminal:write", "input"], ["terminal:resize", "resize"], ["terminal:stop", "close"]]) {
    ipcMain.on(channel, (event, id, a, b) => {
      const ctx = owner(event); if (!ctx) return;
      if (id !== `terminal:${ctx.id}:shell` && id !== `terminal:${ctx.id}:claude`) return;
      manager.human(action, { terminal_id: id, data: a, cols: a, rows: b }, event.sender);
    });
  }
  ipcMain.handle("terminal:resource", (event, action, request = {}) => {
    if (!owner(event)) return { ok: false, error: "unauthorized_sender" };
    if (!["list", "read", "input", "resize", "close", "share"].includes(action)) return { ok: false, error: "unsupported_action" };
    if (!request || typeof request !== "object" || (action !== "list"
      && (typeof request.generation !== "string" || !request.generation))) return { ok: false, error: "generation_required" };
    return manager.human(action, request, event.sender);
  });
  ipcMain.handle("terminal:agent-ticket", async (event, ticket) => {
    const ctx = owner(event);
    if (!ctx || typeof ticket !== "string" || !/^[0-9a-f]{64}$/.test(ticket)) return { ok: false, error: "unauthorized_sender" };
    try {
      const token = getOwnerToken();
      const plan = await redeem(origin, token, ticket, ctx.id);
      if (!/^[0-9a-f]{64}$/.test(plan.result_ticket || "")) return { ok: false, error: "terminal_authority_unavailable" };
      const result = !owner(event) || plan.window_id !== ctx.id
        ? { ok: false, error: "terminal_host_unavailable" }
        : await manager.agent(plan, event.sender);
      // Completion travels native-to-worker with a separate one-use receipt.
      // The renderer never receives the owner token or this receipt ticket.
      await acknowledge(origin, token, plan.result_ticket, ctx.id, result);
      return result;
    } catch { return { ok: false, error: "terminal_result_unconfirmed", result_unconfirmed: true }; }

  });
  const timer = timers.setInterval(() => manager.sweep(), 30_000); timer?.unref?.();
  app.on("before-quit", () => { timers.clearInterval(timer); manager.dispose(); });
  return manager;
}

module.exports = { registerTerminalResourceIpc, redeemTerminalTicket, acknowledgeTerminalResult };
