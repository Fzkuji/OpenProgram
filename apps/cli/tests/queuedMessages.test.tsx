import React, { useState } from 'react';
import { mkdtempSync, writeFileSync } from 'fs';
import { tmpdir } from 'os';
import { join } from 'path';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { KEYS, renderWithKeys } from './keyHarness.js';
import { render } from './renderToFrame.js';
import { PromptInput } from '../src/components/PromptInput/PromptInput.js';
import { QueuedMessages } from '../src/components/PromptInput/QueuedMessages.js';
import { enqueue, nextToSend, previewLine, removeAt, takeAt, type QueuedMessage } from '../src/utils/messageQueue.js';
import type { PendingAttachment } from '../src/utils/attachments.js';

const stripAnsi = (s: string): string => s.replace(/\x1b\[(\d*)C/g, (_m, n: string) => ' '.repeat(Number(n || '1'))).replace(/\x1b\[[0-9;?]*[A-Za-z]/g, '');

// Draft persistence writes under the state dir; keep it in a temp folder.
let stateDir = '';
beforeEach(() => {
  stateDir = mkdtempSync(join(tmpdir(), 'op-queue-'));
  vi.stubEnv('OPENPROGRAM_STATE_DIR', stateDir);
});
afterEach(() => vi.unstubAllEnvs());

describe('queue model', () => {
  it('keeps order, takes, removes and sends oldest first', () => {
    let q: QueuedMessage[] = [];
    q = enqueue(q, 'one');
    q = enqueue(q, 'two');
    q = enqueue(q, 'three');
    expect(q.map((m) => m.text)).toEqual(['one', 'two', 'three']);
    const [taken, rest] = takeAt(q, 1);
    expect(taken?.text).toBe('two');
    expect(rest.map((m) => m.text)).toEqual(['one', 'three']);
    expect(removeAt(q, 0).map((m) => m.text)).toEqual(['two', 'three']);
    const [first, after] = nextToSend(q);
    expect(first?.text).toBe('one');
    expect(after).toHaveLength(2);
    expect(previewLine('a\nb\nc')).toEqual({ line: 'a', more: 2 });
  });
});

describe('QueuedMessages', () => {
  it('lists each queued message under ↳ and marks the selection', () => {
    const q = enqueue(enqueue([], 'first one'), 'second\nmore');
    const { lastFrame } = render(<QueuedMessages queued={q} selected={1} width={60} />);
    const out = stripAnsi(lastFrame() ?? '');
    expect(out).toContain('↳ first one');
    expect(out).toContain('› second +1 line');
  });
});

/** PromptInput with the queue wired the way REPL wires it. */
function Harness(props: {
  busy: boolean;
  onSubmit: (text: string, files?: PendingAttachment[]) => void;
  onError?: (m: string) => void;
  initial?: QueuedMessage[];
  expose: (q: QueuedMessage[]) => void;
  readClipboard?: () => Promise<Buffer | null>;
}) {
  const [queued, setQueued] = useState<QueuedMessage[]>(props.initial ?? []);
  props.expose(queued);
  return (
    <PromptInput
      onSubmit={props.onSubmit}
      busy={props.busy}
      onCancel={() => props.onSubmit('<stop>')}
      queued={queued}
      onQueue={(text, files) => setQueued((q) => enqueue(q, text, files))}
      onTakeQueued={(i) => {
        const [row, rest] = takeAt(queued, i);
        setQueued(rest);
        return row;
      }}
      onRemoveQueued={(i) => setQueued((q) => removeAt(q, i))}
      onAttachError={props.onError}
      readClipboard={props.readClipboard}
    />
  );
}

describe('typing while a turn runs', () => {
  it('Enter queues instead of sending, and the rows show above the input', async () => {
    const sent: string[] = [];
    let queue: QueuedMessage[] = [];
    const view = renderWithKeys(
      <Harness busy onSubmit={(t) => sent.push(t)} expose={(q) => { queue = q; }} />,
    );
    try {
      await view.type('first');
      await view.type(KEYS.enter);
      await view.type('second');
      await view.type(KEYS.enter);
      expect(sent).toEqual([]);
      expect(queue.map((q) => q.text)).toEqual(['first', 'second']);
      // Frames after the first are updates, so read everything written.
      const out = stripAnsi(view.frames.join(''));
      expect(out).toContain('↳ second');
      expect(out).toContain('↑ queued · esc stop');
      // Esc still stops the turn.
      await view.type(KEYS.escape);
      await view.settle();
      expect(sent).toEqual(['<stop>']);
    } finally {
      view.unmount();
    }
  });

  it('↑ selects a queued row; x removes it, e takes it back to edit', async () => {
    let queue: QueuedMessage[] = [];
    const initial = enqueue(enqueue(enqueue([], 'one'), 'two'), 'three');
    const sent: string[] = [];
    const view = renderWithKeys(
      <Harness busy initial={initial} onSubmit={(t) => sent.push(t)} expose={(q) => { queue = q; }} />,
    );
    try {
      await view.type(KEYS.up); // selects "three"
      expect(stripAnsi(view.frames.join(''))).toContain('›');
      await view.type(KEYS.up); // "two"
      await view.type('x');
      expect(queue.map((q) => q.text)).toEqual(['one', 'three']);
      await view.type(KEYS.up); // from "three" (index clamped) to "one"
      await view.type('e');
      expect(queue.map((q) => q.text)).toEqual(['three']);
      await view.type(' more');
      await view.type(KEYS.enter);
      expect(queue.map((q) => q.text)).toEqual(['three', 'one more']);
      expect(sent).toEqual([]);
    } finally {
      view.unmount();
    }
  });

  it('when idle Enter sends at once', async () => {
    const sent: string[] = [];
    const view = renderWithKeys(<Harness busy={false} onSubmit={(t) => sent.push(t)} expose={() => {}} />);
    try {
      await view.type('hello');
      await view.type(KEYS.enter);
      expect(sent).toEqual(['hello']);
    } finally {
      view.unmount();
    }
  });
});

describe('attaching by paste', () => {
  it('a pasted image path becomes a placeholder that is sent with its file', async () => {
    const dir = mkdtempSync(join(tmpdir(), 'op-attach-'));
    const image = join(dir, 'shot one.png');
    writeFileSync(image, Buffer.from([1, 2, 3]));
    const got: Array<{ text: string; files?: PendingAttachment[] }> = [];
    const view = renderWithKeys(
      <Harness busy={false} onSubmit={(text, files) => got.push({ text, files })} expose={() => {}} />,
    );
    try {
      await view.type('see ');
      await view.type(KEYS.paste(image.replace(/ /g, '\\ ')));
      await view.type(KEYS.enter);
      expect(got[0]!.text).toBe('see [Image #1] ');
      expect(got[0]!.files).toHaveLength(1);
      expect(got[0]!.files![0]).toMatchObject({ kind: 'image', path: image, data: Buffer.from([1, 2, 3]).toString('base64') });
    } finally {
      view.unmount();
    }
  });

  it('a pasted sentence stays text; an empty paste reads the clipboard image', async () => {
    const errors: string[] = [];
    const got: Array<{ text: string; files?: PendingAttachment[] }> = [];
    const view = renderWithKeys(
      <Harness
        busy={false}
        onSubmit={(text, files) => got.push({ text, files })}
        onError={(m) => errors.push(m)}
        expose={() => {}}
        readClipboard={async () => Buffer.from([9, 9])}
      />,
    );
    try {
      await view.type(KEYS.paste('just words'));
      await view.type(KEYS.paste(''));
      await view.settle();
      await view.type(KEYS.enter);
      expect(got[0]!.text).toBe('just words[Image #1] ');
      expect(got[0]!.files![0]).toMatchObject({ kind: 'image', filename: 'image-1.png' });
      expect(errors).toEqual([]);
    } finally {
      view.unmount();
    }
  });

  it('a deleted placeholder drops its attachment', async () => {
    const dir = mkdtempSync(join(tmpdir(), 'op-attach-'));
    const pdf = join(dir, 'spec.pdf');
    writeFileSync(pdf, 'pdf');
    const got: Array<{ text: string; files?: PendingAttachment[] }> = [];
    const view = renderWithKeys(
      <Harness busy={false} onSubmit={(text, files) => got.push({ text, files })} expose={() => {}} />,
    );
    try {
      await view.type(KEYS.paste(pdf));
      // ctrl+w twice removes "[PDF #1] " word by word.
      await view.type('\x17');
      await view.type('\x17');
      await view.type('\x17');
      await view.type('text');
      await view.type(KEYS.enter);
      expect(got[0]!.files).toEqual([]);
    } finally {
      view.unmount();
    }
  });
});
