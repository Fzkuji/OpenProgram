/**
 * What the rewind controls do: the dry run and its 400 ms rule, applying the
 * confirmed plan, refilling the composer, the picker, Undo and Redo, Fork,
 * and Rewind and retry. The server plans, applies and withdraws later messages
 * on the current branch; a rewind is never partly applied.
 */
import { useSessionStore } from "@/lib/session-store";
import { translateText } from "@/lib/i18n";
import { dismissToast, showToast } from "@/lib/format-utils/toast";
import { absRawFileUrl } from "@/lib/files/files-shared";
import { sendRuntimeCommand } from "@/lib/runtime-bridge/session/session-load";
import { sendExecutionCancel } from "@/lib/execution/cancel-execution";
import { copyConversation } from "@/lib/chat/copy-conversation";
import { parseAttachments } from "@/components/chat/messages/attachments/user-attachments";

import {
  DIALOG_DELAY_MS,
  SLOW_RESTORE_MS,
  applyErrorText,
  applyOutcomeUnknown,
  blockingText,
  disabledCause,
  disabledReason,
  forkTipId,
  mergeRefill,
  planOutcome,
  refusalText,
  removeRefill,
  sessionUnavailableText,
  skippedLine,
  type DisabledCause,
  type RewindMark,
  type RewindMode,
  type RewindPlan,
  type RewindPoint,
  type RewindResult,
} from "./rewind-logic";
import {
  lastRefill,
  patchFlow,
  requestRefillRetract,
  setRewindMarks,
  useRewindStore,
} from "./rewind-store";

type Reply<T> = { ok: boolean; status: number; data: T };

async function post<T>(url: string, body: unknown): Promise<Reply<T>> {
  const response = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  const data = await response.json().catch(() => ({})) as T;
  return { ok: response.ok, status: response.status, data };
}

/** Why this conversation cannot rewind right now, if anything. */
export function currentDisabledCause(sessionId: string): DisabledCause | null {
  const session = useSessionStore.getState();
  return disabledCause({
    running: Boolean(session.runningTasks?.[sessionId]),
    offline: session.wsStatus === "closed",
    uploading: Boolean(useRewindStore.getState().uploading[sessionId]),
  });
}

let flowToken = 0;
let dialogTimer: ReturnType<typeof setTimeout> | null = null;
/** A confirmed rewind that has not answered yet. */
let restoreInFlight = false;

function clearDialogTimer(): void {
  if (dialogTimer) clearTimeout(dialogTimer);
  dialogTimer = null;
}

/** The dry run. One that could not run at all reads as `transient`. */
async function fetchPlan(sessionId: string, msgId: string): Promise<RewindPlan> {
  try {
    const reply = await post<RewindPlan>("/api/chat/rewind_plan", { session_id: sessionId, msg_id: msgId });
    return reply.status >= 500 ? { status: "error", transient: true } : reply.data;
  } catch {
    return { status: "error", transient: true };
  }
}

function reloadConversation(sessionId: string): void {
  sendRuntimeCommand({ action: "load_session", session_id: sessionId });
}

function isArchived(sessionId: string): boolean {
  return Boolean(useSessionStore.getState().conversations?.[sessionId]?.archived);
}

/** The icon, the context menu and the picker all start here. */
export async function requestRewind(sessionId: string, msgId: string, text: string): Promise<void> {
  if (useRewindStore.getState().flow || currentDisabledCause(sessionId) || isArchived(sessionId)) return;
  if (restoreInFlight) {
    showToast(translateText("An earlier rewind is still running.", "上一次回退还在进行。"), { tone: "warn" });
    return;
  }
  const token = ++flowToken;
  useRewindStore.setState({ flow: {
    sessionId, msgId, text, plan: null, preview: "pending",
    mode: "code_and_conversation", visible: false, phase: "ready", error: null,
  } });
  dialogTimer = setTimeout(() => patchFlow({ visible: true }), DIALOG_DELAY_MS);
  const started = Date.now();
  const plan = await fetchPlan(sessionId, msgId);
  if (token !== flowToken) return;
  clearDialogTimer();
  const outcome = planOutcome(plan, Date.now() - started, translateText);
  if (outcome.kind === "refuse") {
    useRewindStore.setState({ flow: null });
    showToast(outcome.message, { tone: "error" });
    return;
  }
  if (outcome.kind === "instant") {
    await instantRewind(sessionId, msgId, text, plan);
    return;
  }
  patchFlow({
    plan,
    preview: outcome.preview,
    visible: true,
    mode: outcome.preview.kind === "files" ? "code_and_conversation" : "conversation",
  });
}

/** No dialog, but the flow stays until the rewind answers, so a second
 *  request waits its turn. A slow one says so. */
async function instantRewind(sessionId: string, msgId: string, text: string, plan: RewindPlan): Promise<void> {
  patchFlow({ phase: "running" });
  restoreInFlight = true;
  const toastKey = `rewinding-${sessionId}`;
  const slowToast = setTimeout(() => {
    showToast(translateText("Rewinding…", "正在回退…"), { duration: 60_000, key: toastKey, sessionId });
  }, DIALOG_DELAY_MS);
  const reply = await applyPlan(sessionId, msgId, "conversation", plan).catch(() => null);
  clearTimeout(slowToast);
  dismissToast(toastKey);
  restoreInFlight = false;
  useRewindStore.setState({ flow: null });
  if (!reply?.ok) {
    reportFailedApply(sessionId, reply);
    return;
  }
  finishRewind(sessionId, "conversation", reply.data, text);
}

/** The toast for a failed apply; a rewind that may have happened re-reads
 *  the conversation so it matches the files. */
function reportFailedApply(sessionId: string, reply: Reply<RewindResult> | null): string {
  const status = reply?.status ?? 0;
  const message = applyErrorText(status, reply?.data.error, translateText, reply?.data.status);
  if (applyOutcomeUnknown(status, reply?.data.status)) {
    reloadConversation(sessionId);
    void refreshRewindMarks(sessionId);
  }
  showToast(message, { tone: "error" });
  return message;
}

function applyPlan(sessionId: string, msgId: string, mode: RewindMode, plan: RewindPlan): Promise<Reply<RewindResult>> {
  return post<RewindResult>("/api/chat/rewind", {
    session_id: sessionId,
    msg_id: msgId,
    plan_hash: plan.plan_hashes?.[mode],
    idempotency_key: plan.idempotency_key,
    mode,
  });
}

export function setRewindMode(mode: RewindMode): void {
  patchFlow({ mode, error: null, blocking: null });
}

export function cancelRewind(): void {
  const flow = useRewindStore.getState().flow;
  if (!flow || flow.phase === "running") return;
  flowToken += 1;
  clearDialogTimer();
  useRewindStore.setState({ flow: null });
}

export async function confirmRewind(): Promise<void> {
  const flow = useRewindStore.getState().flow;
  if (!flow || !flow.plan || flow.phase !== "ready" || restoreInFlight) return;
  const { sessionId, msgId, mode, text } = flow;
  const token = flowToken;
  restoreInFlight = true;
  patchFlow({ phase: "running", error: null });
  const slowTimer = setTimeout(() => {
    if (token === flowToken) patchFlow({ phase: "slow" });
  }, SLOW_RESTORE_MS);
  let reply: Reply<RewindResult> | null = null;
  try {
    // A dialog opened after a failed check has no plan to apply yet.
    const plan = flow.plan.plan_hashes?.[mode] ? flow.plan : await fetchPlan(sessionId, msgId);
    reply = plan.plan_hashes?.[mode] ? await applyPlan(sessionId, msgId, mode, plan) : null;
  } catch {
    reply = null;
  } finally {
    clearTimeout(slowTimer);
    restoreInFlight = false;
  }
  const dialogOpen = token === flowToken;
  if (reply?.ok && reply.data.status === "committed") {
    if (dialogOpen) useRewindStore.setState({ flow: null });
    finishRewind(sessionId, mode, reply.data, text);
    return;
  }
  if (!dialogOpen) {
    reportFailedApply(sessionId, reply);
    return;
  }
  const status = reply?.status ?? 0;
  const blocking = reply?.data.code === "branch_running" ? reply.data.blocking ?? null : null;
  patchFlow({
    phase: "ready",
    error: blocking
      ? blockingText(blocking, translateText)
      : applyErrorText(status, reply?.data.error, translateText, reply?.data.status),
    blocking,
  });
  if (applyOutcomeUnknown(status, reply?.data.status)) {
    reloadConversation(sessionId);
    void refreshRewindMarks(sessionId);
  }
  // The refused plan is spent: the next press applies a fresh one.
  const fresh = await fetchPlan(sessionId, msgId);
  if (token !== flowToken) return;
  const outcome = planOutcome(fresh, DIALOG_DELAY_MS, translateText);
  if (outcome.kind === "refuse") {
    useRewindStore.setState({ flow: null });
    showToast(outcome.message, { tone: "error" });
    return;
  }
  if (outcome.kind === "dialog") {
    const chosen = useRewindStore.getState().flow?.mode;
    patchFlow({
      plan: fresh,
      preview: outcome.preview,
      mode: outcome.preview.kind === "files" ? chosen ?? "code_and_conversation" : "conversation",
    });
  }
}

/** How long "Stop it, then rewind" waits between tries for the stopped
 *  branch to let go of the conversation's files. */
const STOP_THEN_REWIND_WAITS_MS = [400, 800, 1500, 3000, 5000];

/** The refusal's "Stop it, then rewind": stop the branch in the way, then
 *  apply the same rewind once that branch has ended. */
export async function stopBlockingThenRewind(): Promise<void> {
  const flow = useRewindStore.getState().flow;
  const blocking = flow?.blocking;
  if (!flow || !blocking || flow.phase !== "ready") return;
  const sent = blocking.execution_id
    ? sendExecutionCancel(sendRuntimeCommand, blocking.execution_id, blocking.status_version ?? 0)
    : blocking.op_id
      ? sendRuntimeCommand({ action: "operation.cancel", session_id: flow.sessionId, op_id: blocking.op_id })
      : false;
  if (!sent) {
    patchFlow({ error: translateText("Could not send the stop request. Reconnect and try again.", "停止请求没有发出，请重新连接后再试。") });
    return;
  }
  const same = (other?: { execution_id?: string; op_id?: string } | null) =>
    Boolean(other) && other?.execution_id === blocking.execution_id && other?.op_id === blocking.op_id;
  patchFlow({ phase: "running", error: null, blocking: null });
  for (const wait of STOP_THEN_REWIND_WAITS_MS) {
    await new Promise((resolve) => setTimeout(resolve, wait));
    const current = useRewindStore.getState().flow;
    if (!current || current.sessionId !== flow.sessionId) return;
    patchFlow({ phase: "ready" });
    await confirmRewind();
    const after = useRewindStore.getState().flow;
    // Done, or refused for another reason the dialog now shows.
    if (!after || !same(after.blocking)) return;
    patchFlow({ phase: "running", error: null, blocking: null });
  }
  patchFlow({
    phase: "ready",
    blocking,
    error: translateText("That branch is still stopping. Try again in a moment.", "那个分支还在停止中，请稍后再试。"),
  });
}

function finishRewind(sessionId: string, mode: RewindMode, result: RewindResult, localText: string): void {
  if (result.marks) setRewindMarks(sessionId, result.marks);
  if (mode !== "code") {
    void refillComposer(sessionId, result.user_text || localText);
    reloadConversation(sessionId);
    useSessionStore.getState().focusComposer?.();
  }
  const skipped = result.skipped ?? [];
  if (skipped.length) {
    showToast(skippedLine(skipped, translateText), { tone: "warn" });
  } else if (result.errors?.length) {
    showToast(translateText(
      `Rewound, but some files failed to restore: ${result.errors[0]}`,
      `已回退，但部分文件恢复失败：${result.errors[0]}`,
    ), { tone: "warn" });
  } else if (mode === "code") {
    showToast(translateText("Code restored", "代码已恢复"));
  }
}

/** Each conversation's newest refill. A refill still reading attachments
 *  stops if another takes its place or Undo cancels it. */
const refillTokens = new Map<string, number>();
let refillCounter = 0;

function isImage(name: string): boolean {
  return /\.(png|jpe?g|webp|gif)$/i.test(name);
}

/** The message goes back above any draft; its attachments go back beside
 *  it. One with no readable copy keeps its marker in the text. */
async function refillComposer(sessionId: string, content: string): Promise<void> {
  const refillId = ++refillCounter;
  refillTokens.set(sessionId, refillId);
  const parsed = parseAttachments(content, true);
  const files: File[] = [];
  const markers = parsed.recordings.flatMap(recording => (recording.raw ? [recording.raw] : []));
  for (const item of parsed.attachments) {
    try {
      if (!item.path) throw new Error("no saved copy");
      const response = await fetch(absRawFileUrl(item.path, sessionId));
      if (!response.ok) throw new Error(response.statusText);
      const blob = await response.blob();
      files.push(new File([blob], item.filename, {
        type: blob.type || (isImage(item.filename) ? "image/png" : "application/octet-stream"),
      }));
    } catch {
      if (item.raw) markers.push(item.raw);
    }
  }
  if (refillTokens.get(sessionId) !== refillId) return;
  const store = useSessionStore.getState();
  const text = [...markers, parsed.text].filter(Boolean).join("\n\n");
  const draft = store.composerDrafts?.[sessionId] ?? "";
  const previous = lastRefill.get(sessionId);
  store.setComposerInputFor(sessionId, mergeRefill(text, draft, previous?.text ?? ""));
  if (previous) requestRefillRetract(sessionId, previous.refillId);
  lastRefill.set(sessionId, { text, refillId });
  if (files.length) {
    useRewindStore.setState(state => ({
      refillFiles: {
        ...state.refillFiles,
        [sessionId]: [...(state.refillFiles[sessionId] ?? []), { refillId, files }],
      },
    }));
  }
}

/** Undo takes the refill back out and keeps what the user typed. After a
 *  reload only the server's copy of the message says what the refill was. */
function retractRefill(sessionId: string, serverText: string | undefined): void {
  refillTokens.delete(sessionId);
  const refill = lastRefill.get(sessionId);
  lastRefill.delete(sessionId);
  let text = refill?.text ?? "";
  if (!refill && serverText) {
    const parsed = parseAttachments(serverText, true);
    text = [...parsed.recordings.flatMap(item => (item.raw ? [item.raw] : [])), parsed.text].filter(Boolean).join("\n\n");
  }
  const store = useSessionStore.getState();
  if (text) store.setComposerInputFor(sessionId, removeRefill(store.composerDrafts?.[sessionId] ?? "", text));
  if (refill) requestRefillRetract(sessionId, refill.refillId);
}

/** `fromError`: an error card's Rewind, which starts from an empty composer. */
export async function openRewindPicker(sessionId: string, options: { fromError?: boolean } = {}): Promise<void> {
  if (isArchived(sessionId)) {
    showToast(sessionUnavailableText(translateText), { tone: "warn" });
    return;
  }
  const cause = currentDisabledCause(sessionId);
  if (cause) {
    if (cause !== "responding") showToast(disabledReason(cause, translateText), { tone: "warn" });
    return;
  }
  if (options.fromError) useSessionStore.getState().setComposerInputFor(sessionId, "");
  useRewindStore.setState({ picker: { sessionId, points: null } });
  try {
    const reply = await post<{ points?: RewindPoint[]; error?: string }>(
      "/api/chat/rewind_points", { session_id: sessionId },
    );
    if (!reply.ok) throw new Error(reply.data.error || String(reply.status));
    if (useRewindStore.getState().picker?.sessionId !== sessionId) return;
    useRewindStore.setState({ picker: { sessionId, points: [...(reply.data.points ?? [])].reverse() } });
  } catch {
    useRewindStore.setState({ picker: null });
    showToast(translateText("Couldn't read this conversation's messages.", "未能读取这段对话的消息。"), { tone: "error" });
  }
}

export function closeRewindPicker(): void {
  useRewindStore.setState({ picker: null });
}

export function pickRewindPoint(sessionId: string, point: RewindPoint): void {
  closeRewindPicker();
  void requestRewind(sessionId, point.msg_id, point.user_text ?? "");
}

export async function refreshRewindMarks(sessionId: string): Promise<void> {
  try {
    const reply = await post<{ marks?: RewindMark[] }>("/api/chat/rewind_marks", { session_id: sessionId });
    if (reply.ok) setRewindMarks(sessionId, reply.data.marks ?? []);
  } catch {
    // Marks are read again with the next transcript change.
  }
}

const switching = new Set<string>();

export async function switchRewindMark(sessionId: string, mark: RewindMark): Promise<void> {
  if (switching.has(sessionId)) return;
  switching.add(sessionId);
  const action = mark.variant === "forward" ? "redo" : "undo";
  try {
    const reply = await post<{ marks?: RewindMark[]; code?: string; error?: string; user_text?: string }>(
      `/api/chat/rewind_${action}`, { session_id: sessionId, edge_id: mark.edge_id },
    );
    if (!reply.ok) {
      const message = reply.data.code
        ? refusalText(action, reply.data.code, translateText)
        : disabledReason("responding", translateText);
      if (message) showToast(message, { tone: "error" });
      void refreshRewindMarks(sessionId);
      return;
    }
    if (reply.data.marks) setRewindMarks(sessionId, reply.data.marks);
    if (mark.variant === "pending") retractRefill(sessionId, reply.data.user_text);
    reloadConversation(sessionId);
  } catch {
    const message = refusalText(action, undefined, translateText);
    if (message) showToast(message, { tone: "error" });
  } finally {
    switching.delete(sessionId);
  }
}

/** A new conversation holding this message and its reply; this one stays. */
export function forkFromMessage(sessionId: string, msgId: string): Promise<string | null> {
  const store = useSessionStore.getState();
  const tip = forkTipId(store.messageOrder?.[sessionId], store.rowsByView?.[sessionId] ?? {}, msgId);
  return copyConversation(sessionId, translateText, tip);
}

/** Takes the failed prompt out of the conversation and puts it back in the
 *  composer for another try. Files are left alone. */
export async function rewindAndRetry(sessionId: string, msgId: string, text: string): Promise<void> {
  const cause = currentDisabledCause(sessionId);
  if (cause) {
    showToast(disabledReason(cause, translateText), { tone: "warn" });
    return;
  }
  if ((useSessionStore.getState().composerDrafts?.[sessionId] ?? "").trim()) {
    showToast(translateText("Send or clear your draft first, then rewind and retry.", "先发送或清空草稿，再回退重试。"), { tone: "warn" });
    return;
  }
  try {
    const plan = await fetchPlan(sessionId, msgId);
    if (!plan.plan_hashes?.conversation || plan.status === "error" || (plan.status === "blocked" && plan.error_code !== "file_conflict")) {
      throw new Error(plan.error || "no plan");
    }
    const reply = await applyPlan(sessionId, msgId, "conversation", plan);
    if (!reply.ok || reply.data.status !== "committed") throw new Error(reply.data.error || "refused");
    finishRewind(sessionId, "conversation", reply.data, text);
  } catch {
    showToast(translateText("Couldn't rewind this message. Try again.", "未能回退这条消息，请重试。"), { tone: "error" });
  }
}
