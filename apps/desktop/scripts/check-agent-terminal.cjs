const { test } = require('node:test');
const assert = require('node:assert/strict');
const { createAgentTerminalManager, MAX_OUTPUT } = require('../agent-terminal-manager');

function fixture(options = {}) {
  let time = 1000;
  const pties = [];
  const host = { isDestroyed: () => false };
  const base = {
    terminal_id: 'agent-terminal:' + 'a'.repeat(32), capability: 'b'.repeat(64),
    generation: 'c'.repeat(32), session_id: 'session', agent_id: 'main', scope_id: 'branch',
    sequence: 1, deadline: 90000, action: 'open', actor: 'agent',
    launch: { command: '/bin/bash', args: ['--noprofile', '--norc', '-i'], cwd: '/project', env: { TERM: 'xterm-256color' } },
  };
  const manager = createAgentTerminalManager({
    now: () => time,
    spawn(command, args, opts) {
      const pty = { pid: 100 + pties.length, writes: [], sizes: [], opts,
        write(data) { this.writes.push(data); }, resize(c, r) { this.sizes.push([c, r]); },
        onData(cb) { this.data = cb; }, onExit(cb) { this.exit = cb; },
      };
      pties.push(pty);
      return pty;
    },
    stop(pty) { pty.exit({ exitCode: 0 }); }, ...options,
  });
  let sequence = 1;
  return { manager, host, base, pties, setTime: (n) => { time = n; },
    open: () => manager.dispatch(base, host),
    call: (action, rest = {}, who = host) => manager.dispatch({ ...base, action, sequence: ++sequence, ...rest }, who),
  };
}

test('opens a separate PTY with only the authorized launch environment', async () => {
  const f = fixture();
  assert.equal((await f.open()).ok, true);
  assert.deepEqual(f.pties[0].opts.env, { TERM: 'xterm-256color' });
  assert.equal(f.pties[0].opts.cwd, '/project');
  await f.call('input', { data: 'cd /tmp\r' });
  await f.call('input', { data: 'pwd\r' });
  assert.equal(f.pties.length, 1);
  assert.deepEqual(f.pties[0].writes, ['cd /tmp\r', 'pwd\r']);
  f.manager.dispose();
});
test('manual terminal IDs are never admitted', async () => {
  const f = fixture();
  assert.equal((await f.manager.dispatch({ ...f.base, terminal_id: 'terminal:main:shell' }, f.host)).ok, false);
  assert.equal(f.pties.length, 0);
});
for (const change of [{ capability: 'd'.repeat(64) }, { session_id: 'other' }, { agent_id: 'child' }, { scope_id: 'other-branch' }, { generation: 'd'.repeat(32) }]) {
  test('rejects a mismatched terminal owner ' + Object.keys(change)[0], async () => {
    const f = fixture(); await f.open();
    assert.equal((await f.call('input', { data: 'bad\r', ...change })).ok, false);
    assert.equal(f.pties[0].writes.length, 0); f.manager.dispose();
  });
}
test('a different native window cannot read a guessed handle', async () => {
  const f = fixture(); await f.open();
  assert.equal((await f.call('read', {}, {})).ok, false); f.manager.dispose();
});
test('delivery receipts do not invent a command exit status', async () => {
  const f = fixture(); await f.open();
  const result = await f.call('input', { data: 'sleep 100\r' });
  assert.equal(result.phase, 'delivered'); assert.equal(result.command_complete, null);
  assert.equal(result.exit_code, null); f.manager.dispose();
});
test('deduplicates input and rejects conflicting or stale operations', async () => {
  const f = fixture(); await f.open();
  const plan = { ...f.base, action: 'input', sequence: 2, data: 'echo hi\r' };
  const first = await f.manager.dispatch(plan, f.host);
  assert.deepEqual(await f.manager.dispatch(plan, f.host), first);
  assert.equal(f.pties[0].writes.length, 1);
  assert.equal((await f.manager.dispatch({ ...plan, data: 'different' }, f.host)).ok, false);
  assert.equal((await f.manager.dispatch({ ...plan, sequence: 1 }, f.host)).ok, false);
  f.manager.dispose();
});
test('input that throws remains unconfirmed and is not written twice', async () => {
  const f = fixture(); await f.open(); let attempts = 0;
  f.pties[0].write = () => { attempts++; throw Error('partial delivery'); };
  const plan = { ...f.base, action: 'input', sequence: 2, data: 'run\r' };
  assert.equal((await f.manager.dispatch(plan, f.host)).error, 'operation_unconfirmed');
  assert.equal((await f.manager.dispatch(plan, f.host)).error, 'operation_unconfirmed');
  assert.equal(attempts, 1); f.manager.dispose();
});
test('human takeover fences Agent input without killing the foreground program', async () => {
  const f = fixture(); await f.open();
  assert.equal((await f.call('pause', { actor: 'human' })).controller, 'human');
  assert.equal((await f.call('input', { data: 'blocked\r' })).ok, false);
  assert.equal((await f.call('input', { data: 'yes\r', actor: 'human' })).ok, true);
  assert.equal((await f.call('resume')).ok, false);
  assert.equal((await f.call('resume', { actor: 'human' })).ok, true);
  assert.equal((await f.call('input', { data: 'allowed\r' })).ok, true);
  assert.equal(f.pties[0].writes.length, 2); f.manager.dispose();
});
test('reads are cursor-based and bounded; discarded output is reported', async () => {
  const f = fixture(); await f.open();
  f.pties[0].data('hello'); let r = await f.call('read');
  assert.equal(r.data, 'hello'); assert.equal(r.next_cursor, 5);
  f.pties[0].data(' world'); r = await f.call('read', { cursor: 5 }); assert.equal(r.data, ' world');
  assert.equal((await f.call('read', { cursor: 999999999 })).ok, false);
  f.pties[0].data('x'.repeat(MAX_OUTPUT + 10)); r = await f.call('read', { cursor: 0 });
  assert.equal(r.truncated, true); assert.equal(r.has_more, true); assert.ok(r.data.length <= 24000);
  f.manager.dispose();
});
test('interrupt and resize use the same PTY; interrupts are not closes', async () => {
  const f = fixture(); await f.open();
  assert.equal((await f.call('interrupt')).ok, true);
  assert.deepEqual(f.pties[0].writes, ['\x03']);
  assert.equal((await f.call('resize', { cols: 120, rows: 40 })).ok, true);
  assert.deepEqual(f.pties[0].sizes, [[120, 40]]); f.manager.dispose();
});
test('close remains unconfirmed until the PTY exit event', async () => {
  const f = fixture({ stop() {} }); await f.open();
  assert.equal((await f.call('close')).phase, 'close_requested');
  assert.equal((await f.call('input', { data: 'blocked' })).ok, false);
  f.pties[0].exit({ exitCode: 9 });
  const r = await f.call('read'); assert.equal(r.status, 'closed'); assert.equal(r.exit_code, 9);
  f.manager.dispose();
});
test('expired requests never launch or write', async () => {
  const f = fixture(); f.setTime(100000); assert.equal((await f.open()).ok, false);
  assert.equal(f.pties.length, 0);
});
test('expired idle sessions are stopped; window teardown does not affect another host', async () => {
  const f = fixture({ leaseMs: 100 }); await f.open();
  f.manager.releaseHost({}); assert.equal((await f.call('read')).status, 'running');
  f.setTime(2000); f.manager.sweep(); assert.equal((await f.call('read')).status, 'closed');
  f.manager.dispose();
});
test('maximum input size and session limits are enforced', async () => {
  const f = fixture({ maxPerOwner: 1 }); await f.open();
  assert.equal((await f.call('input', { data: 'x'.repeat(16385) })).ok, false);
  assert.equal((await f.manager.dispatch({ ...f.base, terminal_id: 'agent-terminal:' + 'd'.repeat(32) }, f.host)).error, 'terminal_limit');
  f.manager.dispose();
});
