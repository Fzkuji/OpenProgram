/**
 * Rewind decisions that need no network and no React: who may rewind, what a
 * dry run leads to, how a picker row and a marker read, how refilled text
 * merges with a draft, and the wording for each cause.
 */

export type Text = (en: string, zh: string) => string;

export type RewindMode = "code_and_conversation" | "conversation" | "code";

export type RewindRow = {
  path: string;
  action: "restore" | "delete" | "skip";
  reason?: string | null;
  additions?: number;
  deletions?: number;
};

export type RewindPlan = {
  status?: string;
  mode?: RewindMode;
  files?: RewindRow[];
  summary?: { files?: number; additions?: number; deletions?: number; skipped?: number };
  has_changes?: boolean;
  skipped?: RewindRow[];
  code_available?: boolean;
  code_unavailable_reason?: string | null;
  plan_hashes?: Partial<Record<RewindMode, string>>;
  idempotency_key?: string | null;
  user_text?: string;
  error?: string | null;
  error_code?: string | null;
  /** The dry run itself failed, so nothing is known about the files. */
  transient?: boolean;
};

export type RewindResult = {
  status?: string;
  user_text?: string;
  skipped?: RewindRow[];
  marks?: RewindMark[];
  errors?: string[];
  error?: string;
  /** `branch_running`: another branch runs or waits, named in `blocking`. */
  code?: string;
  kind?: string;
  blocking?: import("./rewind-store").RewindBlocking;
};

/** Why a rewind that restores files waits for another branch. */
export function blockingText(
  blocking: { branch_name?: string; reason_code?: string; selected?: boolean },
  text: Text,
): string {
  const waiting = blocking.reason_code === "wait_open" || blocking.reason_code === "system_access_required";
  if (blocking.selected) {
    return waiting
      ? text("A turn on this branch is waiting for an approval. Stop it, then rewind.", "这个分支上有一轮在等待批准。先停止它，再回退。")
      : text("A turn on this branch is paused. Stop it, then rewind.", "这个分支上有一轮已暂停。先停止它，再回退。");
  }
  const name = blocking.branch_name;
  if (waiting) {
    return name
      ? text(`Branch “${name}” is waiting for an approval. Rewinding files now could undo its work; stop it, then rewind.`, `分支“${name}”正在等待批准。现在回退文件可能会撤掉它的工作；先停止它，再回退。`)
      : text("Another branch is waiting for an approval. Rewinding files now could undo its work; stop it, then rewind.", "另一个分支正在等待批准。现在回退文件可能会撤掉它的工作；先停止它，再回退。");
  }
  return name
    ? text(`Branch “${name}” is running. Rewinding files now could undo its work; stop it, then rewind.`, `分支“${name}”正在运行。现在回退文件可能会撤掉它的工作；先停止它，再回退。`)
    : text("Another branch is running. Rewinding files now could undo its work; stop it, then rewind.", "另一个分支正在运行。现在回退文件可能会撤掉它的工作；先停止它，再回退。");
}

export type RewindPoint = {
  msg_id: string;
  user_text?: string;
  summary?: string;
  code_available?: boolean;
};

export type RewindMark = {
  edge_id: string;
  variant: "pending" | "back" | "forward";
  anchor_head_id?: string | null;
  at?: number | null;
  available?: boolean;
  reason?: string | null;
};

export type RewindPreview =
  | { kind: "files"; files: number; additions: number; deletions: number; skipped: RewindRow[] }
  | { kind: "no_code"; reason: string | null }
  | { kind: "none" };

export type DisabledCause = "responding" | "offline" | "uploading";

export type ForkDisabledCause = "offline" | "unfinished";

/** How long a dry run may take before its dialog appears. */
export const DIALOG_DELAY_MS = 400;
/** How long a restore may take before the dialog says it is slow. */
export const SLOW_RESTORE_MS = 35_000;

export function disabledCause(state: {
  running: boolean; offline: boolean; uploading: boolean;
}): DisabledCause | null {
  if (state.running) return "responding";
  if (state.offline) return "offline";
  if (state.uploading) return "uploading";
  return null;
}

export function disabledReason(cause: DisabledCause, text: Text): string {
  switch (cause) {
    case "responding":
      return text("Rewind is unavailable while the agent is working", "智能体运行时无法回退");
    case "offline":
      return text("Rewind is unavailable while disconnected from the server", "与服务器断开时无法回退");
    case "uploading":
      return text("Rewind is unavailable while files are uploading", "文件上传时无法回退");
  }
}

export function forkDisabledCause(state: {
  offline: boolean; running: boolean; lastPrompt: boolean;
}): ForkDisabledCause | null {
  if (state.offline) return "offline";
  return state.running && state.lastPrompt ? "unfinished" : null;
}

export function forkDisabledReason(cause: ForkDisabledCause, text: Text): string {
  return cause === "offline"
    ? text("Fork is unavailable while disconnected from the server", "与服务器断开时无法分叉")
    : text("Fork is available once this message's reply has finished", "这条消息的回复结束后才能分叉");
}

export function sessionUnavailableText(text: Text): string {
  return text("Rewind isn't available for this session.", "这段对话无法回退。");
}

/** A sent user message the server knows. */
export function isRewindable(msg: {
  role?: string; id?: string; status?: string; rowKind?: string;
}): boolean {
  return msg.role === "user" && Boolean(msg.id)
    && msg.status !== "pending" && msg.status !== "error"
    && msg.rowKind !== "silent";
}

/** What a finished dry run leads to. */
export type PlanOutcome =
  | { kind: "refuse"; message: string }
  | { kind: "instant" }
  | { kind: "dialog"; preview: RewindPreview };

export function planOutcome(plan: RewindPlan, elapsedMs: number, text: Text): PlanOutcome {
  if (plan.error_code === "job_running") {
    return { kind: "refuse", message: text(
      "A background job from this conversation is still running. Rewind once it finishes.",
      "这段对话的后台任务还在运行，等它结束后再回退。",
    ) };
  }
  if (plan.error_code === "archived") {
    return { kind: "refuse", message: sessionUnavailableText(text) };
  }
  if (plan.status === "unavailable" || plan.error_code === "file_conflict") {
    return { kind: "dialog", preview: { kind: "no_code", reason: plan.code_unavailable_reason ?? null } };
  }
  if (plan.transient) return { kind: "dialog", preview: { kind: "none" } };
  if (plan.status !== "ready") {
    return { kind: "refuse", message: text("Can't rewind to this message.", "无法回退到这条消息。") };
  }
  const skipped = plan.skipped ?? [];
  if (plan.has_changes || skipped.length) {
    return { kind: "dialog", preview: {
      kind: "files",
      files: plan.summary?.files ?? 0,
      additions: plan.summary?.additions ?? 0,
      deletions: plan.summary?.deletions ?? 0,
      skipped,
    } };
  }
  return elapsedMs < DIALOG_DELAY_MS ? { kind: "instant" } : { kind: "dialog", preview: { kind: "none" } };
}

export function statsLine(preview: Extract<RewindPreview, { kind: "files" }>, text: Text): string {
  if (!preview.files) return "";
  const files = text(`${preview.files} ${preview.files === 1 ? "file" : "files"}`, `${preview.files} 个文件`);
  return preview.additions || preview.deletions
    ? `${files} · +${preview.additions} −${preview.deletions}`
    : files;
}

function basename(path: string): string {
  const cut = Math.max(path.lastIndexOf("/"), path.lastIndexOf("\\"));
  return cut >= 0 ? path.slice(cut + 1) : path;
}

function skipCause(reason: string | null | undefined, text: Text): string {
  switch (reason) {
    case "other_session": return text("another conversation changed it later", "之后被另一段对话改过");
    case "unsafe_path": return text("it is not safe to write there", "该位置无法安全写入");
    case "parent_missing": return text("its folder no longer exists", "所在文件夹已不存在");
    case "no_backup": return text("no saved copy exists", "没有保存的副本");
    case "changed_while_reading": return text("it changed while being checked", "检查时文件发生了变化");
    case "changed_since_edit": return text("it changed after this conversation edited it", "这段对话编辑之后它又被改过");
    default: return text("it could not be restored", "无法恢复");
  }
}

/** "Not restored: a.py, b.md: another conversation changed it later". */
export function skippedLine(skipped: RewindRow[], text: Text): string {
  if (!skipped.length) return "";
  const shown = skipped.slice(0, 3).map(row => basename(row.path));
  const more = skipped.length - shown.length;
  const names = shown.join(", ") + (more > 0 ? ` +${more}` : "");
  const reasons = new Set(skipped.map(row => row.reason));
  const cause = reasons.size === 1 ? `: ${skipCause(skipped[0].reason, text)}` : "";
  return text(`Not restored: ${names}${cause}`, `未恢复：${names}${cause}`);
}

export function noCodeLine(reason: string | null, text: Text): string {
  switch (reason) {
    case "conflict":
      return text("Some files changed later or cannot be safely restored. No files will be changed. You can rewind the conversation only.", "部分文件后来被修改或无法安全恢复。所有文件都将保持原样，你可以选择仅回退对话。");
    case "evicted":
      return text("Code can't be restored: the saved copies for this point were cleaned up.", "无法恢复代码：这个位置保存的副本已被清理。");
    case "journal_gap":
      return text("Code can't be restored: some changes after this point were not recorded.", "无法恢复代码：这个位置之后有修改没有记录。");
    default:
      return text("Code can't be restored from this point. You can still rewind the conversation.", "无法从这个位置恢复代码，但仍可回退对话。");
  }
}

/** Whether a failed apply may still have changed something: the reply was
 *  lost, the server broke, or its rollback could not finish. */
export function applyOutcomeUnknown(status: number, resultStatus?: string | null): boolean {
  return status === 0 || status >= 500 || resultStatus === "recovery_required";
}

/** Why an apply failed, in the dialog's words. */
export function applyErrorText(
  status: number,
  error: string | undefined,
  text: Text,
  resultStatus?: string | null,
): string {
  if (applyOutcomeUnknown(status, resultStatus)) {
    return text(
      "The rewind may not have finished. Check your files and this conversation before going on.",
      "回退可能没有完成，继续之前请检查文件和这段对话。",
    );
  }
  if (error && /changed since/i.test(error)) {
    return text("Files changed since the check. Review the changes and rewind again.", "检查之后文件有变化，请确认后重新回退。");
  }
  if (status === 409 && error && /run|active|busy/i.test(error)) {
    return disabledReason("responding", text);
  }
  return text("Couldn't rewind. Your files and conversation are unchanged. Try again.", "回退失败，文件和对话都没有改动，请重试。");
}

export function refusalText(
  action: "undo" | "redo",
  code: string | undefined,
  text: Text,
): string | null {
  if (code === "retired") return null;
  if (code === "gone") {
    return action === "undo"
      ? text("Those messages no longer exist, so this rewind can't be undone.", "那些消息已不存在，无法撤销这次回退。")
      : text("Those messages no longer exist, so this rewind can't be redone.", "那些消息已不存在，无法重做这次回退。");
  }
  if (code === "unsent") {
    return action === "undo"
      ? text("Send a message first, then undo this rewind.", "先发送一条消息，再撤销这次回退。")
      : text("Send a message first, then redo this rewind.", "先发送一条消息，再重做这次回退。");
  }
  if (code === "archived") return sessionUnavailableText(text);
  if (code === "stale") {
    return text("The conversation has moved on. Reopen it and try again.", "对话已经变化，请重新打开后再试。");
  }
  if (code === "unreadable") {
    return text("Those messages couldn't be read.", "无法读取那些消息。");
  }
  return action === "undo"
    ? text("Couldn't undo the rewind.", "未能撤销回退。")
    : text("Couldn't redo the rewind.", "未能重做回退。");
}

export function markLabel(mark: RewindMark, text: Text): string {
  return mark.variant === "forward" ? text("Rewind undone", "已撤销回退") : text("Rewound here", "已回退到这里");
}

export function markAction(mark: RewindMark, text: Text): string {
  return mark.variant === "forward" ? text("Redo rewind", "重做回退") : text("Undo rewind", "撤销回退");
}

export function markTip(mark: RewindMark, text: Text): string {
  if (mark.available === false) {
    return mark.variant === "forward"
      ? text("Those messages no longer exist, so this can't be redone.", "那些消息已不存在，无法重做。")
      : text("Those messages no longer exist, so this can't be undone.", "那些消息已不存在，无法撤销。");
  }
  if (mark.variant === "forward") {
    return text(
      "Returns to the rewound conversation. Messages from here on are set aside, and your files stay as they are.",
      "回到回退后的对话。这里之后的消息会被搁置，文件保持现状。",
    );
  }
  if (mark.variant === "back") {
    return text(
      "Brings back the messages this rewind set aside. Messages from here on are set aside for Redo, and your files stay as they are.",
      "找回这次回退搁置的消息。这里之后的消息留给重做，文件保持现状。",
    );
  }
  return text(
    "Brings back the messages this rewind set aside. Your files stay as they are.",
    "找回这次回退搁置的消息，文件保持现状。",
  );
}

/** Anchored marks appear only with their row. Pending and legacy unanchored
 *  marks can appear at the tail; offscreen anchors must not move there. */
export function placeMarks(
  marks: RewindMark[],
  ids: readonly string[],
  hidePending: boolean,
): { after: Map<string, RewindMark[]>; tail: RewindMark[] } {
  const after = new Map<string, RewindMark[]>();
  const shown = new Set(ids);
  const pending = marks.filter(mark => mark.variant === "pending");
  const stray: RewindMark[] = [];
  for (const mark of marks) {
    if (mark.variant === "pending") continue;
    if (mark.anchor_head_id && shown.has(mark.anchor_head_id)) {
      after.set(mark.anchor_head_id, [...(after.get(mark.anchor_head_id) ?? []), mark]);
    } else if (!mark.anchor_head_id) {
      stray.push(mark);
    }
  }
  const byTime = (a: RewindMark, b: RewindMark) => (a.at ?? 0) - (b.at ?? 0);
  for (const group of after.values()) group.sort(byTime);
  const tail = pending.length ? (hidePending ? [] : pending) : stray.sort(byTime);
  return { after, tail };
}

/** Whether another mark shares this one's place and direction. */
export function needsTime(mark: RewindMark, group: RewindMark[]): boolean {
  return group.some(other => other.edge_id !== mark.edge_id && other.variant === mark.variant);
}

/** One line for the picker: attachment names, then the collapsed prose. */
export function pickerLabel(prose: string, names: string[]): string {
  const collapsed = prose.replace(/\s+/g, " ").trim();
  const line = [...names, ...(collapsed ? [collapsed] : [])].join(" · ");
  return line.length > 80 ? `${line.slice(0, 80)}…` : line;
}

/** The dialog's prompt card: attachment names, then the prose with its
 *  line breaks. */
export function promptPreview(prose: string, names: string[] = []): string {
  const line = [...names, prose.trim()].filter(Boolean).join(" · ");
  return line.length > 600 ? `${line.slice(0, 600)}…` : line;
}

/** The refill above the draft; an earlier refill still there is replaced. */
export function mergeRefill(prefill: string, draft: string, previous: string): string {
  const rest = removeRefill(draft, previous);
  if (!prefill) return rest;
  return rest ? `${prefill}\n\n${rest}` : prefill;
}

/** The draft without an earlier refill: gone only while it is still whole,
 *  as all of the draft or ahead of the blank line mergeRefill wrote. */
export function removeRefill(draft: string, refill: string): string {
  if (!refill) return draft;
  if (draft === refill) return "";
  const separated = `${refill}\n\n`;
  return draft.startsWith(separated) ? draft.slice(separated.length) : draft;
}

type Row = { role?: string; rowKind?: string };

/** The last reply of the turn this user message opened, or the message. */
export function forkTipId(
  order: readonly string[] | undefined,
  byId: Record<string, Row | undefined>,
  msgId: string,
): string {
  const start = order?.indexOf(msgId) ?? -1;
  if (!order || start < 0) return msgId;
  let tip = msgId;
  for (const id of order.slice(start + 1)) {
    const row = byId[id];
    if (row?.role === "user" && row.rowKind !== "silent") break;
    if (row?.role === "assistant") tip = id;
  }
  return tip;
}

/** Whether this user message is still the conversation's last prompt. */
export function isLastPrompt(
  order: readonly string[] | undefined,
  byId: Record<string, Row | undefined>,
  msgId: string,
): boolean {
  const start = order?.indexOf(msgId) ?? -1;
  if (!order || start < 0) return false;
  return !order.slice(start + 1).some(id => byId[id]?.role === "user" && byId[id]?.rowKind !== "silent");
}

/** Failure reasons where going back to an earlier message can help. */
export function rewindHelps(reason: string | null | undefined): boolean {
  return reason === "context" || reason === "policy" || reason === "invalid";
}

/** Failures caused by the prompt itself, which can be rewound and resent. */
export function retryHelps(reason: string | null | undefined): boolean {
  return reason === "policy" || reason === "invalid";
}
