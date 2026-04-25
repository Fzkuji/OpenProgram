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

  it('/attach without args prints usage', () => {
    const ctx = makeCtx();
    handleSlash('/attach', ctx);
    expect(ctx.pushSystem).toHaveBeenCalledWith(expect.stringContaining('Usage'));
  });

  it('/attach without conversation says so', () => {
    const ctx = makeCtx({ currentConversation: undefined });
    handleSlash('/attach wechat default wxid_alice', ctx);
    expect(ctx.pushSystem).toHaveBeenCalledWith(
      expect.stringContaining('No current conversation'),
    );
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
      conversation_id: 'local_abc',
    });
  });

  it('unknown slash returns false (falls through to chat)', () => {
    expect(handleSlash('/totally-unknown', makeCtx())).toBe(false);
  });
});
