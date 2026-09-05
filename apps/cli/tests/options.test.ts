import { afterEach, describe, expect, it, vi } from 'vitest';
import { parseTuiOptions } from '../src/options.js';

describe('parseTuiOptions', () => {
  afterEach(() => vi.unstubAllEnvs());

  it('uses alternate-screen mode by default', () => {
    expect(parseTuiOptions([])).toMatchObject({ altScreen: true, screenReader: false });
  });

  it('supports inline mode from argv and environment', () => {
    expect(parseTuiOptions(['--no-alt-screen']).altScreen).toBe(false);
    vi.stubEnv('OPENPROGRAM_TUI_NO_ALT_SCREEN', 'true');
    expect(parseTuiOptions([]).altScreen).toBe(false);
  });

  it('screen-reader mode always disables the alternate screen', () => {
    expect(parseTuiOptions(['--screen-reader'])).toMatchObject({
      altScreen: false,
      screenReader: true,
    });
  });

  it('passes the requested agent and resumed conversation from the launcher', () => {
    vi.stubEnv('OPENPROGRAM_AGENT', 'worker');
    vi.stubEnv('OPENPROGRAM_CONV', 'local_123');
    expect(parseTuiOptions([])).toMatchObject({
      initialAgent: 'worker',
      initialConversation: 'local_123',
    });
  });
});
