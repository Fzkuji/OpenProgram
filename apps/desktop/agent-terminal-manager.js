"use strict";

const crypto = require("node:crypto");

const TERMINAL_ID = /^agent-terminal:[0-9a-f]{32}$/;
const MAX_OUTPUT = 1_000_000;
const MAX_READ = 24_000;
const MAX_INPUT = 16_384;

/** Owns Agent PTYs only. Manual terminal IDs never enter this registry.
 * The trusted host must authorize plans before dispatch; never expose this
 * method to raw renderer arguments. Worker tickets and IPC wiring are separate.
 * A receipt acknowledges input delivery, never command completion.
 */
function createAgentTerminalManager({ spawn, stop, now = Date.now,
  waitForPid = async (pty) => pty.pid, leaseMs = 30 * 60_000,
  maxSessions = 32, maxPerOwner = 4, startupTimeoutMs = 5_000 } = {}) {
  if (!Number.isFinite(startupTimeoutMs) || startupTimeoutMs <= 0) {
    throw new TypeError("startupTimeoutMs must be a positive finite number");
  }
  const records = new Map();
  const pendingOpens = new Set();
  // A released native host must never reacquire a terminal through a late
  // request. Weak membership does not keep closed BrowserWindows alive.
  const releasedHosts = new WeakSet();
  let disposed = false;

  const ownerKey = (plan) => JSON.stringify([plan.session_id, plan.agent_id, plan.scope_id]);
  const error = (reason) => ({ ok: false, error: reason });
  const publicState = (entry) => ({
    terminal_id: entry.id, generation: entry.generation,
    status: entry.status, controller: entry.controller,
    start_cwd: entry.cwd, pid: entry.pty.pid || null,
    exit_code: entry.exitCode ?? null, output_end: entry.end,
    last_operation: entry.lastOperation ? { ...entry.lastOperation } : null,
  });
  const validOwner = (entry, host, plan) => entry.host === host
    && entry.owner === ownerKey(plan) && entry.capability === plan.capability
    && entry.generation === plan.generation;
  function append(entry, data) {
    entry.buffer += String(data);
    entry.end += String(data).length;
    if (entry.buffer.length > MAX_OUTPUT) {
      entry.buffer = entry.buffer.slice(-MAX_OUTPUT);
      // Do not begin retained output in the middle of a surrogate pair.
      if (/^[\uDC00-\uDFFF]/.test(entry.buffer)) entry.buffer = entry.buffer.slice(1);
    }
  }
  function snapshot(entry, plan) {
    const start = entry.end - entry.buffer.length;
    const cursor = plan.cursor ?? 0;
    if (!Number.isSafeInteger(cursor) || cursor < 0 || cursor > entry.end) return error("invalid_cursor");
    const from = Math.max(cursor, start);
    let to = Math.min(entry.end, from + MAX_READ);
    const index = to - start;
    if (to < entry.end && /[\uD800-\uDBFF]/.test(entry.buffer.charAt(index - 1))) to -= 1;
    return { ok: true, ...publicState(entry),
      data: entry.buffer.slice(from - start, to - start),
      next_cursor: to, truncated: cursor < start, has_more: to < entry.end };
  }
  function hostUnavailable(host) {
    if (!host || typeof host !== "object" || releasedHosts.has(host)) return true;
    try { return host.isDestroyed?.() === true; } catch { return true; }
  }
  function waitForReady(entry, deadline) {
    const signal = entry.startupAbort.signal;
    const timeoutMs = Math.max(0, Math.min(startupTimeoutMs, deadline - now(), 2_147_483_647));
    return new Promise((resolve) => {
      let finished = false;
      let timer;
      const finish = (pid) => {
        if (finished) return;
        finished = true;
        clearTimeout(timer);
        signal.removeEventListener("abort", aborted);
        resolve(pid);
      };
      const aborted = () => finish(null);
      if (signal.aborted) return finish(null);
      signal.addEventListener("abort", aborted, { once: true });
      // Keep this timer referenced while the open call is pending. A PID
      // adapter that ignores cancellation still cannot block the caller.
      timer = setTimeout(() => finish(null), timeoutMs);
      Promise.resolve().then(() => {
        if (signal.aborted) return null;
        return waitForPid(entry.pty, { signal, timeoutMs });
      }).then(finish, () => finish(null));
    });
  }
  function beginStop(entry, reason) {
    entry.startupAbort.abort();
    if (entry.status === "exited" || entry.status === "closed") return;
    if (entry.status === "stopping") return;
    entry.status = "stopping";
    entry.lastOperation = { action: "close", phase: "requested", reason };
    try { stop(entry.pty, "SIGTERM"); } catch { /* wait for confirmed exit */ }
    // Some adapters emit exit synchronously from stop(). Do not schedule an
    // escalation after its exit handler has already completed cleanup.
    if (entry.status !== "stopping") return;
    entry.killTimer = setTimeout(() => {
      if (entry.status === "stopping") {
        try { stop(entry.pty, "SIGKILL"); } catch { /* still unconfirmed */ }
      }
    }, 1_000);
    entry.killTimer.unref?.();
  }
  async function open(plan, host) {
    if (records.has(plan.terminal_id) || pendingOpens.has(plan.terminal_id)) return error("terminal_exists");
    const live = [...records.values()].filter((r) => !["closed", "exited"].includes(r.status));
    if (live.length >= maxSessions || live.filter((r) => r.owner === ownerKey(plan)).length >= maxPerOwner) {
      return error("terminal_limit");
    }
    const launch = plan.launch;
    if (!launch || typeof launch.command !== "string" || !Array.isArray(launch.args)
      || !launch.args.every((a) => typeof a === "string") || typeof launch.cwd !== "string"
      || !launch.env || Object.values(launch.env).some((v) => typeof v !== "string")) return error("invalid_launch");
    pendingOpens.add(plan.terminal_id);
    let entry;
    try {
      const pty = spawn(launch.command, launch.args, {
        name: "xterm-256color", cwd: launch.cwd, env: launch.env,
        cols: 100, rows: 30, useConpty: launch.use_conpty === true,
      });
      entry = {
        id: plan.terminal_id, host, owner: ownerKey(plan), capability: plan.capability,
        generation: plan.generation, pty, cwd: launch.cwd, controller: "agent",
        status: "starting", buffer: "", end: 0, lastSequence: plan.sequence,
        receipts: new Map(), lastOperation: null, touched: now(), killTimer: null,
        startupAbort: new AbortController(),
      };
      records.set(entry.id, entry);
      pty.onData((data) => append(entry, data));
      pty.onExit(({ exitCode }) => {
        const stopping = entry.status === "stopping";
        entry.status = stopping ? "closed" : "exited";
        entry.exitCode = exitCode;
        if (entry.killTimer) clearTimeout(entry.killTimer);
        entry.killTimer = null;
        entry.lastOperation = { action: "exit", phase: "confirmed" };
        entry.startupAbort.abort();
      });
      const pid = await waitForReady(entry, plan.deadline);
      if (disposed || !Number.isSafeInteger(pid) || pid <= 0
        || now() >= plan.deadline || hostUnavailable(host) || entry.status !== "starting") {
        beginStop(entry, "open_unconfirmed");
        return { ...error("open_unconfirmed"), ...publicState(entry) };
      }
      entry.status = "running";
      return { ok: true, ...publicState(entry), phase: "opened" };
    } catch {
      if (entry) beginStop(entry, "open_failed");
      return error("terminal_start_failed");
    } finally {
      pendingOpens.delete(plan.terminal_id);
    }
  }
  async function dispatch(plan, host) {
    if (disposed) return error("terminal_manager_closed");
    if (hostUnavailable(host)) return error("terminal_host_unavailable");
    if (!plan || !TERMINAL_ID.test(plan.terminal_id || "")
      || typeof plan.capability !== "string" || !/^[0-9a-f]{64}$/.test(plan.capability)
      || !Number.isSafeInteger(plan.sequence) || plan.sequence < 1
      || typeof plan.generation !== "string" || !/^[0-9a-f]{32}$/.test(plan.generation)
      || !Number.isFinite(plan.deadline) || now() >= plan.deadline
      || !plan.session_id || !plan.agent_id || !plan.scope_id) return error("invalid_or_expired_plan");
    if (plan.action === "open") return open(plan, host);
    const entry = records.get(plan.terminal_id);
    if (!entry || !validOwner(entry, host, plan)) return error("terminal_not_found");
    const digest = crypto.createHash("sha256").update(JSON.stringify(plan)).digest("hex");
    const prior = entry.receipts.get(plan.sequence);
    if (prior) return prior.digest === digest ? prior.result : error("conflicting_operation");
    if (plan.sequence <= entry.lastSequence) return error("stale_operation");
    entry.lastSequence = plan.sequence;
    entry.touched = now();
    let result;
    try {
      if (plan.action === "read") {
        result = snapshot(entry, plan);
      } else if (plan.action === "close") {
        beginStop(entry, "requested");
        result = { ok: true, ...publicState(entry), phase: entry.status === "closed" ? "closed" : "close_requested" };
      } else if (plan.action === "pause" || plan.action === "resume") {
        if (plan.actor !== "human") return error("human_control_required");
        entry.controller = plan.action === "pause" ? "human" : "agent";
        result = { ok: true, ...publicState(entry), phase: "controller_changed" };
      } else if (plan.action === "input" || plan.action === "interrupt" || plan.action === "resize") {
        if (entry.status !== "running") return error("terminal_not_running");
        if (plan.actor !== entry.controller) return error("terminal_control_paused");
        if (plan.action === "resize") {
          if (!Number.isInteger(plan.cols) || !Number.isInteger(plan.rows)
            || plan.cols < 20 || plan.cols > 500 || plan.rows < 5 || plan.rows > 200) return error("invalid_size");
          entry.pty.resize(plan.cols, plan.rows);
        } else {
          const data = plan.action === "interrupt" ? "\x03" : plan.data;
          if (typeof data !== "string" || !data || Buffer.byteLength(data, "utf8") > MAX_INPUT) return error("invalid_input");
          // Record uncertainty BEFORE the write. Throwing does not prove that
          // no bytes reached the PTY; never retry automatically.
          const operation = { action: plan.action, sequence: plan.sequence, phase: "unknown" };
          entry.lastOperation = operation;
          entry.pty.write(data);
          // write() may synchronously emit an exit event. Do not mutate that
          // newer exit observation into a delivery acknowledgement.
          operation.phase = "delivered";
        }
        result = { ok: true, ...publicState(entry), phase: "delivered", command_complete: null };
      } else {
        result = error("unsupported_action");
      }
    } catch {
      result = { ...error("operation_unconfirmed"), ...publicState(entry) };
    }
    entry.receipts.set(plan.sequence, { digest, result });
    while (entry.receipts.size > 256) entry.receipts.delete(entry.receipts.keys().next().value);
    return result;
  }
  function releaseHost(host) {
    if (!host || typeof host !== "object") return;
    releasedHosts.add(host);
    for (const entry of records.values()) if (entry.host === host) beginStop(entry, "window_closed");
  }
  function sweep() {
    for (const [id, entry] of records) {
      if (now() - entry.touched <= leaseMs) continue;
      if (["closed", "exited"].includes(entry.status)) records.delete(id);
      else beginStop(entry, "idle_expired");
    }
  }
  function dispose() {
    disposed = true;
    for (const entry of records.values()) beginStop(entry, "app_closed");
  }
  return { dispatch, releaseHost, sweep, dispose };
}

module.exports = { createAgentTerminalManager, MAX_OUTPUT, MAX_READ, MAX_INPUT };
