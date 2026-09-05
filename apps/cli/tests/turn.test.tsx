import { beforeEach, describe, it, expect, vi } from 'vitest';
import React from 'react';
import { render, renderWithUpdates } from './renderToFrame.js';
import { Messages } from '../src/components/Messages.js';
import { TurnRow } from '../src/components/Turn.js';

const { renderMarkdown } = vi.hoisted(() => ({
  renderMarkdown: vi.fn((text: string) => text),
}));

vi.mock('../src/utils/markdown.js', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../src/utils/markdown.js')>();
  renderMarkdown.mockImplementation(actual.renderMarkdown);
  return {
    ...actual,
    renderMarkdown,
  };
});

const stripAnsi = (s: string): string => s.replace(/\x1b\[[0-9;?]*[A-Za-z]/g, '');

describe('TurnRow', () => {
  beforeEach(() => {
    renderMarkdown.mockClear();
  });

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

  it('reuses committed markdown while only the streaming turn changes', () => {
    const committed = [
      { id: 'fixed', role: 'assistant' as const, text: '**fixed**' },
    ];
    const view = renderWithUpdates(
      <Messages
        committed={committed}
        streaming={{ id: 'live', role: 'assistant', text: 'one', streaming: true }}
      />,
    );

    try {
      expect(renderMarkdown).toHaveBeenCalledTimes(1);

      view.rerender(
        <Messages
          committed={committed}
          streaming={{ id: 'live', role: 'assistant', text: 'two', streaming: true }}
        />,
      );
      expect(renderMarkdown).toHaveBeenCalledTimes(1);
      const streamingFrame = stripAnsi(view.lastFrame() ?? '');
      expect(streamingFrame).toContain('two');
      expect(streamingFrame).not.toContain('one');

      view.rerender(
        <Messages
          committed={[{ ...committed[0], text: '**changed**' }]}
          streaming={{ id: 'live', role: 'assistant', text: 'two', streaming: true }}
        />,
      );
      expect(renderMarkdown).toHaveBeenCalledTimes(2);

      view.rerender(
        <Messages
          committed={[{ ...committed[0], text: '**changed**' }]}
          streaming={{ id: 'live', role: 'assistant', text: 'two', streaming: false }}
        />,
      );
      expect(renderMarkdown).toHaveBeenCalledTimes(3);
    } finally {
      view.unmount();
    }
  });

  it('re-renders committed markdown when the terminal width changes', async () => {
    const committed = [
      { id: 'fixed', role: 'assistant' as const, text: '---' },
    ];
    const view = renderWithUpdates(
      <Messages committed={committed} streaming={null} />,
    );

    try {
      expect(renderMarkdown).toHaveBeenCalledTimes(1);
      await view.resize(40);
      expect(renderMarkdown).toHaveBeenCalledTimes(2);
      const resizedMarkdown = renderMarkdown.mock.results.at(-1)?.value ?? '';
      expect(stripAnsi(resizedMarkdown)).toBe('-'.repeat(39));
    } finally {
      view.unmount();
    }
  });
});
