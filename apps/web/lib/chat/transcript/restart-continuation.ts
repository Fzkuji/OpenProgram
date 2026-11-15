/** A worker restart cuts a reply off, and a new turn carries the work on
 *  (runtime/conversation/restart_turn.py). Its prompt is an instruction to
 *  the model, stamped ``trigger: "restart"`` and drawn as nothing, and the
 *  chat draws the two replies as one: the cut-off reply hides, and the
 *  reply that continues it opens with its steps and a "restarted" step
 *  where the restart fell. Collapsed, the reply reads as if never cut. */
import type { AssistantBlock, ChatMsg, TurnFileSummary } from "../../session-store/types.ts";

/** The rows ``reply`` continues, oldest first: exactly those its
 *  ``continues`` names (stamped by the server, all of its own turn). */
export function continuedRows(rows: Readonly<Record<string, ChatMsg | undefined>>, reply: ChatMsg): ChatMsg[] {
  return (reply.continues ?? []).flatMap((id) => {
    const row = rows[id];
    return row ? [row] : [];
  });
}

/** A reply's ordered blocks, adapting records that kept only the flat
 *  thinking/tools fields. */
export function fallbackBlocks(msg: ChatMsg): AssistantBlock[] {
  return [
    ...(msg.thinking ? [{ type: "thinking" as const, text: msg.thinking }] : []),
    ...(msg.tools ?? []).map((tool): AssistantBlock => ({
      type: "tool", tool: tool.tool, tool_call_id: tool.id,
      input: tool.input, result: tool.result,
      is_error: tool.isError || tool.status === "error" || tool.status === "cancelled",
      outcome: tool.status === "cancelled" ? "cancelled" : undefined,
      truncated: tool.truncated, total_bytes: tool.totalBytes,
      message_id: tool.messageId, node_id: tool.nodeId,
      ...(tool.providerExecuted ? { provider_executed: true } : {}),
    })),
  ];
}

/** Everything one reply draws, its final text included. ``prose`` strips
 *  what the bubble draws apart from the text (attachment markers). */
function partBlocks(msg: ChatMsg, prose: (content: string) => string): AssistantBlock[] {
  const blocks = msg.blocks?.length ? msg.blocks : fallbackBlocks(msg);
  const text = msg.content ? prose(msg.content) : "";
  return text && !blocks.some((b) => b.type === "text") ? [...blocks, { type: "text", text }] : blocks;
}

const addCounts = (a: number | null, b: number | null) => (a === null || b === null ? null : a + b);

/** The files every part of a joined reply changed. A file changed in two
 *  parts is one row, with both parts' line counts. */
function joinedTurnFiles(parts: ChatMsg[]): TurnFileSummary | undefined {
  const summaries = parts.flatMap((part) => (part.turnFiles ? [part.turnFiles] : []));
  if (summaries.length < 2) {
    const only = summaries[0];
    return only && only !== parts[parts.length - 1].turnFiles ? { ...only, spansTurns: true } : only;
  }
  const files = new Map<string, TurnFileSummary["files"][number]>();
  for (const file of summaries.flatMap((summary) => summary.files)) {
    const seen = files.get(file.path);
    files.set(file.path, seen
      ? { ...file, added: addCounts(seen.added, file.added), removed: addCounts(seen.removed, file.removed) }
      : file);
  }
  // A bounded part counts more files than it lists; they are not in the map.
  const unlisted = summaries.reduce((total, summary) => total + Math.max(0, summary.file_count - summary.files.length), 0);
  const last = summaries[summaries.length - 1];
  return {
    ...last,
    spansTurns: true,
    files: [...files.values()],
    file_count: files.size + unlisted,
    added: summaries.map((summary) => summary.added).reduce(addCounts),
    removed: summaries.map((summary) => summary.removed).reduce(addCounts),
    ...(summaries.some((summary) => summary.recoverability === "unavailable") ? { recoverability: "unavailable" as const } : {}),
  };
}

/** ``reply`` drawn with the replies it continues in front of it. */
export function joinedReply(
  cut: ChatMsg[], reply: ChatMsg, prose: (content: string) => string,
): ChatMsg {
  if (!cut.length) return reply;
  const parts = [...cut, reply];
  const blocks: AssistantBlock[] = [];
  parts.forEach((part, i) => {
    if (i > 0) blocks.push({ type: "restart" });
    blocks.push(...partBlocks(part, prose));
  });
  const withFiles = parts.filter((part) => part.turnFiles);
  const all = <T,>(pick: (m: ChatMsg) => T[] | undefined) => parts.flatMap((m) => pick(m) ?? []);
  return {
    ...reply,
    content: parts.map((part) => part.content).filter(Boolean).join("\n\n"),
    // An error's raw text stays this part's, not the whole reply's.
    errorDetail: reply.errorDetail || (reply.status === "error" ? reply.content : undefined),
    turnFiles: joinedTurnFiles(parts),
    // The list reads as reverted only when every part behind it was.
    reverted: withFiles.length ? withFiles.every((part) => part.reverted) : reply.reverted,
    blocks,
    tools: all((m) => m.tools),
    attachCards: all((m) => m.attachCards),
    callRoots: all((m) => m.callRoots),
    runtimeChildren: all((m) => m.runtimeChildren),
  };
}
