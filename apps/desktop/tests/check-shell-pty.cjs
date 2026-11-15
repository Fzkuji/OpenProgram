'use strict';
const { test: nativeTest } = require('node:test');
const test = process.platform === 'darwin' ? nativeTest : nativeTest.skip;
const assert = require('node:assert/strict');
const fs = require('node:fs'), os = require('node:os'), path = require('node:path');
const { EventEmitter } = require('node:events');
const pty = require('node-pty');
const { registerTerminalResourceIpc } = require('../main/terminal/terminal-resource-ipc');
function wait(check, events, timeout = 5000) { return new Promise((resolve, reject) => { let timer; const done = () => { try {
    const value = check();
    if (value) {
        clearTimeout(timer);
        events.off('change', done);
        resolve(value);
    }
}
catch (e) {
    clearTimeout(timer);
    events.off('change', done);
    reject(e);
} }; events.on('change', done); timer = setTimeout(() => { events.off('change', done); reject(Error('timed out')); }, timeout); done(); }); }
async function fixture(t, shell, extra = '', files = {}) {
    const home = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), 'op-shell-test-')));
    const cwd = path.join(home, '目录 with spaces');
    fs.mkdirSync(cwd);
    fs.writeFileSync(path.join(home, shell === 'zsh' ? '.zshrc' : '.bash_profile'), `PS1='OP_READY> '\nPS2='CONT> '\nHISTFILE=$HOME/history\n${extra}\n`);
    for (const [name, body] of Object.entries(files))
        fs.writeFileSync(path.join(home, name), body);
    const events = new EventEmitter(), app = new EventEmitter(), sender = new EventEmitter(), handlers = new Map(), listeners = new Map();
    let native, launch;
    app.getPath = () => home;
    sender.isDestroyed = () => false;
    sender.mainFrame = { url: 'http://127.0.0.1:18100/chat' };
    sender.send = () => events.emit('change');
    const ctx = { id: 'main', win: { webContents: sender } }, event = { sender, senderFrame: sender.mainFrame };
    const manager = registerTerminalResourceIpc({ ipcMain: { handle: (n, f) => handlers.set(n, f), on: (n, f) => listeners.set(n, f) }, app, contextForSender: () => ctx, origin: 'http://127.0.0.1:18100', getOwnerToken: () => '', platform: 'darwin', env: { HOME: home, SHELL: '/bin/' + shell, PATH: '/usr/bin:/bin', TERM: 'xterm-256color', LANG: 'en_US.UTF-8', BASH_SILENCE_DEPRECATION_WARNING: '1' }, spawn(c, a, o) { launch = o; native = pty.spawn(c, a, o); return native; }, listProcesses: () => '' });
    const read = () => manager.human('read', { terminal_id: 'terminal:main:shell', cursor: 0 }, sender);
    const send = data => manager.human('input', { terminal_id: 'terminal:main:shell', data }, sender);
    t.after(async () => { if (read().ok && !['closed', 'exited'].includes(read().status)) {
        native.kill();
        await wait(() => ['closed', 'exited'].includes(read().status), events).catch(() => { });
    } app.emit('before-quit'); fs.rmSync(home, { recursive: true, force: true }); });
    const opened = await handlers.get('terminal:start')(event, { id: 'terminal:main:shell', preset: 'shell', cwd: home });
    assert.equal(opened.ok, true);
    await wait(() => read().data.includes('OP_READY> '), events);
    return { home, cwd, events, read, send, launch, native, manager, sender, handlers, event };
}
for (const shell of ['zsh', 'bash'])
    test(`${shell}: real IPC launch reports foreground lifecycle, Unicode cwd and separate delivery`, async (t) => {
        const f = await fixture(t, shell);
        assert.equal(f.read().shell_integration.status, 'ready', f.read().data);
        assert.equal(f.read().shell_integration.cycle, null, 'initial prompt has no command');
        let n = 0;
        async function command(text, status) { const receipt = f.send(text + '\r'); assert.equal(receipt.command_complete, null); assert.equal(receipt.phase, 'delivered'); n++; await wait(() => f.read().shell_integration.cycle?.id === n && f.read().shell_integration.cycle.phase === 'finished', f.events).catch(e => { throw Error(text + ': ' + JSON.stringify(f.read())); }); assert.equal(f.read().shell_integration.cycle.exit_code, status, f.read().data); }
        await command('false', 1);
        await command('true', 0);
        await command(`cd '${f.cwd}'`, 0);
        assert.equal(f.read().shell_integration.cwd.value, f.cwd);
        assert.equal(f.read().start_cwd, f.home);
        await command('false | true', 0);
        await command('sleep 1 &', 0);
        f.send('sleep 10\r');
        n++;
        await wait(() => f.read().shell_integration.cycle?.id === n && f.read().shell_integration.cycle.phase === 'running', f.events);
        await new Promise(resolve => setTimeout(resolve, 80));
        f.send('\x03');
        await wait(() => f.read().shell_integration.cycle.phase === 'finished', f.events).catch(e => { throw Error('interrupt: ' + JSON.stringify(f.read())); });
        assert.equal(f.read().shell_integration.cycle.exit_code, 130);
        await command('wait', 0);
        const dir = f.launch.env.OP_SHELL_DIR;
        assert.ok(fs.existsSync(dir));
        const same = await f.handlers.get('terminal:start')(f.event, { id: 'terminal:main:shell', preset: 'shell' });
        assert.equal(same.pid, f.native.pid);
        assert.ok(fs.existsSync(dir));
        f.send('exit\r');
        await wait(() => f.read().status === 'exited', f.events).catch(e => { throw Error('exit: ' + JSON.stringify(f.read())); });
        assert.ok(!fs.existsSync(dir));
        assert.equal(f.read().shell_integration.status, 'ended');
    });
for (const [name, extra] of [['readonly', 'readonly PROMPT_COMMAND'], ['debug', "trap 'printf USERDEBUG' DEBUG"], ['extdebug', 'shopt -s extdebug'], ['functrace', 'set -T'], ['prompt', "PROMPT_COMMAND='printf USERPROMPT'"]])
    test(`bash fallback ${name} preserves a usable shell`, async (t) => { const f = await fixture(t, 'bash', extra); await wait(() => f.read().shell_integration.status === 'unavailable', f.events); assert.equal(f.read().shell_integration.cycle, null); f.send('printf STILL_USABLE\\n\r'); await wait(() => f.read().data.includes('STILL_USABLE'), f.events); assert.ok(!f.read().data.includes('readonly variable')); });
for (const phase of ['.zshenv', '.zprofile', '.zshrc', '.zlogin'])
    test(`zsh respects RCS disabled in ${phase}`, async (t) => {
        const f = await fixture(t, 'zsh', '', { [phase]: "PS1='OP_READY> '\nZDOTDIR=$HOME/selected\nunsetopt rcs\n" });
        await wait(() => f.read().shell_integration.status === 'unavailable', f.events);
        f.send('printf "\\nVALUE=%s:%s\\n" "$ZDOTDIR" "$options[rcs]"\r');
        await wait(() => f.read().data.includes('VALUE=' + f.home + '/selected:off'), f.events);
        assert.equal(f.read().shell_integration.cycle, null);
    });
test('zsh keeps status-aware user hooks and user ZDOTDIR', async (t) => {
    const f = await fixture(t, 'zsh', "precmd() { print -r -- USER_STATUS=$?; }\n");
    f.send('false\r');
    await wait(() => f.read().shell_integration.cycle?.phase === 'finished', f.events);
    assert.equal(f.read().shell_integration.cycle.exit_code, 1);
    assert.ok(f.read().data.includes('USER_STATUS=1'));
    f.send('print -r -- ZDOTDIR_SET=${+ZDOTDIR}\r');
    await wait(() => f.read().data.includes('ZDOTDIR_SET=0'), f.events);
});
test('zsh history stays in the user home, not the temporary startup directory', async (t) => {
    const f = await fixture(t, 'zsh', '', { '.zshrc': "PS1='OP_READY> '\n" });
    f.send('print -r -- HIST=$HISTFILE:END\r');
    await wait(() => /HIST=.*:END/.test(f.read().data.replace(/print -r -- HIST=\$HISTFILE:END/g, '')), f.events);
    const value = /HIST=(.*):END/.exec(f.read().data.replace(/print -r -- HIST=\$HISTFILE:END/g, ''))[1];
    assert.ok(!value.startsWith(f.launch.env.OP_SHELL_DIR), value);
    if (value) assert.equal(value, f.home + '/.zsh_history');
});
for (const shell of ['zsh', 'bash'])
    test(`${shell}: multiline and nested shells preserve root cycle identity`, async (t) => {
        const f = await fixture(t, shell);
        const before = f.read().shell_integration.cycle;
        f.send('{\r');
        await wait(() => f.read().data.includes('CONT> '), f.events);
        assert.deepEqual(f.read().shell_integration.cycle, before);
        f.send('false\r}\r');
        await wait(() => f.read().shell_integration.cycle?.phase === 'finished', f.events);
        assert.equal(f.read().shell_integration.cycle.exit_code, 1);
        // Keep the prompt marker out of the command echo, including partial PTY chunks.
        f.send("env PS1='CHILD_''READY> ' /bin/bash --noprofile --norc -i\r");
        await wait(() => f.read().data.endsWith('CHILD_READY> '), f.events);
        const rootCycle = f.read().shell_integration.cycle;
        assert.equal(rootCycle.phase, 'running');
        const offset = f.read().data.length;
        f.send('false\r');
        await wait(() => f.read().data.length > offset && f.read().data.endsWith('CHILD_READY> '), f.events);
        assert.deepEqual(f.read().shell_integration.cycle, rootCycle);
        f.send('exit 7\r');
        await wait(() => f.read().shell_integration.cycle?.phase === 'finished', f.events);
        assert.equal(f.read().shell_integration.cycle.id, rootCycle.id);
        assert.equal(f.read().shell_integration.cycle.exit_code, 7);
    });
for (const shell of ['zsh', 'bash'])
    test(`${shell}: malformed output cannot consume shell completion or the next cycle`, async (t) => {
        const f = await fixture(t, shell);
        const prefix = '\x1b]777;OpenProgram;';
        f.send("printf '\\033]777;OpenProgram;'\r");
        await wait(() => f.read().shell_integration.cycle?.phase === 'finished', f.events);
        assert.deepEqual(f.read().shell_integration.cycle, { id: 1, phase: 'finished', exit_code: 0 });
        assert.equal(f.read().data.split(prefix).length - 1, 1, 'retain only the malformed output, not valid metadata frames');
        f.send("printf '__AFTER_BAD_%s__\\n' frame; false\r");
        await wait(() => f.read().shell_integration.cycle?.id === 2 && f.read().shell_integration.cycle.phase === 'finished', f.events);
        assert.equal(f.read().shell_integration.cycle.exit_code, 1);
        assert.ok(f.read().data.includes('__AFTER_BAD_frame__'));
        assert.equal(f.read().data.split(prefix).length - 1, 1);
    });
