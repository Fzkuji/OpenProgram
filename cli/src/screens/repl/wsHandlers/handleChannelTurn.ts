/**
 * channel_turn WS envelope handler.
 *
 * Channels worker emits this once per inbound turn (wechat /
 * telegram / ...) after persisting the user msg + assistant reply.
 * Two paths:
 *
 *   - Current conv: append both turns to the live transcript so the
 *     view updates without a /resume refresh.
 *   - Foreign conv: surface in the ambient activity feed so the
 *     user sees that the channel processed a turn even when staring
 *     at a different session.
 */
import { Turn } from '../../../components/Turn.js';
import type { WsEventsCtx } from '../useWsEvents.js';

export interface ChannelTurnPayload {
  conv_id: string;
  user?: { id?: string; text?: string; source?: string; peer_display?: string };
  assistant?: { id?: string; text?: string };
}

export function handleChannelTurn(
  d: ChannelTurnPayload,
  c: WsEventsCtx,
  markSessionLive: (convId?: string) => void,
): void {
  if (d.conv_id !== c.conversationId) {
    if (d.conv_id) {
      c.setChannelActivityByConv((m) => {
        const prev = m[d.conv_id] ?? {
          convId: d.conv_id,
          streamingText: '',
          streaming: false,
          lastUpdate: Date.now(),
        };
        return {
          ...m,
          [d.conv_id]: {
            ...prev,
            source: d.user?.source ?? prev.source,
            peerDisplay: d.user?.peer_display ?? prev.peerDisplay,
            userText: d.user?.text ?? prev.userText,
            finalText: d.assistant?.text ?? prev.finalText,
            streaming: false,
            lastUpdate: Date.now(),
          },
        };
      });
      markSessionLive(d.conv_id);
    }
    return;
  }

  const newTurns: Turn[] = [];
  if (d.user?.text) {
    const tag = d.user.peer_display
      ? `[${d.user.source ?? 'channel'}:${d.user.peer_display}] `
      : '';
    newTurns.push({
      id: d.user.id ?? `cu-${Date.now()}`,
      role: 'user',
      text: tag + d.user.text,
    });
  }
  if (d.assistant?.text) {
    newTurns.push({
      id: d.assistant.id ?? `ca-${Date.now()}`,
      role: 'assistant',
      text: d.assistant.text,
    });
  }
  if (newTurns.length > 0) {
    c.setCommitted((m) => [...m, ...newTurns]);
    markSessionLive(d.conv_id);
  }
}
