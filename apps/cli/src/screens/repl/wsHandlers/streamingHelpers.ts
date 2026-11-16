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
import { readFileSync } from 'fs';
import { Turn, ToolCall, TurnBlock, TodoItem } from '../../../components/Turn.js';
import { parseArgs, toolKind } from '../../../components/tools/toolView.js';

type SetStreaming = React.Dispatch<React.SetStateAction<Turn | null>>;

export const newAssistantTurn = (): Turn => ({
  id: `a-${Date.now()}`,
  role: 'assistant',
  text: '',
  blocks: [],
  streaming: true,
});

/** The flat text a text frame adds: on the frame that opens a passage after
 *  a tool call the server names the blank line that goes before its delta. */
export const streamText = (event: { text?: string; separator?: string }): string =>
  (event.separator ?? '') + (event.text ?? '');

export const upsertStreamingText = (
  setStreaming: SetStreaming,
  delta: string,
  separator = '',
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
      // The blocks keep the model's own text; the separator is only in the flat text.
      text: (base.text ?? '') + separator + delta,
      blocks,
      streaming: true,
    };
  });
};

export const appendStreamingTool = (
  setStreaming: SetStreaming,
  tool: string,
  input?: string,
  toolCallId?: string,
): void => {
  setStreaming((s) => {
    const base: Turn = s ?? newAssistantTurn();
    const blocks = (base.blocks ?? []).slice();
    const callId = `t-${Date.now()}-${blocks.length}`;
    const call: ToolCall = {
      id: callId, tool, input, status: 'running', startedAt: Date.now(),
      ...(toolCallId ? { callId: toolCallId } : {}),
    };
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

export interface ToolResultEvent {
  tool: string;
  result?: string;
  is_error?: boolean;
  tool_call_id?: string | null;
  full_output?: { total_lines?: number | null } | null;
}

/** The file line an edit's new text starts on, read from the edited
 *  file; undefined when the file is not on this machine or the text is
 *  not found once. */
export function editStartLine(
  input: string | undefined,
  read: (path: string) => string = (path) => readFileSync(path, 'utf8'),
): number | undefined {
  const args = parseArgs(input);
  const path = typeof args.file_path === 'string' ? args.file_path : typeof args.path === 'string' ? args.path : '';
  const after = typeof args.new_string === 'string' ? args.new_string : '';
  if (!path || !after) return undefined;
  try {
    const text = read(path);
    const at = text.indexOf(after);
    if (at < 0 || text.indexOf(after, at + 1) >= 0) return undefined;
    return text.slice(0, at).split('\n').length;
  } catch {
    return undefined;
  }
}

/** Finish the call a result belongs to: by its call id when the frame
 *  has one, else the newest running call with that tool name. */
export function completeToolBlocks(
  blocks: TurnBlock[],
  ev: ToolResultEvent,
  now: number = Date.now(),
  locateEdit: (input?: string) => number | undefined = editStartLine,
): TurnBlock[] {
  const out = blocks.slice();
  const byId = ev.tool_call_id
    ? out.findIndex((b) => b.kind === 'tool' && b.call.callId === ev.tool_call_id)
    : -1;
  let index = byId;
  if (index < 0) {
    for (let i = out.length - 1; i >= 0; i--) {
      const b = out[i];
      if (b?.kind === 'tool' && b.call.tool === ev.tool && b.call.status === 'running') { index = i; break; }
    }
  }
  const block = out[index];
  if (!block || block.kind !== 'tool') return blocks;
  const total = ev.full_output?.total_lines;
  const startLine = !ev.is_error && toolKind(block.call.tool) === 'edit' ? locateEdit(block.call.input) : undefined;
  out[index] = {
    kind: 'tool',
    call: {
      ...block.call,
      status: ev.is_error ? 'error' : 'done',
      result: ev.result,
      endedAt: now,
      ...(typeof total === 'number' ? { totalLines: total } : {}),
      ...(startLine !== undefined ? { startLine } : {}),
    },
  };
  return out;
}

/** Put a task's steps on the newest task call of the turn. */
export function attachTodoBoard(blocks: TurnBlock[], board: TodoItem[], title?: string): TurnBlock[] {
  for (let i = blocks.length - 1; i >= 0; i--) {
    const b = blocks[i];
    if (b?.kind === 'tool' && toolKind(b.call.tool) === 'todo') {
      const out = blocks.slice();
      out[i] = { kind: 'tool', call: { ...b.call, board, ...(title ? { boardTitle: title } : {}) } };
      return out;
    }
  }
  return blocks;
}

/** A segment's `todos` as the steps a task call shows. */
export function todoBoard(todos: unknown): TodoItem[] {
  if (!Array.isArray(todos)) return [];
  return todos.flatMap((t) => {
    if (!t || typeof t !== 'object') return [];
    const row = t as Record<string, unknown>;
    const subject = typeof row.subject === 'string' ? row.subject : '';
    return subject ? [{ subject, status: typeof row.status === 'string' ? row.status : 'pending' }] : [];
  });
}

/** The segment of a `todos_update` frame's `changed` that the shown agent
 *  draws: the last one of its scope ("" is the conversation's own agent).
 *  A frame closing one task and starting another lists the new one last. */
export function changedTask(changed: unknown, scope = ''): { title: string; board: TodoItem[] } | null {
  if (!Array.isArray(changed)) return null;
  for (let i = changed.length - 1; i >= 0; i--) {
    const row = changed[i];
    if (!row || typeof row !== 'object') continue;
    const segment = row as Record<string, unknown>;
    if ((typeof segment.scope === 'string' ? segment.scope : '') !== scope) continue;
    return { title: typeof segment.title === 'string' ? segment.title : '', board: todoBoard(segment.todos) };
  }
  return null;
}
