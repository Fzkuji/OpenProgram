/** The rows of a read, as the store holds them.
 *
 *  A read sends each tool body once, on its block. A reply's flat `tools` list
 *  gets the input and result back here, so everything past this point sees the
 *  same row a live stream builds. The turns are left as sent: a history window
 *  counts their bytes as they travelled. */
import type { WireTurn } from "../history/history-window.ts";
import type { AssistantBlock, ChatMsg, ChatToolCall } from "../../session-store/types.ts";

export function withToolBodies(row: ChatMsg): ChatMsg {
  const attachCards = row.attachCards?.map(withToolBodies);
  const filled: ChatMsg = attachCards ? { ...row, attachCards } : row;
  if (!row.tools?.length) return filled;
  const calls: AssistantBlock[] = (row.blocks ?? []).filter((block) => block.type === "tool");
  let at = 0;
  const tools = row.tools.map((tool): ChatToolCall => {
    // Both lists are in stored order, so a repeated call id pairs by position.
    while (at < calls.length && calls[at].tool_call_id !== tool.id) at += 1;
    const block = calls[at++];
    return block ? { ...tool, input: block.input ?? "", result: block.result } : tool;
  });
  return { ...filled, tools };
}

export function rowsOfTurns(turns: readonly WireTurn[]): ChatMsg[] {
  return turns.flatMap((turn) => turn.rows as unknown as ChatMsg[]).map(withToolBodies);
}
