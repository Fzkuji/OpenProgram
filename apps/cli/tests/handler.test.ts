import { afterEach, describe, it, expect, vi } from 'vitest';
import {
  mkdtempSync,
  readFileSync,
  rmSync,
  writeFileSync,
} from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { handleSlash, SlashContext } from '../src/commands/handler.js';

const originalCwd = process.cwd();

const makeCtx = (overrides: Partial<SlashContext> = {}): SlashContext => ({
  client: { send: vi.fn() } as never,
  pushSystem: vi.fn(),
  clearCommitted: vi.fn(),
  newSession: vi.fn(),
  exit: vi.fn(),
  openPicker: vi.fn(),
  openClaudeAccounts: vi.fn(),
  toggleTools: vi.fn(),
  toggleBell: vi.fn(() => true),
  showWelcome: vi.fn(),
  showAgentInfo: vi.fn(),
  exportTranscript: vi.fn(() => '/tmp/out.md'),
  ...overrides,
});

describe('handleSlash', () => {
  afterEach(() => {
    process.chdir(originalCwd);
    vi.unstubAllGlobals();
    vi.unstubAllEnvs();
  });

  it('returns false for non-slash input', () => {
    expect(handleSlash('plain text', makeCtx())).toBe(false);
  });

  it('/help prints help text', () => {
    const ctx = makeCtx();
    expect(handleSlash('/help', ctx)).toBe(true);
    expect(ctx.pushSystem).toHaveBeenCalled();
  });

  it('/keybindings opens the shortcut reference', () => {
    const ctx = makeCtx();
    expect(handleSlash('/keybindings', ctx)).toBe(true);
    expect(ctx.openPicker).toHaveBeenCalledWith('shortcuts');
  });

  it('/review submits a read-only review turn', () => {
    const submitChat = vi.fn();
    const ctx = makeCtx({ submitChat });
    expect(handleSlash('/review main', ctx)).toBe(true);
    expect(submitChat).toHaveBeenCalledWith(expect.stringContaining('against main'));
    expect(submitChat).toHaveBeenCalledWith(expect.stringContaining('do not modify files'));
  });

  it('/memory lists topics through the owner API', async () => {
    vi.stubEnv('OPENPROGRAM_BACKEND_URL', 'http://backend.test');
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({
      ok: true,
      json: async () => [{ path: 'people/alice.md', title: 'Alice' }],
    }));
    const ctx = makeCtx();

    expect(handleSlash('/memory list', ctx)).toBe(true);
    await vi.waitFor(() => {
      expect(ctx.pushSystem).toHaveBeenCalledWith('Memory topics:\n• people/alice.md — Alice');
    });
  });

  it('/memory edit saves quoted content through the owner API', async () => {
    vi.stubEnv('OPENPROGRAM_BACKEND_URL', 'http://backend.test');
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, json: async () => ({ ok: true }) });
    vi.stubGlobal('fetch', fetchMock);
    const ctx = makeCtx();

    handleSlash('/memory edit people/alice.md "# Alice Smith"', ctx);
    await vi.waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith(
        'http://backend.test/api/memory/topics/people%2Falice.md',
        expect.objectContaining({
          method: 'PUT',
          body: JSON.stringify({ content: '# Alice Smith' }),
        }),
      );
    });
  });

  it('/jobs requests canonical job DTOs for the active session', () => {
    const send = vi.fn();
    const ctx = makeCtx({
      client: { send } as never,
      currentConversation: 'session-1',
    });
    expect(handleSlash('/jobs', ctx)).toBe(true);
    expect(send).toHaveBeenCalledWith({
      action: 'list_jobs',
      session_id: 'session-1',
    });
  });

  it('/jobs <id> requests one canonical job DTO', () => {
    const send = vi.fn();
    const ctx = makeCtx({ client: { send } as never });
    expect(handleSlash('/jobs t1', ctx)).toBe(true);
    expect(send).toHaveBeenCalledWith({ action: 'get_job', job_id: 't1' });
  });

  it('/clear empties committed', () => {
    const ctx = makeCtx();
    handleSlash('/clear', ctx);
    expect(ctx.clearCommitted).toHaveBeenCalled();
  });

  it('/quit exits', () => {
    const ctx = makeCtx();
    handleSlash('/quit', ctx);
    expect(ctx.exit).toHaveBeenCalled();
  });

  it('/model with no arg opens model picker', () => {
    const ctx = makeCtx();
    handleSlash('/model', ctx);
    expect(ctx.openPicker).toHaveBeenCalledWith('model');
  });

  it('/agent with no arg opens agent picker', () => {
    const ctx = makeCtx();
    handleSlash('/agent', ctx);
    expect(ctx.openPicker).toHaveBeenCalledWith('agent');
  });

  it('/agent <id> sends set_default_agent', () => {
    const send = vi.fn();
    const ctx = makeCtx({ client: { send } as never });
    handleSlash('/agent worker', ctx);
    expect(send).toHaveBeenCalledWith({ action: 'set_default_agent', id: 'worker' });
  });

  it('/login (no arg) opens the Claude accounts panel', () => {
    const ctx = makeCtx();
    handleSlash('/login', ctx);
    expect(ctx.openClaudeAccounts).toHaveBeenCalled();
  });

  it('/login claude opens the Claude accounts panel', () => {
    const ctx = makeCtx();
    handleSlash('/login claude', ctx);
    expect(ctx.openClaudeAccounts).toHaveBeenCalled();
  });

  it('/login wechat opens the channel picker (not a raw command)', () => {
    const ctx = makeCtx();
    handleSlash('/login wechat', ctx);
    expect(ctx.openPicker).toHaveBeenCalledWith('channel');
    expect(ctx.openClaudeAccounts).not.toHaveBeenCalled();
  });

  it('/logout opens the Claude accounts panel', () => {
    const ctx = makeCtx();
    handleSlash('/logout', ctx);
    expect(ctx.openClaudeAccounts).toHaveBeenCalled();
  });

  it('/resume opens resume picker', () => {
    const ctx = makeCtx();
    handleSlash('/resume', ctx);
    expect(ctx.openPicker).toHaveBeenCalledWith('resume');
  });

  it('/tools toggles', () => {
    const ctx = makeCtx();
    handleSlash('/tools', ctx);
    expect(ctx.toggleTools).toHaveBeenCalled();
  });

  it('/effort sets thinking effort', () => {
    const setThinkingEffort = vi.fn();
    const ctx = makeCtx({ setThinkingEffort });
    handleSlash('/effort minimal', ctx);
    expect(setThinkingEffort).toHaveBeenCalledWith('minimal');
  });

  it('/effort with no arg opens effort picker', () => {
    const ctx = makeCtx({ currentThinkingEffort: 'high', setThinkingEffort: vi.fn() });
    handleSlash('/effort', ctx);
    expect(ctx.openPicker).toHaveBeenCalledWith('effort');
  });

  it('/attach without args prints usage', () => {
    const ctx = makeCtx();
    handleSlash('/attach', ctx);
    expect(ctx.pushSystem).toHaveBeenCalledWith(expect.stringContaining('Usage'));
  });

  it('/attach without conversation sends lazy attach_session', () => {
    const send = vi.fn();
    const ctx = makeCtx({
      client: { send } as never,
      currentConversation: undefined,
    });
    handleSlash('/attach wechat default wxid_alice', ctx);
    expect(send).toHaveBeenCalledWith({
      action: 'attach_session',
      channel: 'wechat',
      account_id: 'default',
      peer: 'wxid_alice',
      session_id: '',
      peer_kind: 'direct',
      peer_id: 'wxid_alice',
    });
    expect(ctx.pushSystem).toHaveBeenCalledWith(expect.stringContaining('materialize'));
  });

  it('/attach with conv sends attach_session', () => {
    const send = vi.fn();
    const ctx = makeCtx({
      client: { send } as never,
      currentConversation: 'local_abc',
    });
    handleSlash('/attach wechat default wxid_alice', ctx);
    expect(send).toHaveBeenCalledWith({
      action: 'attach_session',
      channel: 'wechat',
      account_id: 'default',
      peer: 'wxid_alice',
      session_id: 'local_abc',
      peer_kind: 'direct',
      peer_id: 'wxid_alice',
    });
  });

  it('unknown slash returns false (falls through to chat)', () => {
    expect(handleSlash('/totally-unknown', makeCtx())).toBe(false);
  });

  it('aliases /q → /quit', () => {
    const ctx = makeCtx();
    handleSlash('/q', ctx);
    expect(ctx.exit).toHaveBeenCalled();
  });

  it('aliases /h → /help', () => {
    const ctx = makeCtx();
    handleSlash('/h', ctx);
    expect(ctx.pushSystem).toHaveBeenCalled();
  });

  it('aliases /m → /model picker', () => {
    const ctx = makeCtx();
    handleSlash('/m', ctx);
    expect(ctx.openPicker).toHaveBeenCalledWith('model');
  });

  it('/bell toggles', () => {
    const ctx = makeCtx();
    handleSlash('/bell', ctx);
    expect(ctx.toggleBell).toHaveBeenCalled();
  });

  it('/welcome calls showWelcome', () => {
    const ctx = makeCtx();
    handleSlash('/welcome', ctx);
    expect(ctx.showWelcome).toHaveBeenCalled();
  });

  it('/agent inspect calls showAgentInfo', () => {
    const ctx = makeCtx();
    handleSlash('/agent inspect', ctx);
    expect(ctx.showAgentInfo).toHaveBeenCalled();
  });

  it('/theme with no arg opens theme picker', () => {
    const ctx = makeCtx();
    handleSlash('/theme', ctx);
    expect(ctx.openPicker).toHaveBeenCalledWith('theme');
  });

  it('/theme <name> calls setTheme and reports result', () => {
    const setTheme = vi.fn(() => true);
    const ctx = makeCtx({ setTheme });
    handleSlash('/theme light', ctx);
    expect(setTheme).toHaveBeenCalledWith('light');
    expect(ctx.pushSystem).toHaveBeenCalledWith(expect.stringContaining('Theme set to light'));
  });

  it('/theme auto is accepted (system-detect)', () => {
    const setTheme = vi.fn(() => true);
    const ctx = makeCtx({ setTheme });
    handleSlash('/theme auto', ctx);
    expect(setTheme).toHaveBeenCalledWith('auto');
  });

  it('/theme <bogus> reports unknown', () => {
    const setTheme = vi.fn(() => false);
    const ctx = makeCtx({ setTheme });
    handleSlash('/theme bogus', ctx);
    expect(ctx.pushSystem).toHaveBeenCalledWith(expect.stringContaining('Unknown theme'));
  });

  it('/compact requires an active conversation', () => {
    const send = vi.fn();
    const ctx = makeCtx({ client: { send } as never });

    expect(handleSlash('/compact', ctx)).toBe(true);
    expect(send).not.toHaveBeenCalled();
    expect(ctx.pushSystem).toHaveBeenCalledWith('No active session to compact.');
  });

  it('/compact sends the existing compact action for the active conversation', () => {
    const send = vi.fn();
    const ctx = makeCtx({
      client: { send } as never,
      currentConversation: 'session-123',
    });

    expect(handleSlash('/compact', ctx)).toBe(true);
    expect(send).toHaveBeenCalledWith({ action: 'compact', session_id: 'session-123' });
  });

  it('/init reports exactly which seed files were created and skipped', () => {
    const workspace = mkdtempSync(join(tmpdir(), 'openprogram-init-'));
    try {
      writeFileSync(join(workspace, 'SOUL.md'), 'keep me\n');
      process.chdir(workspace);
      const cwd = process.cwd();
      const ctx = makeCtx();

      expect(handleSlash('/init', ctx)).toBe(true);

      expect(ctx.pushSystem).toHaveBeenCalledWith(
        `Initialized OpenProgram workspace at ${cwd}\n` +
        'Created: AGENTS.md, USER.md\n' +
        'Skipped existing: SOUL.md',
      );
      expect(readFileSync(join(workspace, 'SOUL.md'), 'utf8')).toBe('keep me\n');
    } finally {
      process.chdir(originalCwd);
      rmSync(workspace, { recursive: true, force: true });
    }
  });

  it('/init reports a synchronous seed write failure', () => {
    const workspace = mkdtempSync(join(tmpdir(), 'openprogram-init-failure-'));
    try {
      process.chdir(workspace);
      const ctx = makeCtx({
        writeWorkspaceSeed: () => {
          throw new Error('simulated write failure');
        },
      });

      expect(handleSlash('/init', ctx)).toBe(true);

      expect(ctx.pushSystem).toHaveBeenCalledWith(
        '/init failed: simulated write failure',
      );
    } finally {
      process.chdir(originalCwd);
      rmSync(workspace, { recursive: true, force: true });
    }
  });

  it('/doctor renders the backend health report', async () => {
    vi.stubEnv('OPENPROGRAM_BACKEND_URL', 'http://backend.test');
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({
        results: [
          { ok: true, label: 'Python', detail: '3.12' },
          { ok: false, label: 'MCP', detail: 'unavailable' },
        ],
        all_ok: false,
      }),
    });
    vi.stubGlobal('fetch', fetchMock);
    const ctx = makeCtx();

    expect(handleSlash('/doctor', ctx)).toBe(true);

    await vi.waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith('http://backend.test/api/doctor', expect.anything());
      expect(ctx.pushSystem).toHaveBeenCalledWith(
        'Doctor report\n\n✓ Python - 3.12\n✗ MCP - unavailable\n\nSome checks failed.',
      );
    });
  });

  it('/doctor reports HTTP and network failures', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: false, status: 503 }));
    const httpCtx = makeCtx();

    handleSlash('/doctor', httpCtx);
    await vi.waitFor(() => {
      expect(httpCtx.pushSystem).toHaveBeenCalledWith('Doctor check failed: HTTP 503');
    });

    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new Error('connection refused')));
    const networkCtx = makeCtx();
    handleSlash('/doctor', networkCtx);
    await vi.waitFor(() => {
      expect(networkCtx.pushSystem).toHaveBeenCalledWith('Doctor check failed: connection refused');
    });
  });

  it('/mcp lists server status without arguments', async () => {
    vi.stubEnv('OPENPROGRAM_BACKEND_URL', 'http://backend.test');
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({
        servers: [{ name: 'filesystem', enabled: true, ready: true, error: null, tool_count: 2 }],
      }),
    });
    vi.stubGlobal('fetch', fetchMock);
    const ctx = makeCtx();

    expect(handleSlash('/mcp', ctx)).toBe(true);

    await vi.waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith('http://backend.test/api/mcp/servers', expect.anything());
      expect(ctx.pushSystem).toHaveBeenCalledWith('MCP servers:\n✓ filesystem — ready (2 tools)');
    });
  });

  it('/mcp reports list HTTP errors and show network errors', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: false, status: 503 }));
    const listCtx = makeCtx();

    handleSlash('/mcp', listCtx);
    await vi.waitFor(() => {
      expect(listCtx.pushSystem).toHaveBeenCalledWith('MCP list failed: HTTP 503');
    });

    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new Error('connection refused')));
    const showCtx = makeCtx();
    handleSlash('/mcp show filesystem', showCtx);
    await vi.waitFor(() => {
      expect(showCtx.pushSystem).toHaveBeenCalledWith(
        'MCP show failed: connection refused',
      );
    });
  });

  it.each(['show', 'restart', 'enable', 'disable'])('/mcp %s requires a server name', (verb) => {
    const ctx = makeCtx();

    handleSlash(`/mcp ${verb}`, ctx);

    expect(ctx.pushSystem).toHaveBeenCalledWith(`Usage: /mcp ${verb} <name>`);
  });

  it('/mcp reports an empty server list', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ servers: [] }),
    }));
    const ctx = makeCtx();

    handleSlash('/mcp', ctx);

    await vi.waitFor(() => {
      expect(ctx.pushSystem).toHaveBeenCalledWith('MCP servers: none configured.');
    });
  });

  it('/mcp show renders one server and its tools', async () => {
    vi.stubEnv('OPENPROGRAM_BACKEND_URL', 'http://backend.test');
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({
        name: 'filesystem', enabled: true, ready: true, error: null, tool_count: 2,
        tools: ['read_file', 'write_file'],
      }),
    }));
    const ctx = makeCtx();

    handleSlash('/mcp show filesystem', ctx);

    await vi.waitFor(() => {
      expect(ctx.pushSystem).toHaveBeenCalledWith(
        'MCP server: filesystem\nstate: ready\ntools: read_file, write_file',
      );
    });
  });

  it('/mcp show URL-encodes the server name', async () => {
    vi.stubEnv('OPENPROGRAM_BACKEND_URL', 'http://backend.test');
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({
        name: 'repo/fs', enabled: true, ready: true, error: null, tool_count: 0,
      }),
    });
    vi.stubGlobal('fetch', fetchMock);
    const ctx = makeCtx();

    handleSlash('/mcp show repo/fs', ctx);

    await vi.waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith('http://backend.test/api/mcp/servers/repo%2Ffs', expect.anything());
    });
  });

  it.each([
    ['restart', 'restarted'],
    ['enable', 'enabled'],
    ['disable', 'disabled'],
  ])('/mcp %s invokes the server action', async (action, result) => {
    vi.stubEnv('OPENPROGRAM_BACKEND_URL', 'http://backend.test');
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, json: async () => ({}) });
    vi.stubGlobal('fetch', fetchMock);
    const ctx = makeCtx();

    handleSlash(`/mcp ${action} filesystem`, ctx);

    await vi.waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith(
        `http://backend.test/api/mcp/servers/filesystem/${action}`,
        expect.objectContaining({ method: 'POST' }),
      );
      expect(ctx.pushSystem).toHaveBeenCalledWith(`MCP server filesystem ${result}.`);
    });
  });

  it('/mcp add posts a local server configuration', async () => {
    vi.stubEnv('OPENPROGRAM_BACKEND_URL', 'http://backend.test');
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, json: async () => ({}) });
    vi.stubGlobal('fetch', fetchMock);
    const ctx = makeCtx();

    handleSlash('/mcp add demo "C:\\Program Files\\node.exe" server.js --env TOKEN=abc --timeout 45', ctx);

    await vi.waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith(
        'http://backend.test/api/mcp/servers',
        expect.objectContaining({
          method: 'POST',
          body: JSON.stringify({
            name: 'demo',
            type: 'local',
            command: ['C:\\Program Files\\node.exe', 'server.js'],
            env: { TOKEN: 'abc' },
            enabled: true,
            timeout_seconds: 45,
          }),
        }),
      );
    });
  });

  it('/mcp remove deletes the named server', async () => {
    vi.stubEnv('OPENPROGRAM_BACKEND_URL', 'http://backend.test');
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, json: async () => ({}) });
    vi.stubGlobal('fetch', fetchMock);
    const ctx = makeCtx();

    handleSlash('/mcp rm demo', ctx);
    await vi.waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith(
        'http://backend.test/api/mcp/servers/demo',
        expect.objectContaining({ method: 'DELETE' }),
      );
    });
  });

  const styleRow = {
    settings: [
      {
        key: 'agent.output_style',
        value: 'default',
        choices: ['default', 'concise', 'direct'],
      },
    ],
  };

  it('/style lists styles and marks the active one', async () => {
    vi.stubEnv('OPENPROGRAM_BACKEND_URL', 'http://backend.test');
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, json: async () => styleRow });
    vi.stubGlobal('fetch', fetchMock);
    const ctx = makeCtx();

    expect(handleSlash('/style', ctx)).toBe(true);

    await vi.waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith('http://backend.test/api/settings', expect.anything());
      expect(ctx.pushSystem).toHaveBeenCalledWith(
        'Output style (how replies are written):\n● default\n○ concise\n○ direct\n\nSwitch with /style <name>.',
      );
    });
  });

  it('/style <name> posts the new style', async () => {
    vi.stubEnv('OPENPROGRAM_BACKEND_URL', 'http://backend.test');
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, json: async () => styleRow });
    vi.stubGlobal('fetch', fetchMock);
    const ctx = makeCtx();

    handleSlash('/style concise', ctx);

    await vi.waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith(
        'http://backend.test/api/settings',
        expect.objectContaining({
          method: 'POST',
          body: JSON.stringify({ key: 'agent.output_style', value: 'concise' }),
        }),
      );
      expect(ctx.pushSystem).toHaveBeenCalledWith(
        'Output style set to concise. Applies from the next turn.',
      );
    });
  });

  it('/style rejects an unknown name without posting', async () => {
    vi.stubEnv('OPENPROGRAM_BACKEND_URL', 'http://backend.test');
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, json: async () => styleRow });
    vi.stubGlobal('fetch', fetchMock);
    const ctx = makeCtx();

    handleSlash('/style nope', ctx);

    await vi.waitFor(() => {
      expect(ctx.pushSystem).toHaveBeenCalledWith(
        "Unknown style 'nope'. Available: default, concise, direct",
      );
    });
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });
});
