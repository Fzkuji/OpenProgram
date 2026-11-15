/**
 * Messages agents pass to each other, read back for the chat to draw as
 * one-line rows instead of bubbles the user never typed:
 *
 *  - a background-agent notice (``source: "job_followup"``): its
 *    ``notice`` items name each agent that finished, and each item's part
 *    of the notice text (agent/job/notices.py ``notice_text``) holds the
 *    reply to show when the row opens, inside the result block
 *    ``<agent …>`` … ``</agent>`` (runtime/jobs/agent_result.py ``render``)
 *    that notices written before it lack;
 *  - a message delivered to a branch: it starts with the receipt line
 *    ``[message|task from SID:HEAD (sender)]`` (send_message/delivery.py);
 *  - a stored hand-off between conversations: ``📨 Message from “Title”
 *    (id):`` and the answer that came back, ``↩️ Reply from “Title” (id) to
 *    the message you sent it:``.
 */
import type { NoticeItem } from "../../session-store/types";

const KINDS = new Set(["report", "reply", "idle", "stalled"]);

export function readNotice(raw: unknown): NoticeItem[] | undefined {
  if (!Array.isArray(raw)) return undefined;
  const items = raw.flatMap((r): NoticeItem[] => {
    if (!r || typeof r !== "object") return [];
    const o = r as Record<string, unknown>;
    if (typeof o.kind !== "string" || !KINDS.has(o.kind) || typeof o.line !== "string") return [];
    return [{
      kind: o.kind as NoticeItem["kind"],
      name: typeof o.name === "string" ? o.name : "",
      failed: o.failed === true,
      address: typeof o.address === "string" ? o.address : "",
      line: o.line,
    }];
  });
  return items.length ? items : undefined;
}

const FOOTER = "Carry on with the user's request from here";
const CONTINUE = "To continue with it, use send_message(";

const BLOCK_OPEN = /^<agent\s[^>]*>$/;
const BLOCK_CLOSE = "</agent>";

/** The text inside a result block, without its tags and the ``files:`` and
 *  ``usage:`` lines the block ends with; null when ``lines`` has no block. */
function blockText(lines: string[]): string[] | null {
  const open = lines.findIndex((l) => BLOCK_OPEN.test(l));
  const close = lines.lastIndexOf(BLOCK_CLOSE);
  if (open < 0 || close <= open) return null;
  const inner = lines.slice(open + 1, close);
  if (inner.length && inner[inner.length - 1].startsWith("usage: ")) inner.pop();
  if (inner.length && inner[inner.length - 1].startsWith("files: ")) inner.pop();
  return inner;
}

/** What the row for ``items[index]`` shows when it opens: the branch's
 *  reply for a reply or idle item, the task it was given for a report or a
 *  stalled agent (the report itself is in the agent's context, not the
 *  notice). */
export function noticeDetail(content: string, items: NoticeItem[], index: number): string {
  const lines = content.split("\n");
  const starts = new Set(items.map((it) => it.line));
  const from = lines.indexOf(items[index].line);
  if (from < 0) return "";
  let to = from + 1;
  while (to < lines.length && !starts.has(lines[to]) && !lines[to].startsWith(FOOTER)) to++;
  const block = lines.slice(from + 1, to);
  while (block.length && !block[block.length - 1].trim()) block.pop();
  if (block.length && block[block.length - 1].startsWith(CONTINUE)) block.pop();
  const item = items[index];
  if (item.kind === "report" || item.kind === "stalled") {
    const task = block.find((l) => l.startsWith("Task: "));
    return task ? task.slice("Task: ".length) : "";
  }
  const marker = item.kind === "idle" ? "Its latest reply:" : `Reply from branch ${item.address}:`;
  const at = block.indexOf(marker);
  const after = at >= 0 ? block.slice(at + 1) : block;
  return (blockText(after) ?? after).join("\n").trim();
}

export interface Receipt {
  kind: "task" | "message" | "reply";
  /** Who sent it (``main conversation``, an agent, another conversation's
   *  title); empty for a message written before senders were named. */
  from: string;
  address: string;
  body: string;
}

const RECEIPT = /^\[(task|message) from (\S+?)(?: \((.*?)\))?\][^\n]*\n+/;

const HANDOFF = /^(📨 Message from|↩️ Reply from) “([^”\n]*)” \(([^()\s]+)\)[^\n]*:\n+/;
/** The note a hand-off ended with, addressed to the agent that received it. */
const HANDOFF_NOTE = /\n+\(Another of your conversations handed this to you\.[^\n]*\)\s*$/;

export function readReceipt(content: string): Receipt | null {
  const m = RECEIPT.exec(content);
  if (!m) {
    const handed = HANDOFF.exec(content);
    if (!handed) return null;
    return {
      kind: handed[1].startsWith("📨") ? "message" : "reply",
      address: handed[3],
      from: handed[2],
      body: content.slice(handed[0].length).replace(HANDOFF_NOTE, ""),
    };
  }
  return {
    kind: m[1] as Receipt["kind"],
    address: m[2],
    from: m[3] ?? "",
    body: content.slice(m[0].length),
  };
}

/** The sender name send_message gives the main conversation. */
export const MAIN_CONVERSATION = "main conversation";
