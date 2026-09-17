"use strict";

const { test } = require("node:test");
const assert = require("node:assert/strict");
const { createAgentTerminalManager } = require("../agent-terminal-manager");

function deferred() {
  let resolve;
  const promise = new Promise((done) => { resolve = done; });
  return { promise, resolve };
}

// A failing startup regression must fail promptly instead of hanging CI.
async function bounded(promise) {
  let timer;
  try {
    return await Promise.race([
      promise,
      new Promise((_, reject) => {
        timer = setTimeout(() => reject(new Error("startup did not settle")), 500);
      }),
    ]);
  } finally {
    clearTimeout(timer);
  }
}

function fixture(t, options = {}) {
  const pties = [];
  const stops = [];
  const host = { isDestroyed: () => false };
  const plan = {
    terminal_id: "agent-terminal:" + "a".repeat(32), capability: "b".repeat(64),
    generation: "c".repeat(32), session_id: "session", agent_id: "main", scope_id: "branch",
    sequence: 1, deadline: Date.now() + 10_000, action: "open", actor: "agent",
    launch: { command: "/bin/bash", args: ["-i"], cwd: "/project", env: {} },
  };
  const manager = createAgentTerminalManager({
    startupTimeoutMs: 30,
    spawn() {
      const pty = {
        pid: 123, writes: [],
        onData(fn) { this.emitData = fn; }, onExit(fn) { this.emitExit = fn; },
        write(data) { this.writes.push(data); }, resize() {},
      };
      pties.push(pty);
      return pty;
    },
    stop(pty, signal) { stops.push(signal); pty.emitExit({ exitCode: 0 }); },
    ...options,
  });
  t.after(() => manager.dispose());
  return { manager, pties, stops, host, plan,
    open: () => manager.dispatch(plan, host),
    call: (action, extra = {}) => manager.dispatch({ ...plan, sequence: 2, action, ...extra }, host),
  };
}

test("a destroyed host is rejected before spawning a terminal", async (t) => {
  const f = fixture(t); f.host.isDestroyed = () => true;
  assert.equal((await f.open()).ok, false);
  assert.equal(f.pties.length, 0);
});

test("a released host cannot reopen terminals with another ID", async (t) => {
  const f = fixture(t); await f.open(); f.manager.releaseHost(f.host);
  const next = { ...f.plan, terminal_id: "agent-terminal:" + "d".repeat(32) };
  assert.equal((await f.manager.dispatch(next, f.host)).ok, false);
  assert.equal(f.pties.length, 1);
  // Revocation belongs to the exact native host, not all windows.
  assert.equal((await f.manager.dispatch(next, { isDestroyed: () => false })).ok, true);
});

test("exit before PID readiness is not reported as an opened terminal", async (t) => {
  const f = fixture(t, { waitForPid(pty) { pty.emitExit({ exitCode: 7 }); return pty.pid; } });
  const result = await f.open();
  assert.equal(result.ok, false);
  assert.equal(result.status, "exited");
  assert.equal(result.exit_code, 7);
  assert.notEqual(result.phase, "opened");
});

for (const action of ["release", "dispose", "close"]) {
  test(`${action} settles startup even when the PID adapter never resolves`, async (t) => {
    const ready = deferred();
    const f = fixture(t, { startupTimeoutMs: 10_000, waitForPid: () => ready.promise });
    const opening = f.open();
    if (action === "release") f.manager.releaseHost(f.host);
    else if (action === "dispose") f.manager.dispose();
    else await f.call("close");
    const result = await bounded(opening);
    assert.equal(result.ok, false);
    assert.equal(result.status, "closed");
    ready.resolve(123);
    await Promise.resolve();
    assert.equal(f.stops.length, 1);
  });
}

test("a hung PID adapter times out, is aborted, and cannot revive the terminal", async (t) => {
  const ready = deferred(); let signal;
  const f = fixture(t, { waitForPid: (_pty, options) => { signal = options?.signal; return ready.promise; } });
  const result = await bounded(f.open());
  assert.equal(result.ok, false);
  assert.equal(signal.aborted, true);
  assert.equal(f.stops.length, 1);
  ready.resolve(123);
  await Promise.resolve();
  const read = await f.call("read");
  assert.equal(read.status, "closed");
  assert.equal((await f.call("input", { sequence: 3, data: "never\r" })).ok, false);
  assert.equal(f.pties[0].writes.length, 0);
});

test("the request deadline bounds startup even with a longer adapter budget", async (t) => {
  const f = fixture(t, { startupTimeoutMs: 10_000, waitForPid: () => new Promise(() => {}) });
  f.plan.deadline = Date.now() + 30;
  assert.equal((await bounded(f.open())).ok, false);
  assert.equal(f.stops.length, 1);
});

test("a failed stop remains unconfirmed rather than claiming the process exited", async (t) => {
  const f = fixture(t, { stop() {}, waitForPid: () => new Promise(() => {}) });
  const result = await bounded(f.open());
  assert.equal(result.ok, false);
  assert.equal(result.status, "stopping");
  assert.equal(result.exit_code, null);
  f.pties[0].emitExit({ exitCode: 9 });
  assert.equal((await f.call("read")).status, "closed");
});

for (const pid of [0, -1, 1.5, "123", NaN, Infinity]) {
  test(`a malformed ready PID is rejected: ${String(pid)}`, async (t) => {
    const f = fixture(t, { waitForPid: () => pid });
    assert.equal((await f.open()).ok, false);
    assert.equal(f.stops.length, 1);
  });
}

test("a rejected PID adapter is handled without an unhandled rejection", async (t) => {
  const f = fixture(t, { waitForPid: () => Promise.reject(new Error("not ready")) });
  assert.equal((await bounded(f.open())).ok, false);
  assert.equal(f.stops.length, 1);
});

test("pending startup counts toward the live-session limit", async (t) => {
  const ready = deferred();
  const f = fixture(t, { maxPerOwner: 1, waitForPid: () => ready.promise });
  const opening = f.open();
  const next = { ...f.plan, terminal_id: "agent-terminal:" + "d".repeat(32) };
  assert.equal((await f.manager.dispatch(next, f.host)).error, "terminal_limit");
  ready.resolve(123); assert.equal((await opening).ok, true);
  assert.equal(f.pties.length, 1);
});

test("invalid startup timeouts are rejected rather than disabling the bound", () => {
  for (const value of [0, -1, NaN, Infinity, "5000"]) {
    assert.throws(() => createAgentTerminalManager({ startupTimeoutMs: value }), /startupTimeoutMs/);
  }
});

test("exit during a write cannot change an exit receipt into a delivery receipt", async (t) => {
  const f = fixture(t); await f.open();
  f.pties[0].write = () => f.pties[0].emitExit({ exitCode: 0 });
  const result = await f.call("input", { data: "exit\r" });
  assert.equal(result.status, "exited");
  assert.deepEqual(result.last_operation, { action: "exit", phase: "confirmed" });
  assert.equal(result.command_complete, null);
});
