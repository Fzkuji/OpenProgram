import { describe, it, expect } from 'vitest';
import React from 'react';
import { render } from 'ink-testing-library';
import { BottomBar } from '../src/components/BottomBar.js';

const stripAnsi = (s: string): string => s.replace(/\x1b\[[0-9;?]*[A-Za-z]/g, '');

describe('BottomBar', () => {
  it('shows agent · model · session on the right', () => {
    const { lastFrame } = render(
      <BottomBar agent="main" model="gpt-5.4" conversationId="local_abc12345" />,
    );
    const out = stripAnsi(lastFrame() ?? '');
    expect(out).toContain('main');
    expect(out).toContain('gpt-5.4');
    expect(out).toContain('local_abc1234');
  });

  it('switches the left hint when slashMode is active', () => {
    const idle = render(<BottomBar agent="main" />);
    const idleOut = stripAnsi(idle.lastFrame() ?? '');
    // Idle hint is now context-only; "type / for commands" lives inside
    // the input box, not in the bottom bar.
    expect(idleOut).toContain('ctrl+c quit');

    const slash = render(<BottomBar agent="main" slashMode />);
    const slashOut = stripAnsi(slash.lastFrame() ?? '');
    expect(slashOut).toContain('↑↓ choose');
  });

  it('renders permission + thinking effort cycle indicators', () => {
    const ask = render(<BottomBar agent="main" permissionMode="ask" thinkingEffort="medium" />);
    expect(stripAnsi(ask.lastFrame() ?? '')).toContain('ask');
    const bypass = render(<BottomBar agent="main" permissionMode="bypass" thinkingEffort="high" />);
    expect(stripAnsi(bypass.lastFrame() ?? '')).toContain('bypass');
    expect(stripAnsi(bypass.lastFrame() ?? '')).toContain('high');
  });

  it('renders busy indicator on the right', () => {
    const { lastFrame } = render(<BottomBar agent="main" busy />);
    expect(stripAnsi(lastFrame() ?? '')).toContain('working');
  });

  it('renders token counts when provided', () => {
    const { lastFrame } = render(
      <BottomBar agent="main" tokens={{ input: 12500, output: 800 }} />,
    );
    const out = stripAnsi(lastFrame() ?? '');
    expect(out).toContain('12.5k');
    expect(out).toContain('800');
  });
});
