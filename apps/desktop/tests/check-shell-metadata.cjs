'use strict';
const { test } = require('node:test');
const assert = require('node:assert/strict');
const { createTerminalResourceManager } = require('../main/terminal/terminal-resource-manager');
const nonce = 'a'.repeat(32);
const frame = (seq, kind, cycle = 0, status = '', cwd = '/tmp') => `\x1b]777;OpenProgram;1;${nonce};123;${seq};${kind};${cycle};${status};${Buffer.from(kind === 'start' ? '' : cwd).toString('hex')}\x07`;
function fixture(t, options = {}) { let p, cleanup = 0; const host = {}; const manager = createTerminalResourceManager({ resolveLaunch: () => ({ command: '/bin/zsh', args: ['-l'], cwd: '/start', env: {}, integration: { nonce, mode: 'zsh-hooks', cleanup() { cleanup++; } } }), spawn() { return p = { pid: 123, onData(f) { this.data = f; }, onExit(f) { this.exit = f; }, write() { }, resize() { } }; }, stop() { p.exit({ exitCode: 0 }); }, ...options }); t.after(() => manager.dispose()); return { manager, host, get p() { return p; }, get cleanup() { return cleanup; }, open: () => manager.open({ id: 't', preset: 'shell', shared: true }, host), read: () => manager.human('read', { terminal_id: 't', cursor: 0 }, host) }; }
test('manager observes shell cycles independently of delivered input and PTY exit', async (t) => { const f = fixture(t); await f.open(); assert.equal(f.read().shell_integration.status, 'starting'); f.p.data('before' + frame(1, 'ready') + 'after'); assert.equal(f.read().data, 'beforeafter'); assert.equal(f.read().shell_integration.cwd.value, '/tmp'); assert.equal(f.read().start_cwd, '/start'); f.p.data(frame(2, 'start', 1)); const receipt = f.manager.human('input', { terminal_id: 't', data: 'false\r' }, f.host); assert.equal(receipt.command_complete, null); assert.equal(receipt.phase, 'delivered'); f.p.data(frame(3, 'end', 1, 1, '/tmp/目录 x')); assert.equal(f.read().shell_integration.cycle.exit_code, 1); assert.equal(f.read().exit_code, null); assert.equal(f.cleanup, 0); f.p.exit({ exitCode: 7 }); assert.equal(f.cleanup, 1); assert.equal(f.read().shell_integration.status, 'ended'); assert.equal(f.read().exit_code, 7); });
test('every frame split is accepted; wrong identity and replay remain output', async (t) => { const f = fixture(t); await f.open(); let seq = 1; const initial = frame(seq++, 'ready'); for (const c of initial)
    f.p.data(c); for (let i = 1; i < frame(2, 'start', 1).length; i++) {
    const s = frame(seq++, 'start', i), e = frame(seq++, 'end', i, 0);
    f.p.data(s.slice(0, i));
    f.p.data(s.slice(i));
    f.p.data(e);
} const before = f.read(); assert.equal(before.data, ''); assert.equal(before.shell_integration.cycle.phase, 'finished'); const replay = frame(1, 'ready'), foreign = frame(seq, 'ready').replace(nonce, 'b'.repeat(32)); f.p.data(replay + foreign); assert.equal(f.read().data, replay + foreign); assert.equal(f.read().shell_integration.event_sequence, before.shell_integration.event_sequence); });
test('unmatched completion cannot fabricate success and oversized frames are bounded', async (t) => { const f = fixture(t); await f.open(); f.p.data(frame(1, 'ready')); const bad = frame(2, 'end', 5, 0); f.p.data(bad); assert.equal(f.read().shell_integration.cycle, null); assert.equal(f.read().data, bad); f.p.data('\x1b]777;OpenProgram;' + 'x'.repeat(40000)); assert.ok(f.read().output_end > 40000); });
test('incomplete framing expires into the same replay stream and exit flushes tail', async (t) => { const callbacks = new Map(); let id = 0, p; const host = {}; const manager = createTerminalResourceManager({ timers: { setTimeout(fn, ms) { const key = ++id; callbacks.set(key, { fn, ms }); return key; }, clearTimeout(key) { callbacks.delete(key); } }, resolveLaunch: () => ({ command: '/bin/zsh', args: [], cwd: '/tmp', env: {}, integration: { nonce, mode: 'zsh-hooks', cleanup() { } } }), spawn() { return p = { pid: 123, onData(f) { this.data = f; }, onExit(f) { this.exit = f; } }; }, stop() { p.exit({ exitCode: 0 }); } }); t.after(() => manager.dispose()); await manager.open({ id: 't', preset: 'shell' }, host); const read = () => manager.human('read', { terminal_id: 't', cursor: 0 }, host); p.data('x\x1b]777;OpenProgram;1;'); assert.equal(read().data, 'x'); [...callbacks.values()].find(x => x.ms === 1000).fn(); assert.equal(read().data, 'x\x1b]777;OpenProgram;1;'); p.data('tail\x1b]'); p.exit({ exitCode: 0 }); assert.ok(read().data.endsWith('tail\x1b]')); assert.equal(read().shell_integration.status, 'ended'); });
test('old PTY frames after confirmed exit cannot update a replacement generation', async (t) => { const f = fixture(t); const first = await f.open(), old = f.p; old.data(frame(1, 'ready')); old.data(frame(2, 'start', 1)); old.exit({ exitCode: 0 }); assert.equal(f.read().shell_integration.cycle.phase, 'unknown'); const second = await f.open(); assert.notEqual(second.generation, first.generation); old.data(frame(3, 'end', 1, 0)); assert.equal(f.read().shell_integration.status, 'starting'); assert.equal(f.read().shell_integration.cycle, null); });
test('spawn failure cleans private launch assets without fabricating a terminal', async () => { let cleanup = 0; const manager = createTerminalResourceManager({ resolveLaunch: () => ({ integration: { cleanup() { cleanup++; } } }), spawn() { throw Error('spawn failed'); }, stop() { } }); const result = await manager.open({ id: 't', preset: 'shell' }, {}); assert.equal(result.error, 'terminal_start_failed'); assert.equal(cleanup, 1); manager.dispose(); });
test('launch preparation has explicit packaged assets, private permissions and conflict fallback', () => { const fs = require('node:fs'), path = require('node:path'); const { prepareShellIntegration, ASSETS } = require('../main/terminal/terminal-shell-integration'); const packaged = require('./packaged-files').packagedFiles(); for (const name of ASSETS)
    assert.ok(packaged.has('main/terminal/shell-integration/' + name)); const launch = { command: '/bin/bash', args: ['-l'], cwd: '/tmp', env: { HOME: '/synthetic' } }; const supported = prepareShellIntegration(launch, { preset: 'shell', platform: 'darwin' }); assert.ok(supported.integration); const dir = supported.env.OP_SHELL_DIR; assert.ok(fs.existsSync(path.join(dir, 'hooks.bash'))); if (process.platform !== 'win32') {
    assert.equal(fs.statSync(dir).mode & 0o777, 0o700);
    assert.equal(fs.statSync(path.join(dir, 'hooks.bash')).mode & 0o777, 0o600);
} supported.integration.cleanup(); supported.integration.cleanup(); assert.ok(!fs.existsSync(dir)); const prior = { ...launch, env: { ...launch.env, PROMPT_COMMAND: 'custom' } }; const result = prepareShellIntegration(prior, { preset: 'shell', platform: 'darwin' }); assert.equal(result.env, prior.env); assert.equal(result.integrationReason, 'existing_prompt_command'); assert.equal(prepareShellIntegration(launch, { preset: 'claude', platform: 'darwin' }).integrationReason, 'unsupported_launch'); });
test('malformed prefixes preserve output and resynchronize to matching completions across chunks', async (t) => {
    const malformed = '\x1b]777;OpenProgram;invalid';
    const stream = 'before' + malformed + frame(3, 'end', 1, 0) + 'after';
    const partitions = Array.from({ length: stream.length + 1 }, (_, split) => [stream.slice(0, split), stream.slice(split)]);
    partitions.push([...stream]);
    for (const chunks of partitions) {
        const visible = [];
        const f = fixture(t, { emit(_host, kind, value) { if (kind === 'data') visible.push(value.data); } });
        await f.open();
        f.p.data(frame(1, 'ready') + frame(2, 'start', 1));
        for (const chunk of chunks) f.p.data(chunk);
        assert.deepEqual(f.read().shell_integration.cycle, { id: 1, phase: 'finished', exit_code: 0 });
        const expected = 'before' + malformed + 'after';
        assert.equal(visible.join(''), expected);
        assert.equal(f.read().data, expected);
        assert.equal(f.read().next_cursor, expected.length);
        f.p.data(frame(4, 'start', 2) + frame(5, 'end', 2, 1));
        assert.deepEqual(f.read().shell_integration.cycle, { id: 2, phase: 'finished', exit_code: 1 });
        assert.equal(f.read().shell_integration.event_sequence, 5);
        assert.equal(f.read().data, expected);
        assert.equal(visible.join(''), expected);
        f.manager.dispose();
    }
});
