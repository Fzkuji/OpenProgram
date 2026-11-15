import { describe, expect, it, vi } from 'vitest';
import { handleChannelTurn, type ChannelTurnPayload } from '../src/screens/repl/wsHandlers/handleChannelTurn.js';
import type { WsEventsCtx } from '../src/screens/repl/useWsEvents.js';
import type { Turn } from '../src/components/Turn.js';

describe('handleChannelTurn text boundary', () => {
  it.each([null, 1, { text: 'unexpected' }, ['unexpected']])(
    'preserves foreign text and drops malformed focused text: %j',
    (text) => {
      let background = {
        foreign: { convId: 'foreign', userText: 'previous user', finalText: 'previous reply' },
      };
      let committed: Turn[] = [];
      const ctx = {
        conversationId: 'focused',
        setChannelActivityByConv: (update: (rows: typeof background) => typeof background) => {
          background = update(background);
        },
        setCommitted: (update: (rows: Turn[]) => Turn[]) => { committed = update(committed); },
      } as unknown as WsEventsCtx;
      const payload = {
        session_id: 'foreign', user: { text }, assistant: { text },
      } as unknown as ChannelTurnPayload;
      handleChannelTurn(payload, ctx, vi.fn());
      expect(background.foreign).toMatchObject({
        userText: 'previous user', finalText: 'previous reply', streaming: false,
      });
      handleChannelTurn({ ...payload, session_id: 'focused' }, ctx, vi.fn());
      expect(committed).toEqual([]);
    },
  );

  it('retains valid focused messages and explicit empty foreign text', () => {
    let background: Record<string, unknown> = {};
    let committed: Turn[] = [];
    const ctx = {
      conversationId: 'focused',
      setChannelActivityByConv: (update: (rows: typeof background) => typeof background) => {
        background = update(background);
      },
      setCommitted: (update: (rows: Turn[]) => Turn[]) => { committed = update(committed); },
    } as unknown as WsEventsCtx;
    handleChannelTurn({ session_id: 'focused', user: { text: 'hello' }, assistant: { text: 'reply' } }, ctx, vi.fn());
    expect(committed).toMatchObject([{ role: 'user', text: 'hello' }, { role: 'assistant', text: 'reply' }]);
    handleChannelTurn({ session_id: 'foreign', user: { text: '' }, assistant: { text: '' } }, ctx, vi.fn());
    expect(background.foreign).toMatchObject({ userText: '', finalText: '' });
  });
});
