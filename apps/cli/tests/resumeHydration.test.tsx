import React from 'react';
import { expect, it, vi } from 'vitest';
import { renderWithUpdates } from './renderToFrame.js';
import { useWsEvents, type WsEventsCtx } from '../src/screens/repl/useWsEvents.js';
import type { ConnectionState } from '../src/ws/client.js';

vi.mock('../src/utils/history.js', () => ({ trimHistoryFile: vi.fn() }));

it.each(['connecting', 'connected'] as const)('loads the selected session on %s startup and reconnection', async (initial) => {
  let changed: (state: ConnectionState) => void = () => {};
  const send = vi.fn();
  const ctx = {
    conversationId: 'saved-goal-session', setConnState: vi.fn(),
    client: {
      getState: () => initial,
      send,
      on: () => () => {},
      onState: (fn: typeof changed) => { changed = fn; fn(initial); return () => {}; },
    },
  } as unknown as WsEventsCtx;
  function Probe() { useWsEvents(ctx); return null; }
  const view = renderWithUpdates(<Probe />);
  try {
    await vi.waitFor(() => expect(send).toHaveBeenCalledWith({ action: 'stats' }));
    if (initial === 'connecting') changed('connected');
    expect(send).toHaveBeenCalledWith({ action: 'load_session', session_id: 'saved-goal-session' });
    expect(send.mock.calls.filter(([request]) => request.action === 'load_session')).toHaveLength(1);
    send.mockClear();
    ctx.conversationId = 'newly-selected-session';
    changed('disconnected');
    expect(send).not.toHaveBeenCalled();
    changed('connected');
    expect(send).toHaveBeenCalledExactlyOnceWith({ action: 'load_session', session_id: 'newly-selected-session' });
  } finally { view.unmount(); }
});
