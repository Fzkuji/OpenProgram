/**
 * Streaming-turn block manipulation helpers.
 *
 * Each WS `chat_response` event with a `stream_event` payload either
 * appends a text delta or starts/updates a tool call on the
 * currently-streaming assistant turn. The helpers below own the
 * `setStreaming` SetStateAction shape so the WS handler stays
 * focused on dispatch.
 *
 * They take a SetState updater rather than a Turn directly so each
 * call is one React commit — no read-modify-write race when two
 * envelopes land in the same tick.
 */
import { Turn, ToolCall, TurnBlock } from '../../../components/Turn.js';

type SetStreaming = React.Dispatch<React.SetStateAction<Turn | null>>;

export const newAssistantTurn = (): Turn => ({
  id: `a-${Date.now()}`,
  role: 'assistant',
  text: '',
  blocks: [],
  streaming: true,
});

export const upsertStreamingText = (
  setStreaming: SetStreaming,
  delta: string,
): void => {
  setStreaming((s) => {
    const base: Turn = s ?? newAssistantTurn();
    const blocks = (base.blocks ?? []).slice();
    const last = blocks[blocks.length - 1];
    if (last && last.kind === 'text') {
      blocks[blocks.length - 1] = { kind: 'text', text: last.text + delta };
    } else {
      blocks.push({ kind: 'text', text: delta });
    }
    return {
      ...base,
      text: (base.text ?? '') + delta,
      blocks,
      streaming: true,
    };
  });
};

export const appendStreamingTool = (
  setStreaming: SetStreaming,
  tool: string,
  input?: string,
): void => {
  setStreaming((s) => {
    const base: Turn = s ?? newAssistantTurn();
    const blocks = (base.blocks ?? []).slice();
    const callId = `t-${Date.now()}-${blocks.length}`;
    const call: ToolCall = { id: callId, tool, input, status: 'running' };
    blocks.push({ kind: 'tool', call });
    return { ...base, blocks, streaming: true };
  });
};

export const finalizeStreamingTools = (setStreaming: SetStreaming): void => {
  setStreaming((s) => {
    if (!s) return s;
    const blocks = (s.blocks ?? []).map((b): TurnBlock =>
      b.kind === 'tool' && b.call.status === 'running'
        ? { kind: 'tool', call: { ...b.call, status: 'done' as const } }
        : b,
    );
    return { ...s, blocks };
  });
};
