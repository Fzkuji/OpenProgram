import React from 'react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { renderWithUpdates } from './renderToFrame.js';
import { GoalStatus } from '../src/components/GoalStatus.js';
import { Box, Text } from '../src/runtime/index';
import type { BackendClient, ConnectionState, WsEnvelope } from '../src/ws/client.js';

const mounted: Array<() => void> = [];
afterEach(() => { mounted.splice(0).forEach((close) => close()); vi.restoreAllMocks(); });
function connection() {
  const frames = new Set<(frame: WsEnvelope) => void>();
  const states = new Set<(state: ConnectionState) => void>();
  const client = {
    getState: () => 'connected',
    on: (fn: (frame: WsEnvelope) => void) => { frames.add(fn); return () => frames.delete(fn); },
    onState: (fn: (state: ConnectionState) => void) => { states.add(fn); return () => states.delete(fn); },
  } as unknown as BackendClient;
  return { client, frames, states,
    emit: (frame: unknown) => frames.forEach((fn) => fn(frame as WsEnvelope)),
    state: (value: ConnectionState) => states.forEach((fn) => fn(value)),
  };
}
const goal = (version = 1, status = 'paused_recoverable') => ({
  goal_id: 'g1', version, status, phase: 'work', text: 'Write the survey',
  checklist: [{ done: true }, { done: false }], questions: [{ status: 'pending' }],
});
const reply = (value: unknown) => new Response(JSON.stringify({ goal: value }));
function mount(client: BackendClient, sessionId = 's1') {
  const tree = (id: string) => <Box flexDirection="column"><GoalStatus client={client} sessionId={id} /><Text>Prompt remains available</Text></Box>;
  const view = renderWithUpdates(tree(sessionId));
  mounted.push(view.unmount);
  return { ...view, select: (id: string) => view.rerender(tree(id)), text: async () => {
    // Read a full repaint, not one incremental terminal write.
    view.repaint();
    return (view.lastFrame() ?? '').replace(/\x1b\[[0-9;?]*[A-Za-z]/g, '');
  } };
}

describe('TUI Goal status', () => {
  it.each([reply(goal(1)), new Response('', { status: 404 })])('does not replace a live snapshot with a stale in-flight HTTP reply', async (response) => {
    let resolve!: (response: Response) => void;
    vi.spyOn(globalThis, 'fetch').mockReturnValue(new Promise((done) => { resolve = done; }));
    const c = connection(); const view = mount(c.client);
    c.emit({ type: 'goal_update', data: { session_id: 's1', goal: goal(5, 'evaluating') } });
    await vi.waitFor(async () => expect(await view.text()).toContain('evaluating'));
    resolve(response);
    await new Promise((done) => setTimeout(done, 30));
    expect(await view.text()).toContain('evaluating');
  });

  it('ignores an old session response and foreign updates after switching', async () => {
    let resolve!: (response: Response) => void;
    const fetch = vi.spyOn(globalThis, 'fetch').mockReturnValueOnce(new Promise((done) => { resolve = done; }))
      .mockResolvedValueOnce(reply({ ...goal(), text: 'Second goal' }));
    const c = connection(); const view = mount(c.client);
    await vi.waitFor(() => expect(fetch).toHaveBeenCalledTimes(1));
    view.select('s2');
    await vi.waitFor(async () => expect(await view.text()).toContain('Second goal'));
    resolve(reply(goal(99)));
    c.emit({ type: 'goal_update', data: { session_id: 's1', goal: goal(100) } });
    await new Promise((done) => setTimeout(done, 30));
    expect(await view.text()).toContain('Second goal');
    expect(await view.text()).not.toContain('Write the survey');
    expect(fetch.mock.calls[0][1]?.signal?.aborted).toBe(true);
  });

  it('renders loaded Goal state at narrow widths and keeps a no-Goal session empty', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response('', { status: 404 }));
    const c = connection(); const view = mount(c.client);
    await vi.waitFor(async () => expect(await view.text()).not.toContain('Goal status unavailable'));
    c.emit({ type: 'session_loaded', data: { id: 's1', goal: goal(7) } });
    await view.resize(40);
    await vi.waitFor(async () => expect(await view.text()).toContain('Write the survey'));
    expect(await view.text()).toContain('Details and controls: /goal');
    for (const status of ['achieved', 'impossible', 'cancelled', 'cleared']) {
      c.emit({ type: 'goal_update', data: { session_id: 's1', goal: goal(8, status) } });
      await vi.waitFor(async () => expect(await view.text()).not.toContain('Write the survey'));
      expect(await view.text()).toContain('Prompt remains available');
    }
  });

  it('hydrates, applies live versions and keeps async questions outside the prompt', async () => {
    const fetch = vi.spyOn(globalThis, 'fetch').mockResolvedValue(reply(goal()));
    const c = connection(); const view = mount(c.client);
    await vi.waitFor(async () => expect(await view.text()).toContain('Write the survey'));
    expect(fetch.mock.calls[0][0]).toContain('/api/sessions/s1/goal');
    expect(await view.text()).toContain('1/2');
    expect(await view.text()).toContain('1 pending');
    c.emit({ type: 'goal_update', data: { session_id: 's1', goal: goal(3, 'evaluating') } });
    await vi.waitFor(async () => expect(await view.text()).toContain('evaluating'));
    c.emit({ type: 'session_loaded', data: { id: 's1', goal: goal(2) } });
    await new Promise((resolve) => setTimeout(resolve, 30));
    expect(await view.text()).toContain('evaluating');
    c.emit({ type: 'chat_response', data: { type: 'goal_update', session_id: 's1', goal: goal(4, 'achieved') } });
    await vi.waitFor(async () => expect(await view.text()).not.toContain('Write the survey'));
    expect(await view.text()).toContain('Prompt remains available');
  });

  it('keeps the last snapshot offline and refreshes on reconnection', async () => {
    const fetch = vi.spyOn(globalThis, 'fetch').mockResolvedValueOnce(reply(goal()))
      .mockRejectedValueOnce(new Error('private transport detail'));
    const c = connection(); const view = mount(c.client);
    await vi.waitFor(async () => expect(await view.text()).toContain('Write the survey'));
    c.state('disconnected');
    await vi.waitFor(async () => expect(await view.text()).toContain('offline'));
    c.state('connected');
    await vi.waitFor(async () => expect(await view.text()).toContain('refresh failed'));
    expect(await view.text()).toContain('Write the survey');
    expect(await view.text()).not.toContain('private transport detail');
    expect(fetch).toHaveBeenCalledTimes(2);
    view.unmount();
    expect(c.frames.size).toBe(0); expect(c.states.size).toBe(0);
  });
});
