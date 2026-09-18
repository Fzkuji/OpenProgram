"use strict";
const { test } = require("node:test");
const assert = require("node:assert/strict");
const { createTerminalResourceManager, MAX_OUTPUT, MAX_INPUT } = require("../terminal-resource-manager");
function deferred() { let resolve; return { promise: new Promise(r => { resolve = r; }), resolve: v => resolve(v) }; }
async function bounded(promise) {
  let timer;
  try { return await Promise.race([promise, new Promise((_, reject) => { timer = setTimeout(() => reject(Error("did not settle")), 500); })]); }
  finally { clearTimeout(timer); }
}
function fixture(t, options = {}) {
  let time = 1000;
  const host = { isDestroyed: () => false }, other = { isDestroyed: () => false };
  const pties = [], events = [], stops = [];
  const manager = createTerminalResourceManager({ now: () => time, startupTimeoutMs: 30,
    resolveLaunch: r => ({ command: "/bin/bash", args: ["-i"], cwd: r.cwd || "/project", env: { TERM: "xterm-256color" } }),
    spawn(command, args, opts) {
      const pty = { pid: 100 + pties.length, opts, writes: [], sizes: [],
        onData(fn) { this.output = fn; }, onExit(fn) { this.exit = fn; },
        write(data) { this.writes.push(data); }, resize(c, r) { this.sizes.push([c, r]); } };
      pties.push(pty); return pty;
    },
    stop(pty, signal) { stops.push(signal); pty.exit({ exitCode: 0 }); },
    emit: (who, kind, value) => events.push({ who, kind, value }), ...options,
  });
  let op = 0;
  const plan = { owner_id: "exec-A", session_id: "chat-A", deadline: 100000 };
  const agent = (action, rest = {}, who = host) => manager.agent({ ...plan, action, operation_id: `op-${++op}`, ...rest }, who);
  const open = (extra = {}, who = host) => manager.open({ id: "terminal:main:shell", preset: "shell", shared: true, ...extra }, who);
  const binding = r => ({ terminal_id: r.terminal_id, generation: r.generation, binding_id: r.binding_id, expected_input_revision: r.input_revision });
  t.after(() => manager.dispose());
  return { manager, host, other, pties, events, stops, plan, agent, open, binding, setTime: v => { time = v; } };
}
test("Agent adopts a human-created terminal without spawning or changing its environment", async t => {
  const f = fixture(t); const r = await f.open(); const seen = await f.agent("observe", f.binding(r));
  assert.equal(seen.ok, true); assert.equal(seen.pid, r.pid);
  assert.equal((await f.agent("input", { ...f.binding(seen), data: "pwd\r" })).ok, true);
  assert.equal(f.pties.length, 1); assert.equal(f.pties[0].opts.cwd, "/project");
});
test("Agent-created and human-created terminals use the same registry and view", async t => {
  const f = fixture(t); const r = await f.agent("open", { terminal_id: "terminal-resource:" + "a".repeat(32) });
  f.pties[0].output("one"); const viewed = f.manager.human("read", f.binding(r), f.host);
  assert.equal(viewed.data, "one"); assert.equal(viewed.pid, r.pid); assert.equal(f.pties.length, 1);
});
test("private terminals remain in their conversation until explicitly shared", async t => {
  const f = fixture(t); const r = await f.agent("open", { terminal_id: "terminal-resource:" + "a".repeat(32) });
  const other = { owner_id: "exec-B", session_id: "chat-B" };
  assert.equal((await f.agent("list", other)).items.length, 0);
  assert.equal((await f.agent("observe", { ...f.binding(r), ...other })).ok, false);
  assert.equal((await f.agent("release", f.binding(r))).ok, true);
  assert.equal(f.manager.human("share", { ...f.binding(r), shared: true }, f.host).ok, true);
  assert.equal((await f.agent("observe", { ...f.binding(r), ...other })).ok, true); assert.equal(f.pties.length, 1);
});
test("one temporary Agent lease does not permanently bind the creator", async t => {
  const f = fixture(t); const r = await f.open(); const seen = await f.agent("observe", f.binding(r));
  assert.equal((await f.agent("observe", { ...f.binding(r), owner_id: "exec-B" })).error, "terminal_in_use");
  assert.equal((await f.agent("release", f.binding(seen))).phase, "released");
  const next = await f.agent("observe", { ...f.binding(r), owner_id: "exec-B", session_id: "chat-B" });
  assert.equal(next.ok, true); assert.equal(next.pid, seen.pid); assert.equal(f.stops.length, 0);
});
test("lease expiry releases access but never kills a shell or loses output", async t => {
  const f = fixture(t, { leaseMs: 100 }); const r = await f.open(); await f.agent("observe", f.binding(r));
  f.pties[0].output("still running"); f.setTime(2000); f.manager.sweep();
  const next = await f.agent("observe", { ...f.binding(r), owner_id: "exec-B" });
  assert.equal(next.ok, true); assert.equal(next.data, "still running"); assert.equal(f.stops.length, 0);
});
test("view polling does not acquire, renew or reorder control", async t => {
  const f = fixture(t, { leaseMs: 100 }); const r = await f.open(); const seen = await f.agent("observe", f.binding(r));
  f.setTime(1099); f.manager.human("read", f.binding(r), f.host);
  assert.equal((await f.agent("input", { ...f.binding(seen), data: "echo ok\r" })).ok, true);
  f.setTime(1300); f.manager.human("read", f.binding(r), f.host);
  assert.equal((await f.agent("observe", { ...f.binding(r), owner_id: "exec-B" })).ok, true);
});
test("human input invalidates a stale Agent observation without pausing", async t => {
  const f = fixture(t); const r = await f.open(); const seen = await f.agent("observe", f.binding(r));
  f.manager.human("input", { ...f.binding(r), data: "pwd\r" }, f.host);
  assert.equal((await f.agent("input", { ...f.binding(seen), data: "ls\r" })).error, "observe_required");
  const next = await f.agent("observe", f.binding(r));
  assert.equal((await f.agent("input", { ...f.binding(next), data: "ls\r" })).ok, true); assert.equal(f.stops.length, 0);
});
for (const action of ["input", "interrupt", "close"]) test(`partial human input fences ${action}`, async t => {
  const f = fixture(t); const r = await f.open(); await f.agent("observe", f.binding(r));
  f.manager.human("input", { ...f.binding(r), data: "git commit -m '" }, f.host);
  const next = await f.agent("observe", f.binding(r));
  assert.equal((await f.agent(action, { ...f.binding(next), data: "not allowed\r" })).error, "human_input_pending");
  assert.equal(f.stops.length, 0); assert.equal(f.pties[0].writes.length, 1);
});
test("human newline or interrupt clears the partial-input guard", async t => {
  const f = fixture(t); const r = await f.open();
  for (const boundary of ["\r", "\n", "\x03"]) {
    f.manager.human("input", { ...f.binding(r), data: "partial" }, f.host);
    assert.equal(f.manager.human("read", f.binding(r), f.host).human_input_pending, true);
    f.manager.human("input", { ...f.binding(r), data: boundary }, f.host);
    assert.equal(f.manager.human("read", f.binding(r), f.host).human_input_pending, false);
  }
});
for (const action of ["observe", "input", "close", "release"]) test(`another window cannot ${action} a guessed ID`, async t => {
  const f = fixture(t); const r = await f.open(); const seen = await f.agent("observe", f.binding(r));
  assert.equal((await f.agent(action, { ...f.binding(seen), data: "bad\r" }, f.other)).ok, false);
});
test("old generations cannot operate replacements", async t => {
  const f = fixture(t); const r = await f.open(); const seen = await f.agent("observe", f.binding(r));
  f.manager.human("close", f.binding(r), f.host); const next = await f.open();
  assert.notEqual(next.generation, r.generation);
  assert.equal((await f.agent("input", { ...f.binding(seen), data: "bad\r" })).ok, false);
  assert.equal(f.manager.human("read", f.binding(r), f.host).ok, false); assert.equal(f.pties[1].writes.length, 0);
});
test("independent cursors never consume another reader's output", async t => {
  const f = fixture(t); const r = await f.open(); f.pties[0].output("hello world");
  const a = await f.agent("observe", { ...f.binding(r), cursor: 0 });
  const b = f.manager.human("read", { ...f.binding(r), cursor: 0 }, f.host);
  assert.equal(a.data, b.data); assert.equal(f.manager.human("read", { ...f.binding(r), cursor: 6 }, f.host).data, "world");
});
test("bounded output reports gaps and preserves surrogate pairs", async t => {
  const f = fixture(t); const r = await f.open(); f.pties[0].output("x".repeat(MAX_OUTPUT + 1));
  let out = f.manager.human("read", { ...f.binding(r), cursor: 0 }, f.host);
  assert.equal(out.truncated, true); assert.equal(out.has_more, true); assert.ok(out.data.length <= 24000);
  f.pties[0].output("😀"); out = f.manager.human("read", { ...f.binding(r), cursor: MAX_OUTPUT + 1 }, f.host);
  assert.equal(out.data, "😀");
  assert.equal(f.manager.human("read", { ...f.binding(r), cursor: Number.MAX_SAFE_INTEGER }, f.host).ok, false);
});
test("delivery is not completion and duplicate input is not replayed", async t => {
  const f = fixture(t); const r = await f.open(); const seen = await f.agent("observe", f.binding(r));
  const args = { ...f.binding(seen), data: "sleep 100\r", operation_id: "same" };
  const a = await f.agent("input", args); const b = await f.agent("input", args);
  assert.equal(a.command_complete, null); assert.equal(a.exit_code, null); assert.equal(b.receipt_replayed, true);
  assert.equal(f.pties[0].writes.length, 1);
  assert.equal((await f.agent("input", { ...args, data: "different" })).error, "conflicting_operation");
});
test("partial delivery errors are retained and not retried", async t => {
  const f = fixture(t); const r = await f.open(); const seen = await f.agent("observe", f.binding(r)); let writes = 0;
  f.pties[0].write = () => { writes++; throw Error("partial"); };
  const args = { ...f.binding(seen), data: "echo x\r", operation_id: "same" };
  assert.equal((await f.agent("input", args)).error, "operation_unconfirmed");
  assert.equal((await f.agent("input", args)).error, "operation_unconfirmed"); assert.equal(writes, 1);
});
test("release requires a binding but does not kill the shell", async t => {
  const f = fixture(t); const r = await f.open(); const seen = await f.agent("observe", f.binding(r));
  assert.equal((await f.agent("release", { ...f.binding(seen), binding_id: "wrong" })).ok, false);
  assert.equal((await f.agent("release", f.binding(seen))).status, "running"); assert.equal(f.stops.length, 0);
});
test("revoking sharing invalidates access without closing", async t => {
  const f = fixture(t); const r = await f.open(); const seen = await f.agent("observe", f.binding(r));
  assert.equal(f.manager.human("share", { ...f.binding(r), shared: false, session_id: "chat-B" }, f.host).ok, true);
  assert.equal((await f.agent("input", { ...f.binding(seen), data: "bad\r" })).ok, false); assert.equal(f.stops.length, 0);
});
test("close is unconfirmed until exit and idempotent afterwards", async t => {
  const f = fixture(t, { stop() {} }); const r = await f.open();
  assert.equal(f.manager.human("close", f.binding(r), f.host).phase, "close_requested");
  f.pties[0].exit({ exitCode: 9 });
  assert.equal(f.manager.human("read", f.binding(r), f.host).exit_code, 9);
  assert.equal(f.manager.human("close", f.binding(r), f.host).phase, "closed");
});
for (const kind of ["close", "releaseHost", "dispose", "exit"]) test(`${kind} cancels startup and late readiness`, async t => {
  const pending = deferred(); const f = fixture(t, { startupTimeoutMs: 10000, waitForPid: () => pending.promise });
  const opening = f.open();
  if (kind === "close") f.manager.human("close", { terminal_id: "terminal:main:shell" }, f.host);
  if (kind === "releaseHost") f.manager.releaseHost(f.host);
  if (kind === "dispose") f.manager.dispose();
  if (kind === "exit") f.pties[0].exit({ exitCode: 7 });
  assert.equal((await bounded(opening)).ok, false); pending.resolve(100); await Promise.resolve();
});
test("hung startup times out without inventing a confirmed exit", async t => {
  const f = fixture(t, { waitForPid: () => new Promise(() => {}), stop() {} });
  const r = await bounded(f.open()); assert.equal(r.ok, false); assert.equal(r.status, "stopping"); assert.equal(r.exit_code, null);
  f.pties[0].exit({ exitCode: 1 });
});
for (const pid of [0, -1, 1.5, "123", NaN, Infinity]) test(`invalid readiness PID ${String(pid)}`, async t => {
  const f = fixture(t, { waitForPid: () => pid }); assert.equal((await f.open()).ok, false); assert.equal(f.stops.length, 1);
});
test("destroyed and released hosts cannot spawn", async t => {
  const f = fixture(t); f.host.isDestroyed = () => true; assert.equal((await f.open()).ok, false);
  f.host.isDestroyed = () => false; f.manager.releaseHost(f.host); assert.equal((await f.open()).ok, false); assert.equal(f.pties.length, 0);
});
test("pending startups count toward limits and host release is isolated", async t => {
  const pending = deferred(); const f = fixture(t, { maxSessions: 1, waitForPid: () => pending.promise });
  const opening = f.open(); assert.equal((await f.open({ id: "other" }, f.other)).error, "terminal_limit");
  f.manager.releaseHost(f.other); pending.resolve(100); assert.equal((await opening).ok, true);
});
test("write-triggered exit keeps the newer exit receipt", async t => {
  const f = fixture(t); const r = await f.open(); const seen = await f.agent("observe", f.binding(r));
  f.pties[0].write = () => f.pties[0].exit({ exitCode: 0 });
  const result = await f.agent("input", { ...f.binding(seen), data: "exit\r" });
  assert.equal(result.status, "exited"); assert.equal(result.last_operation.phase, "confirmed");
});
test("oversized Agent input is rejected rather than truncated", async t => {
  const f = fixture(t); const r = await f.open(); const seen = await f.agent("observe", f.binding(r));
  assert.equal((await f.agent("input", { ...f.binding(seen), data: "x".repeat(MAX_INPUT + 1) })).error, "invalid_input");
  assert.equal(f.pties[0].writes.length, 0);
});
test("expired plans do not spawn", async t => {
  const f = fixture(t); f.setTime(100000);
  assert.equal((await f.agent("open", { terminal_id: "terminal-resource:" + "a".repeat(32) })).ok, false); assert.equal(f.pties.length, 0);
});
test("invalid configuration cannot disable limits", () => {
  for (const key of ["startupTimeoutMs", "leaseMs", "maxSessions", "maxHistory"]) for (const value of [0, -1, NaN, Infinity, "5000"]) {
    assert.throws(() => createTerminalResourceManager({ [key]: value }), new RegExp(key));
  }
});
test("restart waits for prior exit before replacing generation", async t => {
  const f = fixture(t, { stop() {} }); const first = await f.open();
  f.manager.human("close", f.binding(first), f.host); const restarting = f.open();
  assert.equal(f.pties.length, 1); f.pties[0].exit({ exitCode: 0 }); const next = await bounded(restarting);
  assert.equal(next.ok, true); assert.equal(f.pties.length, 2); assert.notEqual(next.generation, first.generation);
});
test("unconfirmed close prevents a competing shell on restart", async t => {
  const f = fixture(t, { stop() {} }); const first = await f.open();
  f.manager.human("close", f.binding(first), f.host);
  assert.equal((await bounded(f.open())).error, "terminal_busy"); assert.equal(f.pties.length, 1);
});
test("retiring a task releases its binding not its resource", async t => {
  const f = fixture(t); const r = await f.open(); const lease = await f.agent("observe", f.binding(r));
  const next = await f.agent("observe", { ...f.binding(r), owner_id: "exec-B", retired_owners: ["exec-A"] });
  assert.equal(next.ok, true); assert.equal(f.pties.length, 1); assert.equal(f.stops.length, 0);
  assert.equal((await f.agent("input", { ...f.binding(lease), data: "old\r" })).error, "binding_not_found");
});
test("Agent resize and sharing use the observed instance and lease", async t => {
  const f = fixture(t); const opened = await f.agent("open", { terminal_id: "terminal-resource:" + "b".repeat(32) });
  const request = f.binding(opened);
  assert.equal((await f.agent("resize", { ...request, cols: 120, rows: 40 })).ok, true);
  assert.deepEqual(f.pties[0].sizes.at(-1), [120, 40]);
  assert.equal((await f.agent("share", { ...request, shared: true })).ok, true);
  assert.equal((await f.agent("list", { session_id: "chat-B" })).items.length, 1);
  assert.equal((await f.agent("share", { ...request, binding_id: "wrong", shared: false })).ok, false);
});
