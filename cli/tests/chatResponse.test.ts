import { describe, expect, it, vi } from 'vitest';

import { handleChatResponse } from '../src/screens/repl/wsHandlers/handleChatResponse.js';
import type { WsEventsCtx } from '../src/screens/repl/useWsEvents.js';
import type { Turn } from '../src/components/Turn.js';


describe('handleChatResponse', () => {
  it('commits local command output and finishes the turn', () => {
    let committed: Turn[] = [];
    const finishTurn = vi.fn();
    const setStreaming = vi.fn();
    const ctx = {
      conversationId: 'session-1',
      setStreaming,
      setCommitted: (update: (rows: Turn[]) => Turn[]) => {
        committed = update(committed);
      },
      finishTurn,
    } as unknown as WsEventsCtx;

    handleChatResponse(
      { type: 'local_command', session_id: 'session-1', content: 'Add project settings' },
      ctx,
      vi.fn(),
    );

    expect(setStreaming).toHaveBeenCalledWith(null);
    expect(committed).toMatchObject([
      { role: 'system', text: 'Add project settings' },
    ]);
    expect(finishTurn).toHaveBeenCalledOnce();
  });

  it('records local output for a session that is no longer focused', () => {
    let background: Record<string, unknown> = {};
    const ctx = {
      conversationId: 'session-2',
      setChannelActivityByConv: (
        update: (rows: Record<string, unknown>) => Record<string, unknown>,
      ) => {
        background = update(background);
      },
    } as unknown as WsEventsCtx;

    handleChatResponse(
      { type: 'local_command', session_id: 'session-1', content: 'Add project settings' },
      ctx,
      vi.fn(),
    );

    expect(background).toMatchObject({
      'session-1': {
        convId: 'session-1',
        finalText: 'Add project settings',
        streaming: false,
      },
    });
  });
});
