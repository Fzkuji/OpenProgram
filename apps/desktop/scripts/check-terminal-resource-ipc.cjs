"use strict";
const { test } = require("node:test");
const assert = require("node:assert/strict");
const { EventEmitter } = require("node:events");
const http = require("node:http");
const { registerTerminalResourceIpc, redeemTerminalTicket } = require("../terminal-resource-ipc");
function setup(t, overrides = {}) {
  const handlers = new Map(), listeners = new Map(), pties = [], sent = [], acknowledged = [];
  const app = new EventEmitter(); app.getPath = () => "/workspace";
  const sender = new EventEmitter(); sender.isDestroyed = () => false;
  sender.mainFrame = { url: "http://127.0.0.1:18100/chat" };
  sender.send = (channel, payload) => sent.push({ channel, payload });
  const ctx = { id: "main", win: { webContents: sender } };
  const event = { sender, senderFrame: sender.mainFrame }, plans = new Map();
  const manager = registerTerminalResourceIpc({
    ipcMain: { handle: (name, fn) => handlers.set(name, fn), on: (name, fn) => listeners.set(name, fn) },
    app, contextForSender: e => e.sender === sender ? ctx : null,
    origin: "http://127.0.0.1:18100", getOwnerToken: () => "owner-test-token",
    fileSystem: { realpathSync: p => p, statSync: p => ({ isDirectory: () => p !== "/file" }), existsSync: () => true },
    platform: "linux", env: { SHELL: "/bin/bash", EXISTING_ENV: "keep" },
    spawn(command, args, options) {
      const pty = new EventEmitter(); Object.assign(pty, { command, args, options, pid: 999 + pties.length, writes: [] });
      pty.onData = fn => pty.on("data", fn); pty.onExit = fn => pty.on("exit", fn);
      pty.write = data => pty.writes.push(data); pty.resize = (cols, rows) => { pty.size = [cols, rows]; };
      pty.kill = () => pty.emit("exit", { exitCode: 0 }); pties.push(pty); return pty;
    },
    kill(pid) { pties.find(pty => pty.pid === Math.abs(pid))?.kill(); }, listProcesses: () => "",
    acknowledge: async (...args) => { acknowledged.push(args); return { ok: true }; },
    redeem: async (_origin, _token, ticket, windowId) => {
      const plan = plans.get(ticket); plans.delete(ticket);
      if (!plan || plan.window_id !== windowId) throw Error("denied"); return plan;
    }, ...overrides,
  });
  t.after(() => app.emit("before-quit"));
  let counter = 0;
  const agent = (action, args = {}) => {
    const ticket = (++counter).toString(16).padStart(64, "0");
    plans.set(ticket, { action, owner_id: "exec-a", session_id: "chat-a", window_id: "main", deadline: Date.now() + 5000,
      operation_id: String(counter), result_ticket: ticket, ...args });
    return handlers.get("terminal:agent-ticket")(event, ticket);
  };
  const start = () => handlers.get("terminal:start")(event, { id: "terminal:main:shell", preset: "shell", cwd: "/workspace" });
  return { handlers, listeners, app, sender, ctx, event, manager, pties, sent, plans, agent, start, acknowledged };
}
test("IPC attaches the Agent to the user's exact PTY", async t => {
  const f = setup(t); const first = await f.start();
  f.listeners.get("terminal:write")(f.event, first.terminal_id, "export MY_VALUE=kept\r");
  assert.equal((await f.agent("list")).items.length, 1);
  const seen = await f.agent("observe", { terminal_id: first.terminal_id, generation: first.generation });
  const written = await f.agent("input", { ...seen, action: "input", data: "echo $MY_VALUE\r", expected_input_revision: seen.input_revision });
  assert.equal(written.ok, true); assert.equal(written.command_complete, null);
  assert.equal(f.pties.length, 1); assert.deepEqual(f.pties[0].writes, ["export MY_VALUE=kept\r", "echo $MY_VALUE\r"]);
  assert.equal(f.pties[0].options.env.EXISTING_ENV, "keep"); f.pties[0].emit("data", "kept\r\n");
  assert.equal(f.handlers.get("terminal:resource")(f.event, "read", { terminal_id: first.terminal_id, generation: first.generation, cursor: 0 }).data, "kept\r\n");
});
test("foreign sender, child frame and origin are rejected", async t => {
  const f = setup(t);
  for (const event of [{ sender: {} }, { sender: f.sender, senderFrame: { url: f.sender.mainFrame.url } }, { sender: f.sender, senderFrame: null }]) {
    assert.equal((await f.handlers.get("terminal:start")(event, {})).ok, false);
  }
  f.sender.mainFrame.url = "https://other.test";
  assert.equal((await f.start()).ok, false); assert.equal(f.pties.length, 0);
});
test("manual presets reject arbitrary programs and foreign IDs", async t => {
  const f = setup(t);
  assert.equal((await f.handlers.get("terminal:start")(f.event, { preset: "/bin/sh", id: "terminal:main:shell" })).ok, false);
  assert.equal((await f.handlers.get("terminal:start")(f.event, { preset: "shell", id: "terminal:other:shell" })).ok, false);
  assert.equal(f.pties.length, 0);
});
test("raw renderer plans cannot reach Agent dispatch", async t => {
  const f = setup(t);
  assert.equal((await f.handlers.get("terminal:agent-ticket")(f.event, { action: "open", command: "untrusted" })).ok, false);
  assert.equal((await f.handlers.get("terminal:agent-ticket")(f.event, "a".repeat(64))).ok, false); assert.equal(f.pties.length, 0);
});
test("host navigation during redemption cannot dispatch", async t => {
  let release; const f = setup(t, { redeem: () => new Promise(resolve => { release = resolve; }) });
  const pending = f.handlers.get("terminal:agent-ticket")(f.event, "a".repeat(64));
  f.sender.mainFrame = { url: "http://127.0.0.1:18100/chat" };
  release({ action: "open", terminal_id: "terminal-resource:" + "b".repeat(32), window_id: "main", owner_id: "a", session_id: "a", deadline: Date.now() + 1000 });
  assert.equal((await pending).ok, false); assert.equal(f.pties.length, 0);
});
test("resource views require the exact generation", async t => {
  const f = setup(t); const first = await f.start();
  for (const generation of [undefined, "stale"]) assert.equal(f.handlers.get("terminal:resource")(f.event, "input", { terminal_id: first.terminal_id, generation, data: "bad" }).ok, false);
  assert.equal(f.pties[0].writes.length, 0);
});
test("releasing access does not close the native resource", async t => {
  const f = setup(t); const first = await f.start();
  const seen = await f.agent("observe", { terminal_id: first.terminal_id, generation: first.generation });
  assert.equal((await f.agent("release", { ...seen, action: "release" })).ok, true);
  assert.equal(f.manager.human("list", {}, f.sender).items[0].status, "running");
  f.handlers.get("terminal:resource")(f.event, "close", { terminal_id: first.terminal_id, generation: first.generation });
  assert.equal(f.manager.human("list", {}, f.sender).items[0].status, "closed");
});
test("remount keeps the same PID, original cwd and buffered output", async t => {
  const f = setup(t); const first = await f.start(); f.pties[0].emit("data", "history");
  const attached = await f.handlers.get("terminal:start")(f.event, { id: first.terminal_id, preset: "shell", cwd: "/different", cols: 120, rows: 35 });
  assert.equal(attached.pid, first.pid); assert.equal(attached.start_cwd, "/workspace");
  assert.equal(f.pties.length, 1); assert.deepEqual(f.pties[0].size, [120, 35]); assert.equal(f.sent.at(-1).payload.data, "history");
});
test("redemption rejects remote origins and malformed tickets before networking", async () => {
  for (const args of [["https://remote.test", "token", "a".repeat(64)], ["http://127.0.0.1:18100", "", "a".repeat(64)], ["http://127.0.0.1:18100", "token", "bad"]]) {
    await assert.rejects(redeemTerminalTicket(...args, "main"));
  }
});
test("HTTP redemption uses a fixed path and rejects a foreign-window plan", async t => {
  const seen = [];
  const server = http.createServer((request, response) => {
    let data = ""; request.on("data", chunk => { data += chunk; });
    request.on("end", () => {
      seen.push({ url: request.url, authorization: request.headers.authorization, data: JSON.parse(data) });
      response.setHeader("content-type", "application/json");
      response.end(JSON.stringify({ ok: true, plan: { window_id: seen.length === 1 ? "main" : "other" } }));
    });
  });
  await new Promise(resolve => server.listen(0, "127.0.0.1", resolve)); t.after(() => server.close());
  const origin = `http://127.0.0.1:${server.address().port}`;
  assert.equal((await redeemTerminalTicket(origin, "test-secret", "a".repeat(64), "main")).window_id, "main");
  assert.equal(seen[0].url, "/api/terminal/claim"); assert.equal(seen[0].authorization, "Bearer test-secret");
  await assert.rejects(redeemTerminalTicket(origin, "test-secret", "b".repeat(64), "main"));
});


test("native acknowledgements carry the actual result, not a filtered Web reply", async t => {
  const f = setup(t); await f.start();
  const result = await f.agent("list");
  assert.equal(result.ok, true); assert.equal(f.acknowledged.length, 1);
  assert.equal(f.acknowledged[0][3], "main");
  assert.deepEqual(f.acknowledged[0][4], result);
});

test("lost acknowledgement never replays a delivered input", async t => {
  let failAck = false;
  const f = setup(t, { acknowledge: async () => { if (failAck) throw Error("lost"); return { ok: true }; } });
  const first = await f.start();
  const seen = await f.agent("observe", { terminal_id: first.terminal_id, generation: first.generation });
  failAck = true;
  const result = await f.agent("input", { ...seen, action: "input", data: "one\r", expected_input_revision: seen.input_revision });
  assert.equal(result.result_unconfirmed, true); assert.deepEqual(f.pties[0].writes, ["one\r"]);
});

test("closing a root process also terminates descendants that ignore SIGTERM", {
  skip: process.platform === "win32", timeout: 8000,
}, async t => {
  const { spawn, execFileSync } = require("node:child_process");
  const { tmpdir } = require("node:os");
  const { setTimeout: delay } = require("node:timers/promises");
  let parent, childPid;
  const handlers = new Map(), app = new EventEmitter(), sender = new EventEmitter();
  app.getPath = () => tmpdir(); sender.isDestroyed = () => false; sender.send = () => {};
  sender.mainFrame = { url: "http://127.0.0.1:18100/chat" };
  const event = { sender, senderFrame: sender.mainFrame };
  const ctx = { id: "main", win: { webContents: sender } };
  let childReady;
  const ready = new Promise(resolve => { childReady = resolve; });
  const childCode = "process.on('SIGTERM',()=>{}); console.log(process.pid); setInterval(()=>{},1000)";
  const parentCode = `const {spawn}=require('node:child_process'); const c=spawn(process.execPath,['-e',${JSON.stringify(childCode)}],{stdio:['ignore','pipe','ignore']}); c.stdout.pipe(process.stdout); setInterval(()=>{},1000);`;
  t.after(async () => {
    if (childPid) { try { process.kill(childPid, "SIGKILL"); } catch {} }
    if (parent) {
      try { process.kill(-parent.pid, "SIGKILL"); } catch {}
      if (parent.exitCode === null && parent.signalCode === null) {
        await new Promise(resolve => parent.once("exit", resolve));
      }
    }
    app.emit("before-quit");
  });
  registerTerminalResourceIpc({
    ipcMain: { handle: (name, fn) => handlers.set(name, fn), on() {} }, app,
    contextForSender: () => ctx, origin: "http://127.0.0.1:18100", getOwnerToken: () => "",
    spawn() {
      parent = spawn(process.execPath, ["-e", parentCode], { detached: true, stdio: ["ignore", "pipe", "ignore"] });
      parent.stdout.once("data", data => { childPid = Number(String(data).trim()); childReady(); });
      return { pid: parent.pid, onData() {}, onExit: fn => parent.once("exit", exitCode => fn({ exitCode })),
        resize() {}, kill: signal => parent.kill(signal) };
    },
  });
  const opened = await handlers.get("terminal:start")(event, { id: "terminal:main:shell", preset: "shell", cwd: tmpdir() });
  assert.equal(opened.ok, true);
  await ready;
  assert.ok(Number.isSafeInteger(childPid) && childPid > 0);
  const exited = new Promise(resolve => parent.once("exit", resolve));
  handlers.get("terminal:resource")(event, "close", { terminal_id: opened.terminal_id, generation: opened.generation });
  await exited;
  const running = () => {
    try { return !/^\s*Z/.test(execFileSync("/bin/ps", ["-p", String(childPid), "-o", "stat="], { encoding: "utf8" })); }
    catch { return false; }
  };
  const deadline = Date.now() + 2000;
  while (running() && Date.now() < deadline) await delay(20);
  assert.equal(running(), false, "root exit must not abandon the captured descendant");
  assert.equal(handlers.get("terminal:resource")(event, "list").items[0].status, "closed");
});
