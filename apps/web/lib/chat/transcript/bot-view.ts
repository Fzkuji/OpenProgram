/**
 * How a conversation with an always-on Agent reads: a chat with a bot. The
 * stored messages do not change; this only decides what is drawn, so an old
 * conversation shows the same way and comes back whole when always-on is off.
 *
 * Only what was said is kept: the user's messages and the Agent's text, short
 * paragraphs as their own bubbles, with the time shown again after a pause.
 */
import type { ChatMsg, Citation } from "@/lib/session-store";
import { closeOpenFence } from "@/lib/chat/markdown/open-fence";
import { JOINED_SOURCE, formulaOpen } from "@/lib/chat/markdown/streaming-blocks";
import { isLive } from "./row-status.ts";

/** A pause this long brings the time back. */
export const BREAK_SECONDS = 30 * 60;
/** A reply never draws more bubbles than this; the last one takes the rest. */
export const MAX_BUBBLES = 4;
/** A bubble wider than this (CJK counts double) is not followed by another. */
export const MAX_BUBBLE_WEIGHT = 320;

const INTERNAL_SOURCES = new Set([
  "job_followup", "merge_turn", "agent_spawn", "hook_continue", "scheduler", "goal", "llm",
]);

/** What a restart stores as the text of a reply cut before it wrote any
 *  (runtime/execution/dag.py). */
const RESTART_MARKER = "[interrupted] worker restarted mid-turn";

/** ``msg`` with only what the Agent wrote left in its content. A failed turn
 *  with no detail saved its error there, and a restart its marker. */
export function botWritten(msg: ChatMsg): ChatMsg {
  const notWritten = msg.status === "error" ? !msg.errorDetail : msg.content === RESTART_MARKER;
  return notWritten && msg.content ? { ...msg, content: "" } : msg;
}

/** The text the Agent wrote in a reply, without its steps. */
export function botReplyText(msg: ChatMsg): string {
  const written = (msg.blocks ?? []).flatMap((block) => block.type === "text" && block.text ? [block.text] : []);
  // A block cut inside code must not swallow the text joined after it.
  for (let index = 0; index < written.length - 1; index += 1) written[index] = closeOpenFence(written[index]);
  if (written.length) return written.join("\n\n").trim();
  return (botWritten(msg).content || "").trim();
}

/** The citations of a reply's text, for the one row of sources under it. */
export function botCitations(msg: ChatMsg): Citation[] {
  return (msg.blocks ?? []).flatMap((block) => block.type === "text" ? block.citations ?? [] : []);
}

/** How a reply ended short, when that needs saying. A stopped reply says
 *  nothing, and one a restart cut off is carried on by the reply after it. */
export function botFailure(msg: ChatMsg): "error" | "interrupted" | null {
  if (msg.status === "error") return "error";
  return msg.status === "interrupted" && !msg.continuedBy ? "interrupted" : null;
}

/** Whether a live reply that has text is off doing something else: its text
 *  shows its own progress, so the dots return only after the last words. */
export function botWorksAfterText(msg: ChatMsg): boolean {
  const last = msg.blocks?.[msg.blocks.length - 1];
  return last !== undefined && last.type !== "text";
}

/** The files a reply sent. Their markers ride the reply's stored content,
 *  which the text blocks do not carry. ``parse`` reads the markers. */
export function botReplyFiles<File extends { path: string }>(
  msg: ChatMsg | readonly ChatMsg[],
  parse: (content: string) => { attachments: File[] },
): File[] {
  const found = [msg].flat().flatMap((part) => [...parse(botReplyText(part)).attachments, ...parse(part.content || "").attachments]);
  return found.filter((file, index) => found.findIndex((other) => other.path === file.path) === index);
}

/** Whether a user row is something a person said. The row's kind is the
 *  server's word; a restart prompt and internal notes are not speech. */
function isSaid(msg: ChatMsg): boolean {
  return (msg.rowKind === "user" || msg.rowKind === "received_message")
    && msg.trigger !== "restart" && !msg.notice && !INTERNAL_SOURCES.has(msg.source ?? "");
}

/** Whether an assistant row is a reply, as opposed to a card or runtime record. */
function isReply(msg: ChatMsg): boolean {
  return msg.rowKind === "assistant" && !msg.goalVerification;
}

/** What a scheduled run answers when it has nothing to tell, or could not
 *  do its check (runtime/scheduler/task_branch.py). Both marks are for the
 *  scheduler, which reports a task that keeps failing in a message of its
 *  own: a run kept on the main line says nothing there either. */
const RUN_MARK = /^\s*(?:[\[【]?\s*(?:nothing new|无新内容|没有新内容)\s*[\]】]?\s*[.。!！]?\s*$|\[could not check\])/i;

/** Whether a message draws anything but its steps. */
export function botShows(msg: ChatMsg): boolean {
  if (msg.rowKind === "user" || msg.rowKind === "received_message") return isSaid(msg);
  if (!isReply(msg)) return false;
  if (botFailure(msg) !== null) return true;
  const text = botReplyText(msg);
  return text !== "" && !(RUN_MARK.test(text) && !isLive(msg.status));
}

/** Whether a row takes part in the time lines. The lines are worked out when
 *  a row is added, before a reply has words, so a reply still writing counts. */
function holdsTime(msg: ChatMsg): boolean {
  return botShows(msg) || (isReply(msg) && !msg.function && isLive(msg.status));
}

function seconds(timestamp: number): number {
  return timestamp > 1e12 ? timestamp / 1000 : timestamp;
}

function sameDay(first: number, second: number): boolean {
  return new Date(first * 1000).toDateString() === new Date(second * 1000).toDateString();
}

/** The rows that open with the time: the first, any after a pause, the
 *  first of a new day, and a report, which arrives on its own. A reply cut off by a restart has no row of its own;
 *  when it wrote anything, the reply carrying it on stands in for it. */
export function timeBreaks(messages: ChatMsg[]): Map<string, number> {
  const breaks = new Map<string, number>();
  const ids = new Set(messages.map((msg) => msg.id));
  const carried = new Set<string>();
  let previous: number | null = null;
  for (const msg of messages) {
    if (msg.continuedBy && ids.has(msg.continuedBy)) {
      if (holdsTime(msg) || carried.has(msg.id)) carried.add(msg.continuedBy);
      continue;
    }
    if ((!holdsTime(msg) && !carried.has(msg.id)) || !msg.timestamp) continue;
    const at = seconds(msg.timestamp);
    if (msg.reportId || previous === null || at - previous >= BREAK_SECONDS || !sameDay(previous, at)) breaks.set(msg.id, at);
    previous = at;
  }
  return breaks;
}

const FENCE = /^ {0,3}(`{3,}|~{3,})([^\n]*)/;
const THEMATIC_BREAK = /^ {0,3}([-*_])(?:[ \t]*\1){2,}[ \t]*$/;
const DELIMITER_ROW = /^ *\|? *:?-+:? *(?:\| *:?-+:? *)*\|? *$/;
const NUMBERED = "[一二三四五六七八九十]+[、.]";
const CONTINUES = new RegExp(`^(?: {4}|\\t| {0,3}(?:\`\`\`|~~~|\\||[-*+][ \\t]|\\d+[.)][ \\t]|>|[•·・]|${NUMBERED}))`);
const RICH = new RegExp(`^(?: {4}|\\t| {0,3}(?:\`{3,}[^\`\\n]*$|~{3,}|>|#{1,6}\\s)|\\s*(?:[-*+]|\\d{1,9}[.)])\\s|\\s*[•·・]|\\s*${NUMBERED}|=+\\s*$|\\$\\$|\\\\\\[)`, "m");
const LONE_BOLD = /^\*\*[^\n]+\*\*[:：]?$/;
/** Text that could still turn into a list, rule, code or table as it streams. */
const UNDECIDED = /^ {0,3}(?:[-*+>|~`_$•·・]{1,2}|\d+[.)]?|[一二三四五六七八九十]+[、.]?|[-*_ \t]+|\|[^\n]*)$/;
const WIDE = /[\u1100-\u115f\u2e80-\ua4cf\uac00-\ud7a3\uf900-\ufaff\ufe30-\ufe6f\uff00-\uff60\uffe0-\uffe6\u{20000}-\u{3ffff}]/u;

function isTableDelimiter(line: string | undefined): boolean {
  return line !== undefined && line.includes("|") && DELIMITER_ROW.test(line);
}

/** A piece that belongs with the text before it. A table with no leading pipe
 *  is only known from its second line, after it is already on screen as its
 *  own bubble, so while streaming it stays there and joins when the reply ends. */
function continues(piece: string, streaming: boolean): boolean {
  const [first, second] = piece.split("\n");
  return CONTINUES.test(piece) || THEMATIC_BREAK.test(first) || (!streaming && isTableDelimiter(second));
}

/** Whether a bubble is plain paragraphs, which another bubble may follow. */
function isPlain(bubble: string): boolean {
  if (RICH.test(bubble) || LONE_BOLD.test(bubble.trim())) return false;
  return !bubble.split("\n").some((line) => THEMATIC_BREAK.test(line) || isTableDelimiter(line));
}

function weight(text: string): number {
  let total = 0;
  for (const character of text) total += WIDE.test(character) ? 2 : 1;
  return total;
}

/** The paragraphs of a reply; code and display formulas stay in one piece. */
function splitPieces(text: string): string[] {
  const pieces: string[] = [];
  let current: string[] = [];
  let prose: string[] = [];
  let fence: { char: string; length: number } | null = null;
  const close = () => {
    if (current.length) pieces.push(current.join("\n"));
    current = [];
    prose = [];
  };
  for (const line of text.split("\n")) {
    const [, marker, rest = ""] = FENCE.exec(line) ?? [];
    const wasFenced = fence !== null;
    if (fence) {
      if (marker && marker[0] === fence.char && marker.length >= fence.length && !rest.trim()) fence = null;
    } else if (marker && !(marker[0] === "`" && rest.includes("`"))) {
      fence = { char: marker[0], length: marker.length };
    }
    if (!fence && !wasFenced && line.trim() === "" && !formulaOpen(prose.join("\n"))) {
      close();
      continue;
    }
    current.push(line);
    if (!fence && !wasFenced) prose.push(line);
  }
  close();
  return pieces;
}

/**
 * One reply as bubbles. While a reply streams, a bubble only ever closes as a
 * prefix of what it shows at the end of the stream and the count never drops,
 * so nothing jumps under the reader; a piece that cannot be classified yet
 * waits off screen, unless it is the first, which has nothing to join. Only
 * when the reply ends do a pipe-less table and a source whose rendering joins
 * distant parts merge, since neither can be known earlier.
 */
export function splitBubbles(text: string, options: { streaming?: boolean } = {}): string[] {
  const streaming = options.streaming === true;
  if (!streaming && JOINED_SOURCE.test(text)) return text.trim() ? [text.trim()] : [];
  const pieces = splitPieces(text);
  if (streaming && pieces.length > 1 && UNDECIDED.test(pieces[pieces.length - 1])) pieces.pop();
  const bubbles: string[] = [];
  for (const piece of pieces) {
    const last = bubbles[bubbles.length - 1];
    const startsNew = last === undefined || (
      !continues(piece, streaming) && bubbles.length < MAX_BUBBLES && isPlain(last) && weight(last) <= MAX_BUBBLE_WEIGHT
    );
    if (startsNew) bubbles.push(piece);
    else bubbles[bubbles.length - 1] += `\n\n${piece}`;
  }
  return bubbles;
}

type Text = (english: string, chinese: string) => string;

/** Why a report arrived unasked, when that is known. An older report saved
 *  no title and shows none, rather than a possibly wrong reason. */
export function reportPhrase(msg: ChatMsg, text: Text): string | null {
  if (!msg.reportId || !msg.reportTitle) return null;
  if (msg.reportSource === "task") return text(`Scheduled: ${msg.reportTitle}`, `定时任务：${msg.reportTitle}`);
  if (msg.reportKind === "approval") return text("Asks for your decision", "请你决定");
  if (msg.reportSource !== "check_in") return null;
  if (msg.reportKind === "finding") return text("Noticed while checking in", "例行查看时发现");
  return msg.reportKind === "done" ? text("Finished in the background", "后台做完了") : null;
}

/** The line that opens a stretch: the time, and why a report came. */
export function botTimeLine(msg: ChatMsg | undefined, at: number, now: Date, locale: string, text: Text): string {
  const phrase = msg ? reportPhrase(msg, text) : null;
  return phrase ? `${breakLabel(at, now, locale)} · ${phrase}` : breakLabel(at, now, locale);
}

/** The time as a chat shows it: today's as a clock, recent ones by day. */
export function breakLabel(at: number, now: Date, locale: string): string {
  const when = new Date(at * 1000);
  const clock = when.toLocaleTimeString(locale, { hour: "2-digit", minute: "2-digit" });
  const midnight = (date: Date) => new Date(date.getFullYear(), date.getMonth(), date.getDate()).getTime();
  const days = Math.round((midnight(now) - midnight(when)) / 86400000);
  if (days === 0) return clock;
  if (days < 7) return `${when.toLocaleDateString(locale, { weekday: "short" })} ${clock}`;
  return `${when.toLocaleDateString(locale, { month: "short", day: "numeric" })} ${clock}`;
}
