import { describe, it, expect } from 'vitest';
import { readdirSync, readFileSync } from 'fs';
import { join } from 'path';
import { cancelCurrentExecution, cancelExecution, cancelSend, stopForeground } from '../src/screens/repl/cancelExecution.js';
import type { WsEnvelope, WsListener, WsRequest } from '../src/ws/client.js';

function fakeClient(capabilities: string[] = []) {
  const sent: Record<string, unknown>[] = [];
  const listeners = new Set<WsListener>();
  return {
    sent,
    listeners,
    hasCapability(name: string) { return capabilities.includes(name); },
    send(req: WsRequest) { sent.push(req as unknown as Record<string, unknown>); },
    on(listener: WsListener) {
      listeners.add(listener);
      return () => { listeners.delete(listener); };
    },
    emit(ev: unknown) { for (const l of [...listeners]) l(ev as WsEnvelope); },
  };
}

const rejected = (commandId: string, code: string, status: string, version: number) => ({
  type: 'execution.command.updated',
  command: {
    command_id: commandId,
    execution_id: 'exec-1',
    status: 'rejected',
    rejection_code: code,
    latest_snapshot: { execution_id: 'exec-1', status, status_version: version },
  },
});

describe('cancelExecution', () => {
  it('sends the cancel again once, same command id, at the version a stale refusal names', () => {
    const client = fakeClient();
    cancelExecution(client, 'exec-1', 3, 'cmd-1');
    expect(client.sent).toEqual([{
      type: 'execution.command', action: 'execution.cancel', command_id: 'cmd-1',
      execution_id: 'exec-1', expected_version: 3, payload: {},
    }]);

    client.emit(rejected('cmd-1', 'stale_version', 'running', 5));
    expect(client.sent).toHaveLength(2);
    expect(client.sent[1]).toEqual({ ...client.sent[0], expected_version: 5 });

    // Only once: a second stale refusal ends it.
    client.emit(rejected('cmd-1', 'stale_version', 'running', 6));
    expect(client.sent).toHaveLength(2);
    expect(client.listeners.size).toBe(0);
  });

  it('reads the command under data as the worker may wrap it', () => {
    const client = fakeClient();
    cancelExecution(client, 'exec-1', 3, 'cmd-1');
    const frame = rejected('cmd-1', 'stale_version', 'paused', 4);
    client.emit({ type: frame.type, data: { command: frame.command } });
    expect(client.sent.map((f) => f.expected_version)).toEqual([3, 4]);
  });

  it('does not resend when the execution is already stopping or ended', () => {
    for (const status of ['cancelling', 'cancelled', 'completed']) {
      const client = fakeClient();
      cancelExecution(client, 'exec-1', 3, 'cmd-1');
      client.emit(rejected('cmd-1', 'stale_version', status, 7));
      expect(client.sent).toHaveLength(1);
      expect(client.listeners.size).toBe(0);
    }
  });

  it('ignores other commands and other refusals, and lets go once applied', () => {
    const client = fakeClient();
    cancelExecution(client, 'exec-1', 3, 'cmd-1');
    client.emit(rejected('cmd-other', 'stale_version', 'running', 5));
    expect(client.sent).toHaveLength(1);
    client.emit(rejected('cmd-1', 'invalid_transition', 'running', 5));
    expect(client.sent).toHaveLength(1);
    expect(client.listeners.size).toBe(0);

    const second = fakeClient();
    cancelExecution(second, 'exec-1', 3, 'cmd-2');
    second.emit({ type: 'execution.command.updated', command: { command_id: 'cmd-2', status: 'applied' } });
    expect(second.listeners.size).toBe(0);
  });
});

describe('a worker that announces what its stop does', () => {
  it('cancel_ignores_version: the cancel goes out once and nothing waits on a refusal', () => {
    const client = fakeClient(['cancel_ignores_version']);
    cancelExecution(client, 'exec-1', 3, 'cmd-1');
    expect(client.sent).toHaveLength(1);
    expect(client.listeners.size).toBe(0);
    client.emit(rejected('cmd-1', 'stale_version', 'running', 5));
    expect(client.sent).toHaveLength(1);
  });

  it('cancel_by_request: a stop before the ack names the send', () => {
    const client = fakeClient(['cancel_ignores_version', 'cancel_by_request']);
    const notes: string[] = [];
    const send = { sessionId: 'local_abc', requestId: 'req-1' };
    expect(cancelCurrentExecution(client, undefined, undefined, (t) => notes.push(t), () => 'cmd-1', send)).toBe(true);
    expect(client.sent).toEqual([{
      type: 'execution.command', action: 'execution.cancel', command_id: 'cmd-1',
      session_id: 'local_abc', client_request_id: 'req-1', payload: {},
    }]);
    expect(notes).toEqual([]);
    // Once the ack named the execution, the stop names it.
    expect(cancelCurrentExecution(client, 'exec-1', 4, () => {}, () => 'cmd-2', send)).toBe(true);
    expect(client.sent[1]).toMatchObject({ execution_id: 'exec-1', command_id: 'cmd-2' });
  });

  it('an older worker is not sent a stop it cannot read; the REPL says so as before', () => {
    const client = fakeClient();
    const notes: string[] = [];
    const send = { sessionId: 'local_abc', requestId: 'req-1' };
    expect(cancelCurrentExecution(client, undefined, undefined, (t) => notes.push(t), () => 'cmd-1', send)).toBe(false);
    expect(client.sent).toEqual([]);
    expect(notes).toEqual(['Cancel unavailable: no current execution version']);
  });

  it('cancelSend builds the frame that names a send', () => {
    const client = fakeClient(['cancel_by_request']);
    expect(cancelSend(client, { sessionId: 's', requestId: 'r' }, 'c')).toBe('c');
    expect(client.sent[0]).toMatchObject({ session_id: 's', client_request_id: 'r', command_id: 'c' });
  });
});

describe('the REPL sends its first turn with a session id and a request id', () => {
  it('names the send it may stop', () => {
    const source = readFileSync('src/screens/REPL.tsx', 'utf8');
    expect(source).toMatch(/const sessionId = conversationId \?\? `local_\$\{randomLocalId\(\)\}`/);
    expect(source).toMatch(/session_id: sessionId,\s*client_request_id: requestId,/);
    expect(source).toMatch(/pendingSend: pendingSendRef\.current,/);
  });
});

describe('useWsEvents keeps the version a refusal names', () => {
  it('stores the latest_snapshot version of the current execution', async () => {
    const React = await import('react');
    const { renderWithUpdates } = await import('./renderToFrame.js');
    const { useWsEvents } = await import('../src/screens/repl/useWsEvents.js');
    let listener: WsListener = () => {};
    const versionRef = { current: 3 as number | undefined };
    const ctx = {
      conversationId: 's-1', setConnState: () => {},
      setJobsList: () => {}, setSelectedJob: () => {},
      executionIdRef: { current: 'exec-1' },
      executionVersionRef: versionRef,
      client: {
        getState: () => 'connected', send: () => {},
        on: (fn: WsListener) => { listener = fn; return () => {}; },
        onState: (fn: (s: string) => void) => { fn('connected'); return () => {}; },
      },
    } as never;
    function Probe() { useWsEvents(ctx); return null; }
    const view = renderWithUpdates(React.createElement(Probe));
    try {
      listener(rejected('cmd-1', 'stale_version', 'running', 9) as unknown as WsEnvelope);
      expect(versionRef.current).toBe(9);
    } finally { view.unmount(); }
  });
});

describe('cancelCurrentExecution (REPL Ctrl-C and stop)', () => {
  it('cancels the current execution and resends once on a stale refusal', () => {
    const client = fakeClient();
    const notes: string[] = [];
    expect(cancelCurrentExecution(client, 'exec-1', 3, (t) => notes.push(t), () => 'cmd-1')).toBe(true);
    client.emit(rejected('cmd-1', 'stale_version', 'running', 5));
    expect(client.sent.map((f) => [f.command_id, f.expected_version])).toEqual([['cmd-1', 3], ['cmd-1', 5]]);
    expect(notes).toEqual([]);
  });

  it('cancels an execution whose version it never saw; says so only without an execution', () => {
    const client = fakeClient();
    const notes: string[] = [];
    expect(cancelCurrentExecution(client, 'exec-1', undefined, (t) => notes.push(t), () => 'x')).toBe(true);
    expect(client.sent.map((f) => [f.execution_id, f.expected_version])).toEqual([['exec-1', 0]]);
    expect(cancelCurrentExecution(client, undefined, 3, (t) => notes.push(t), () => 'y')).toBe(false);
    expect(client.sent).toHaveLength(1);
    expect(notes).toEqual(['Cancel unavailable: no current execution version']);
  });
});

describe('every TUI cancel goes through cancelExecution', () => {
  // A cancel sent straight with client.send would skip the stale-version resend.
  function sources(dir: string): string[] {
    return readdirSync(dir, { withFileTypes: true }).flatMap((entry) => {
      const path = join(dir, entry.name);
      if (entry.isDirectory()) return sources(path);
      return /\.tsx?$/.test(entry.name) ? [path] : [];
    });
  }

  it('no other source builds an execution.cancel frame', () => {
    const allowed = new Set([
      join('src', 'screens', 'repl', 'cancelExecution.ts'),
      join('src', 'ws', 'client.ts'), // the request type lists the action
    ]);
    const senders = sources('src').filter(
      (path) => !allowed.has(path) && readFileSync(path, 'utf8').includes("'execution.cancel'"),
    );
    expect(senders).toEqual([]);
  });

  it('the REPL stop and the Jobs Stop job use it', () => {
    expect(readFileSync('src/screens/REPL.tsx', 'utf8')).toMatch(/stopForeground\(\{\s*client,/);
    expect(readFileSync('src/screens/repl/pickerRouter.tsx', 'utf8')).toMatch(/cancelExecution\(\s*client,/);
  });
});

describe('stopForeground (Ctrl-C or Esc)', () => {
  function press(client: ReturnType<typeof fakeClient>, state: { commandId: string | null }, notes: string[], over: Record<string, unknown> = {}) {
    let n = 0;
    return stopForeground({
      client, running: true, executionId: 'exec-1', expectedVersion: 2,
      pendingSend: null, sessionId: 's-1', operationId: undefined, state,
      pushSystem: (t) => notes.push(t), newCommandId: () => `cmd-${++n}`,
      ...over,
    } as Parameters<typeof stopForeground>[0]);
  }

  it('stops a running turn on the first press and says so once', () => {
    const client = fakeClient(['cancel_ignores_version']);
    const state = { commandId: null };
    const notes: string[] = [];
    expect(press(client, state, notes)).toBe('turn');
    expect(client.sent).toHaveLength(1);
    expect(notes).toEqual(['Cancel execution']);
  });

  it('repeated presses resend the same stop with one line', () => {
    const client = fakeClient(['cancel_ignores_version']);
    const state = { commandId: null };
    const notes: string[] = [];
    press(client, state, notes);
    press(client, state, notes);
    press(client, state, notes);
    expect(client.sent.map((f) => f.command_id)).toEqual(['cmd-1', 'cmd-1', 'cmd-1']);
    expect(notes).toEqual(['Cancel execution']);
  });

  it('stops a send that has no ack yet by its request id', () => {
    const client = fakeClient(['cancel_ignores_version', 'cancel_by_request']);
    const notes: string[] = [];
    expect(press(client, { commandId: null }, notes, {
      executionId: null, pendingSend: { sessionId: 's-1', requestId: 'req-1' },
    })).toBe('turn');
    expect(client.sent[0]).toMatchObject({ session_id: 's-1', client_request_id: 'req-1' });
  });

  it('stops a /compact by its op id when no turn runs', () => {
    const client = fakeClient();
    const notes: string[] = [];
    expect(press(client, { commandId: null }, notes, { running: false, operationId: 'op-1' })).toBe('operation');
    expect(client.sent).toEqual([{ action: 'operation.cancel', session_id: 's-1', op_id: 'op-1' }]);
    expect(notes).toEqual([]);
  });

  it('returns null when nothing runs', () => {
    const client = fakeClient();
    expect(press(client, { commandId: null }, [], { running: false })).toBe(null);
    expect(client.sent).toEqual([]);
  });
});
