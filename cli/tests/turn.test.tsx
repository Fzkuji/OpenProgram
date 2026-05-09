import { describe, it, expect } from 'vitest';
import React from 'react';
import { render } from 'ink-testing-library';
import { TurnRow } from '../src/components/Turn.js';

const stripAnsi = (s: string): string => s.replace(/\x1b\[[0-9;?]*[A-Za-z]/g, '');

describe('TurnRow', () => {
  it('user turn carries the > prefix on the first line', () => {
    const { lastFrame } = render(
      <TurnRow turn={{ id: 'u1', role: 'user', text: 'hello world' }} />,
    );
    const out = stripAnsi(lastFrame() ?? '');
    expect(out).toContain('> hello world');
  });

  it('assistant turn renders the bullet glyph', () => {
    const { lastFrame } = render(
      <TurnRow turn={{ id: 'a1', role: 'assistant', text: 'reply text' }} />,
    );
    const out = stripAnsi(lastFrame() ?? '');
    expect(out).toMatch(/●\s+reply text/);
  });

  it('renders tool calls under the assistant turn', () => {
    const { lastFrame } = render(
      <TurnRow
        turn={{
          id: 'a2',
          role: 'assistant',
          text: 'sure thing',
          tools: [
            { id: 't1', tool: 'Bash', input: 'ls /tmp', status: 'done' },
          ],
        }}
      />,
    );
    const out = stripAnsi(lastFrame() ?? '');
    expect(out).toContain('Bash');
    expect(out).toContain('ls /tmp');
  });

  it('system turn renders dim italic', () => {
    const { lastFrame } = render(
      <TurnRow turn={{ id: 's1', role: 'system', text: 'note' }} />,
    );
    const out = stripAnsi(lastFrame() ?? '');
    expect(out).toContain('note');
  });
});
