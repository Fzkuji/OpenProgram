"use strict";

const crypto = require("node:crypto");

const MAX_OUTPUT = 1_000_000;
const MAX_READ = 24_000;
const MAX_INPUT = 16_384;
const ended = (entry) => ["closed", "exited"].includes(entry.status);
const fail = (error) => ({ ok: false, error });
const randomId = () => crypto.randomBytes(16).toString("hex");

/** One native PTY per resource, regardless of creator. Agent bindings do not own
 * the process. Only a trusted host may call agent after redeeming a worker
 * ticket. Human input does not pause a task; execution controls own that state.
 */
function createTerminalResourceManager({ spawn, stop, resolveLaunch,
  waitForPid = async (pty) => pty.pid, finalizeStop = () => {}, now = Date.now,
  emit = () => {}, startupTimeoutMs = 5_000, leaseMs = 90_000,
  maxSessions = 32, maxHistory = 64, timers = { setTimeout, clearTimeout } } = {}) {
  const schedule = timers.setTimeout, cancelTimer = timers.clearTimeout;
  for (const [name, value] of Object.entries({ startupTimeoutMs, leaseMs, maxSessions, maxHistory })) {
    if (!Number.isSafeInteger(value) || value <= 0) throw new TypeError(`${name} must be a positive integer`);
  }
  const records = new Map();
  const releasedHosts = new WeakSet();
  let disposed = false;
  const unavailable = (host) => {
    if (!host || typeof host !== "object" || releasedHosts.has(host)) return true;
    try { return host.isDestroyed?.() === true; } catch { return true; }
  };
  const leaseFor = (entry) => {
    if (entry.lease && entry.lease.until <= now()) entry.lease = null;
    return entry.lease;
  };
  const state = (entry) => ({
    terminal_id: entry.id, generation: entry.generation, preset: entry.preset,
    status: entry.status, start_cwd: entry.cwd, pid: entry.pty.pid || null,
    exit_code: entry.exitCode ?? null, output_end: entry.end,
    shared: entry.shared, session_ids: [...entry.associations],
    input_revision: entry.inputRevision, human_input_pending: entry.humanPending,
    in_use: !!leaseFor(entry),
    last_operation: entry.lastOperation ? { ...entry.lastOperation } : null,
  });
  function notify(entry) {
    try { emit(entry.host, "resource", state(entry)); } catch { /* view teardown cannot alter execution */ }
  }
  function append(entry, chunk) {
    if (records.get(entry.id) !== entry || ended(entry)) return;
    const data = String(chunk);
    entry.end += data.length;
    entry.buffer = (entry.buffer + data).slice(-MAX_OUTPUT);
    if (/^[\uDC00-\uDFFF]/.test(entry.buffer)) entry.buffer = entry.buffer.slice(1);
    try { emit(entry.host, "data", { id: entry.id, generation: entry.generation, data }); } catch { /* view is optional */ }
  }
  function snapshot(entry, cursor = 0) {
    if (!Number.isSafeInteger(cursor) || cursor < 0 || cursor > entry.end) return fail("invalid_cursor");
    const start = entry.end - entry.buffer.length;
    let from = Math.max(cursor, start);
    if (from > start && /[\uDC00-\uDFFF]/.test(entry.buffer.charAt(from - start))) from += 1;
    let to = Math.min(entry.end, from + MAX_READ);
    if (to < entry.end && /[\uD800-\uDBFF]/.test(entry.buffer.charAt(to - start - 1))) to -= 1;
    return { ok: true, ...state(entry), data: entry.buffer.slice(from - start, to - start),
      next_cursor: to, truncated: cursor < start, has_more: to < entry.end };
  }
  function beginStop(entry, reason) {
    entry.startupAbort.abort();
    entry.lease = null;
    if (ended(entry) || entry.status === "stopping") return;
    entry.status = "stopping";
    entry.lastOperation = { action: "close", phase: "requested", reason };
    try { stop(entry.pty, "SIGTERM"); } catch { /* still unconfirmed */ }
    if (entry.status === "stopping") {
      entry.killTimer = schedule(() => {
        entry.killTimer = null;
        if (entry.status === "stopping") {
          try { stop(entry.pty, "SIGKILL"); } catch { /* no fabricated exit */ }
        }
      }, 1_000);
      entry.killTimer?.unref?.();
    }
    notify(entry);
  }
  function ready(entry, deadline) {
    const signal = entry.startupAbort.signal;
    const timeoutMs = Math.max(0, Math.min(startupTimeoutMs, deadline - now(), 2_147_483_647));
    return new Promise((resolve) => {
      let finished = false, timer;
      const finish = (pid) => {
        if (finished) return;
        finished = true; cancelTimer(timer);
        signal.removeEventListener("abort", aborted); resolve(pid);
      };
      const aborted = () => finish(null);
      if (signal.aborted) return finish(null);
      signal.addEventListener("abort", aborted, { once: true });
      timer = schedule(() => finish(null), timeoutMs);
      Promise.resolve().then(() => signal.aborted ? null : waitForPid(entry.pty, { signal, timeoutMs }))
        .then(finish, () => finish(null));
    });
  }
  function trimHistory() {
    const history = [...records.values()].filter(ended);
    for (const entry of history.slice(0, Math.max(0, history.length - maxHistory))) records.delete(entry.id);
  }
  async function open(request, host) {
    if (disposed || unavailable(host)) return fail("terminal_host_unavailable");
    if (!request || typeof request.id !== "string" || request.id.length > 256 || !request.id
      || !["shell", "claude"].includes(request.preset)) return fail("invalid_terminal");
    const existing = records.get(request.id);
    if (existing && existing.host !== host) return fail("terminal_not_found");
    if (existing?.status === "stopping") {
      // Restart waits for confirmed exit. Never start two shells under one ID.
      let timer;
      try {
        await Promise.race([existing.closed, new Promise((resolve) => {
          timer = schedule(resolve, startupTimeoutMs);
        })]);
      } finally { cancelTimer(timer); }
      if (!ended(existing)) return fail("terminal_busy");
      return open(request, host);
    }
    if (existing && !ended(existing)) {
      if (existing.status !== "running") return fail("terminal_busy");
      return { ok: true, ...state(existing), reused: true };
    }
    if ([...records.values()].filter((entry) => !ended(entry)).length >= maxSessions) return fail("terminal_limit");
    let entry;
    try {
      const launch = resolveLaunch(request);
      const pty = spawn(launch.command, launch.args, {
        name: "xterm-256color", cwd: launch.cwd, env: launch.env,
        cols: request.cols || 100, rows: request.rows || 30, useConpty: launch.useConpty === true,
      });
      entry = { id: request.id, host, pty, preset: request.preset, cwd: launch.cwd,
        generation: randomId(), status: "starting", buffer: "", end: 0,
        shared: request.shared === true, privateSession: request.session_id || "",
        associations: new Set(request.session_id ? [request.session_id] : []),
        inputRevision: 0, humanPending: false, lease: null,
        receipts: new Map(), lastOperation: null, startupAbort: new AbortController(), killTimer: null };
      entry.closed = new Promise((resolve) => { entry.confirmClosed = resolve; });
      records.set(entry.id, entry);
      pty.onData((data) => append(entry, data));
      pty.onExit(({ exitCode }) => {
        const stopping = entry.status === "stopping";
        // Root exit must not cancel cleanup of descendants that ignored TERM.
        // The native adapter only targets the captured descendants here, never
        // the exited root PID or its potentially reusable process-group ID.
        if (stopping) finalizeStop(entry.pty);
        entry.status = stopping ? "closed" : "exited";
        entry.exitCode = exitCode; entry.confirmClosed(); entry.lease = null; entry.startupAbort.abort();
        if (entry.killTimer !== null) cancelTimer(entry.killTimer);
        entry.killTimer = null; entry.lastOperation = { action: "exit", phase: "confirmed" };
        if (records.get(entry.id) === entry) {
          notify(entry);
          try { emit(host, "data", { id: entry.id, generation: entry.generation, data: "", done: true, exitCode }); } catch { /* no viewer */ }
        }
        trimHistory();
      });
      const deadline = request.deadline ?? now() + startupTimeoutMs;
      const pid = await ready(entry, deadline);
      if (disposed || unavailable(host) || !Number.isSafeInteger(pid) || pid <= 0
        || now() >= deadline || entry.status !== "starting") {
        beginStop(entry, "open_unconfirmed");
        return { ...fail("open_unconfirmed"), ...state(entry) };
      }
      entry.status = "running"; notify(entry);
      return { ok: true, ...state(entry), reused: false };
    } catch {
      if (entry) beginStop(entry, "open_failed");
      return fail("terminal_start_failed");
    }
  }
  const find = (id, host, generation) => {
    const entry = records.get(id);
    return entry && entry.host === host && (!generation || generation === entry.generation) ? entry : null;
  };
  const accessible = (entry, sessionId) => entry.shared || entry.privateSession === sessionId;
  function input(entry, data, human) {
    const limit = human ? 65_536 : MAX_INPUT;
    if (entry.status !== "running") return fail("terminal_not_running");
    if (typeof data !== "string" || !data || Buffer.byteLength(data, "utf8") > limit) return fail("invalid_input");
    const operation = { action: "input", phase: "unknown" };
    entry.lastOperation = operation;
    entry.inputRevision += 1;
    if (human) {
      // Conservative input-fragment protection, not a shell-completion parser.
      const lastBoundary = Math.max(data.lastIndexOf("\r"), data.lastIndexOf("\n"), data.lastIndexOf("\x03"));
      entry.humanPending = lastBoundary < 0 || lastBoundary < data.length - 1;
    }
    try {
      entry.pty.write(data); operation.phase = "delivered"; notify(entry);
      return { ok: true, ...state(entry), phase: "delivered", command_complete: null };
    } catch {
      notify(entry); return { ...fail("operation_unconfirmed"), ...state(entry), command_complete: null };
    }
  }
  function human(action, request, host) {
    if (disposed || unavailable(host)) return fail("terminal_host_unavailable");
    if (action === "list") return { ok: true, items: [...records.values()].filter((r) => r.host === host).map(state) };
    const entry = find(request?.terminal_id, host, request?.generation);
    if (!entry) return fail("terminal_not_found");
    if (action === "read") return snapshot(entry, request.cursor);
    if (action === "input") return input(entry, request.data, true);
    if (action === "close") {
      beginStop(entry, "human_closed");
      return { ok: true, ...state(entry), phase: ended(entry) ? "closed" : "close_requested" };
    }
    if (action === "share") {
      if (typeof request.shared !== "boolean" || (!request.shared && !request.session_id)) return fail("invalid_scope");
      entry.shared = request.shared;
      if (!request.shared) entry.privateSession = request.session_id;
      entry.lease = null; notify(entry); return { ok: true, ...state(entry) };
    }
    if (action === "resize") {
      if (!Number.isInteger(request.cols) || !Number.isInteger(request.rows)) return fail("invalid_size");
      try { entry.pty.resize(Math.min(500, Math.max(20, request.cols)), Math.min(200, Math.max(5, request.rows))); }
      catch { return fail("resize_failed"); }
      return { ok: true, ...state(entry) };
    }
    return fail("unsupported_action");
  }
  async function agent(plan, host) {
    if (disposed || unavailable(host)) return fail("terminal_host_unavailable");
    if (!plan || typeof plan.owner_id !== "string" || !plan.owner_id
      || typeof plan.session_id !== "string" || !plan.session_id
      || !Number.isFinite(plan.deadline) || now() >= plan.deadline) return fail("invalid_or_expired_plan");
    const { action, session_id: sessionId, owner_id: owner } = plan;
    // These identities are certified finished by the worker, not model input.
    if (Array.isArray(plan.retired_owners)) {
      for (const record of records.values()) {
        if (record.host === host && record.lease && plan.retired_owners.includes(record.lease.owner)) {
          record.lease = null; notify(record);
        }
      }
    }
    if (action === "list") {
      return { ok: true, items: [...records.values()]
        .filter((entry) => entry.host === host && accessible(entry, sessionId) && !ended(entry)).map(state) };
    }
    if (action === "open") {
      if (!/^terminal-resource:[0-9a-f]{32}$/.test(plan.terminal_id || "")) return fail("invalid_terminal");
      const result = await open({ id: plan.terminal_id, preset: "shell", cwd: plan.workdir,
        session_id: sessionId, shared: false, deadline: plan.deadline }, host);
      if (!result.ok) return result;
      return agent({ ...plan, action: "observe", generation: result.generation, cursor: 0 }, host);
    }
    const entry = find(plan.terminal_id, host, plan.generation);
    if (!entry || !plan.generation || !accessible(entry, sessionId)) return fail("terminal_not_found");
    if (action === "observe") {
      const result = snapshot(entry, plan.cursor);
      if (!result.ok) return result;
      const lease = leaseFor(entry);
      if (lease && lease.owner !== owner) return fail("terminal_in_use");
      if (!ended(entry)) {
        entry.lease = lease || { owner, binding: randomId() };
        entry.lease.until = now() + leaseMs;
      }
      if (entry.associations.size < 128) entry.associations.add(sessionId);
      notify(entry);
      return { ...result, session_ids: [...entry.associations], binding_id: entry.lease?.binding || null, in_use: !!entry.lease };
    }
    const key = `${owner}:${plan.operation_id}`;
    const digest = crypto.createHash("sha256").update(JSON.stringify([
      action, plan.generation, plan.binding_id, plan.data, plan.expected_input_revision, plan.shared, plan.cols, plan.rows,
    ])).digest("hex");
    if (typeof plan.operation_id !== "string" || !plan.operation_id || plan.operation_id.length > 256) return fail("invalid_operation");
    const prior = entry.receipts.get(key);
    if (prior) return prior.digest === digest ? { ...prior.result, receipt_replayed: true } : fail("conflicting_operation");
    const lease = leaseFor(entry);
    if (!lease || lease.owner !== owner || lease.binding !== plan.binding_id) return fail("binding_not_found");
    lease.until = now() + leaseMs;
    let result;
    if (action === "release") {
      entry.lease = null; notify(entry); result = { ok: true, ...state(entry), phase: "released" };
    } else if (["input", "interrupt", "close", "share", "resize"].includes(action)) {
      if (entry.humanPending) return fail("human_input_pending");
      if (plan.expected_input_revision !== entry.inputRevision) return fail("observe_required");
      if (action === "share" || action === "resize") {
        result = human(action, { ...plan, session_id: sessionId }, host);
      } else if (action === "close") {
        beginStop(entry, "agent_closed");
        result = { ok: true, ...state(entry), phase: ended(entry) ? "closed" : "close_requested" };
      } else result = input(entry, action === "interrupt" ? "\x03" : plan.data, false);
    } else return fail("unsupported_action");
    entry.receipts.set(key, { digest, result });
    while (entry.receipts.size > 128) entry.receipts.delete(entry.receipts.keys().next().value);
    return result;
  }
  function releaseHost(host) {
    if (!host || typeof host !== "object") return;
    releasedHosts.add(host);
    for (const entry of records.values()) if (entry.host === host) beginStop(entry, "window_closed");
  }
  function sweep() {
    for (const entry of records.values()) {
      const prior = entry.lease;
      if (!leaseFor(entry) && prior) notify(entry);
    }
    trimHistory();
  }
  function dispose() { disposed = true; for (const entry of records.values()) beginStop(entry, "app_closed"); }
  return { open, agent, human, releaseHost, sweep, dispose };
}

module.exports = { createTerminalResourceManager, MAX_INPUT, MAX_READ, MAX_OUTPUT };
