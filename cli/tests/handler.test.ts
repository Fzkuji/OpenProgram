import { describe, it, expect, vi } from 'vitest';
import { handleSlash, SlashContext } from '../src/commands/handler.js';

const makeCtx = (overrides: Partial<SlashContext> = {}): SlashContext => ({
  client: { send: vi.fn() } as never,
  pushSystem: vi.fn(),
  clearCommitted: vi.fn(),
  newSession: vi.fn(),
  exit: vi.fn(),
  openPicker: vi.fn(),
  toggleTools: vi.fn(),
  toggleBell: vi.fn(() => true),
  showWelcome: vi.fn(),
  showAgentInfo: vi.fn(),
  exportTranscript: vi.fn(() => '/tmp/out.md'),
  ...overrides,
});

describe('handleSlash', () => {
  it('returns false for non-slash input', () => {
    expect(handleSlash('plain text', makeCtx())).toBe(false);
  });

  it('/help prints help text', () => {
    const ctx = makeCtx();
    expect(handleSlash('/help', ctx)).toBe(true);
    expect(ctx.pushSystem).toHaveBeenCalled();
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

  it('/effort reports available values', () => {
    const ctx = makeCtx({ currentThinkingEffort: 'high', setThinkingEffort: vi.fn() });
    handleSlash('/effort', ctx);
    expect(ctx.pushSystem).toHaveBeenCalledWith(expect.stringContaining('off|minimal|low|medium|high|xhigh'));
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
});
