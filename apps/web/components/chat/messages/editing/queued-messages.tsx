"use client";

/**
 * Messages typed during a run, as one raised card docked above the input
 * box. A message on its way into the current turn is not here: it shows at
 * the end of the running reply (PendingSteers) and comes back to this card
 * only if adding it fails. Each row is one line: a tag says where the
 * message goes ("Next turn" while it waits for the run to finish, or why it
 * was not sent), then the text, then hover actions (add to this turn,
 * retry, edit, remove). Clicking the text expands the full message and its
 * attachments. The heading collapses the card; "Clear all" removes every
 * row that is not being edited. Editing or removing a row whose delivery is
 * unconfirmed first takes the message back from the turn; one the turn
 * already has stays and leaves as delivered.
 * Dragging a row's line, or Alt+↑/↓ on its text, changes the send order;
 * rows being delivered or edited stay put.
 */

import { useEffect, useRef, useState } from "react";
import { Reorder, useDragControls } from "framer-motion";
import { useTranslation } from "@/lib/i18n";
import { steeringIntoTurn, useSendQueue, type QueuedMessage } from "@/lib/chat/send/send-queue";
import { queuedHasAttachments, snapshotQueuedAttachments } from "@/lib/chat/send/queued-attachments";
import { steerQueuedMessage, takeBackQueuedSteer } from "@/lib/chat/send/steer-message";
import { showToast } from "@/lib/format-utils/toast";
import { CornerDownRight, Pencil, X, Paperclip, ChevronDown, ChevronUp, RotateCcw } from "lucide-react";
import { HoverTip } from "@/components/ui/tooltip";
import { Button } from "@/components/ui/button";
import { AttachmentStrip } from "../../composer/attach/strip/attachment-strip";
import { useComposerAttachments } from "../../composer/attach/use-composer-attachments";
import { attachmentsBlockSend } from "../../composer/attach/attachment-session-cache";
import { AttachmentChips, parseAttachments } from "../attachments/user-attachments";
import { maskSecrets } from "@/lib/chat/transcript/secret-mask";
import { useSessionStore } from "@/lib/session-store";
import styles from "./queued-messages.module.css";

const EMPTY: QueuedMessage[] = [];

type Text = ReturnType<typeof useTranslation>["text"];
type Tone = "steer" | "next" | "wait" | "error";

/** A row is being added to the current turn: its steer request or the
 *  command receipt it waits on is in flight, with no failure recorded. */
function addingToTurn(row: QueuedMessage): boolean {
  return Boolean((row.injecting || row.steerCommand) && !row.steerError);
}

/** The tag, the optional visible note under the line, and the full status
 *  sentence announced to screen readers. */
function rowState(row: QueuedMessage, text: Text, connected = true): { tone: Tone; tag: string; note?: string; status: string } {
  const locked = Boolean(row.injecting || row.steerCommand);
  const hasFiles = queuedHasAttachments(row);
  const next = text("Next turn", "下一轮");
  const notSent = text("Not sent", "未发送");
  if (row.steerError === "cancelled" && locked) {
    const note = text("Checking delivery after Stop…", "正在确认停止前的发送结果…");
    return { tone: "wait", tag: text("Checking", "确认中"), note, status: note };
  }
  if (row.steerError === "ended") {
    const note = text("The original turn ended", "原来的轮次已结束");
    return { tone: "error", tag: notSent, note, status: text("Not sent · original turn ended", "未发送，原来的轮次已结束") };
  }
  if (row.steerError === "cancelled") {
    const note = text("The turn was stopped", "当前轮已停止");
    return { tone: "error", tag: notSent, note, status: text("Not sent · turn stopped", "未发送，当前轮已停止") };
  }
  if (row.deliveryError) {
    const note = text("Retry or edit", "可重试或编辑");
    return { tone: "error", tag: notSent, note, status: text("Not sent · retry or edit", "未发送，可重试或编辑") };
  }
  if (row.steerError === "unconfirmed") {
    const note = text("Delivery unconfirmed — retry to check", "发送结果待确认，请重试查询");
    return { tone: "wait", tag: text("Unconfirmed", "待确认"), note, status: note };
  }
  if (locked) {
    return { tone: "steer", tag: text("This turn", "插入当前轮"),
      status: text("Waiting to add to current turn…", "等待补充到当前轮…") };
  }
  if (row.steerError === "too_long") {
    const note = text("Over the 4,096-character limit for adding to this turn", "超出插入当前轮的 4,096 字符限制");
    return { tone: "next", tag: next, note,
      status: text("Queued · over the 4,096-character limit for current-turn input", "排队中，超出当前轮补充的 4,096 字符限制") };
  }
  if (row.steerError) {
    const note = text("Couldn't add to this turn", "未能插入当前轮");
    return { tone: "next", tag: next, note, status: text("Queued for the next turn", "已排队，将在下一轮发送") };
  }
  if (!connected) {
    const note = text("Sends when the connection is back", "连接恢复后发送");
    return { tone: "wait", tag: text("Waiting for connection", "等待连接"), note, status: note };
  }
  return { tone: "next", tag: next,
    status: hasFiles ? text("Queued with attachments", "附件消息排队中") : text("Queued", "排队中") };
}

function QueueEditor({ row, sessionId }: { row: QueuedMessage; sessionId: string }) {
  const { text } = useTranslation();
  const [value, setValue] = useState(row.text);
  const [initial] = useState(() => snapshotQueuedAttachments(row));
  const files = useComposerAttachments(`queue:${sessionId}:${row.id}`, initial);
  const mounted = useRef(false);
  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
      queueMicrotask(() => {
        if (mounted.current) return;
        const queue = useSendQueue.getState();
        queue.endEdit(sessionId, row.id);
        queue.drain(sessionId);
      });
    };
  }, [sessionId, row.id]);
  const blocked = Boolean(attachmentsBlockSend(files.pendingImages, files.pendingDocs))
    || (!value.trim() && !files.pendingImages.length && !files.pendingDocs.length);
  const cancel = () => useSendQueue.getState().endEdit(sessionId, row.id);
  const save = () => {
    if (blocked) return;
    useSendQueue.getState().updateDraft(sessionId, row.id, {
      text: value.trim(), images: files.pendingImages, docs: files.pendingDocs,
    });
  };
  return <div className={styles.editor} ref={files.composerRootRef} data-file-drop=""
    onDragOver={files.onDragOver} onDragLeave={files.onDragLeave} onDrop={files.onDrop}>
    <AttachmentStrip sessionId={sessionId} pendingImages={files.pendingImages} pendingDocs={files.pendingDocs}
      imageError={files.imageError} fileInputRef={files.fileInputRef} onFileInputChange={files.onFileInputChange}
      onRemoveImage={files.removeImage} onRemoveDoc={files.removeDoc} onDismissError={() => files.setImageError(null)} />
    <textarea autoFocus aria-label={text("Edit queued message", "编辑排队消息")}
      value={value} rows={3} onChange={event => setValue(event.target.value)}
      onPaste={event => {
        if (event.clipboardData.files.length) {
          event.preventDefault(); void files.addFiles(Array.from(event.clipboardData.files));
        }
      }}
      onKeyDown={event => {
        if (event.key === "Escape") { event.preventDefault(); cancel(); }
        if (event.key === "Enter" && (event.metaKey || event.ctrlKey)) { event.preventDefault(); save(); }
      }} />
    <div className="flex items-center gap-[6px] px-[8px] pb-[8px]">
      <Button type="button" variant="elevated" size="sm" onClick={files.onPickImages}><Paperclip size={14} />{text("Add attachment", "添加附件")}</Button>
      <span className="flex-1" />
      <Button type="button" variant="elevated" size="sm" onClick={cancel}>{text("Cancel", "取消")}</Button>
      <Button type="button" size="sm" onClick={save} disabled={blocked}>{text("Save", "保存")}</Button>
    </div>
  </div>;
}

function RowAction({ label, onClick, disabled, children }: {
  label: string; onClick(): void; disabled?: boolean; children: React.ReactNode;
}) {
  return <HoverTip label={label}>
    <Button type="button" variant="ghost" size="icon-xs" disabled={disabled} onClick={onClick} aria-label={label}>
      {children}
    </Button>
  </HoverTip>;
}

function QueueRow({ row, sessionId, movable, onMove }: {
  row: QueuedMessage; sessionId: string; movable: boolean; onMove(delta: -1 | 1): void;
}) {
  const { text } = useTranslation();
  const [expanded, setExpanded] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);
  const controls = useDragControls();
  // The click that ends a drag must not also toggle the row open.
  const dragged = useRef(false);
  const locked = Boolean(row.injecting || row.steerCommand);
  const hasFiles = queuedHasAttachments(row);
  const fileCount = (row.images?.length ?? 0) + (row.docs?.length ?? 0);
  // Display only: the queued text still sends with its secrets intact.
  const parsed = parseAttachments(maskSecrets(row.text));
  const connected = useSessionStore((s) => s.wsStatus === "open");
  const state = rowState(row, text, connected);
  const firstLine = parsed.text.split("\n").find(line => line.trim()) ?? "";
  // The steer request itself is being prepared; once its command exists the
  // message can still be taken back until a safe point takes it.
  const preparing = Boolean(row.injecting && !row.steerCommand);
  const whenTakenBack = async (then: () => void) => {
    if (!row.steerCommand) return then();
    if (await takeBackQueuedSteer(sessionId, row.id)) return then();
    if (useSendQueue.getState().queues[sessionId]?.some(item => item.id === row.id)) {
      showToast(text("Already being added to this turn", "已在插入当前轮，无法撤回"));
    }
  };
  const canRetry = !locked && (row.deliveryError || ["cancelled", "ended"].includes(row.steerError ?? ""));
  const canSteer = Boolean(row.steerCommand) || !["cancelled", "ended"].includes(row.steerError ?? "");
  const steerLabel = row.steerCommand
    ? text("Retry delivery confirmation", "重试确认发送结果")
    : text("Add to this turn", "插入当前轮");
  if (row.editing) {
    return <Reorder.Item as="article" value={row.id} dragListener={false} className={styles.row}
      data-queued-message={row.id} data-editing="true"
      aria-label={text("Queued message", "排队消息")}>
      <QueueEditor row={row} sessionId={sessionId} />
      <span role="status" className={styles.srOnly}>{text("Editing · send paused", "编辑中，暂停发送")}</span>
    </Reorder.Item>;
  }
  return <Reorder.Item as="article" value={row.id} dragListener={false} dragControls={controls}
    className={styles.row} data-queued-message={row.id} data-tone={state.tone}
    aria-label={text("Queued message", "排队消息")}
    whileDrag={{ boxShadow: "var(--shadow)", zIndex: 1 }}
    transition={{ type: "spring", stiffness: 600, damping: 40 }}
    onDragStart={() => { dragged.current = true; }}
    onDragEnd={() => { window.setTimeout(() => { dragged.current = false; }, 50); }}>
    <div className="relative flex items-center gap-[8px] min-h-[36px] p-[0_8px_0_14px]" onPointerDown={(event) => {
      if (!movable || event.button !== 0) return;
      if ((event.target as Element).closest(`.${styles.actions}`)) return;
      controls.start(event);
    }}>
      <span className={styles.tag} data-tone={state.tone}>
        {state.tone === "steer" || state.tone === "wait" ? <i aria-hidden="true" /> : null}
        {state.tag}
      </span>
      {hasFiles && <span className={styles.files}><Paperclip size={12} aria-hidden="true" />{fileCount}</span>}
      <button type="button" className={styles.text} aria-expanded={expanded}
        aria-keyshortcuts={movable ? "Alt+ArrowUp Alt+ArrowDown" : undefined}
        onClick={() => { if (!dragged.current) setExpanded(!expanded); }}
        onKeyDown={(event) => {
          if (!movable || !event.altKey || (event.key !== "ArrowUp" && event.key !== "ArrowDown")) return;
          event.preventDefault();
          onMove(event.key === "ArrowUp" ? -1 : 1);
        }}>
        {firstLine || text("Attachments", "附件")}
      </button>
      <div className={styles.actions}>
        <span className="message-timestamp">
          {new Date(row.queuedAt).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}
        </span>
        {canSteer && <RowAction label={steerLabel}
          disabled={row.injecting || (!!row.steerCommand && !row.steerError)}
          onClick={() => void steerQueuedMessage(sessionId, row.id)}><CornerDownRight size={14} /></RowAction>}
        {canRetry && <RowAction label={text("Retry send", "重试发送")}
          onClick={() => useSendQueue.getState().retryDraft(sessionId, row.id)}><RotateCcw size={14} /></RowAction>}
        <RowAction label={text("Edit queued message", "编辑排队消息")} disabled={preparing}
          onClick={() => void whenTakenBack(() => useSendQueue.getState().beginEdit(sessionId, row.id))}><Pencil size={14} /></RowAction>
        <RowAction label={text("Remove from queue", "从队列移除")} disabled={preparing}
          onClick={() => void whenTakenBack(() => useSendQueue.getState().removeDraft(sessionId, row.id))}><X size={14} /></RowAction>
      </div>
    </div>
    {state.note && <div className={styles.note} data-tone={state.tone}>{state.note}</div>}
    <span role="status" className={styles.srOnly}>{state.status}</span>
    {expanded && <div className="flex flex-col gap-[6px] p-[0_14px_10px]">
      {hasFiles && <AttachmentStrip sessionId={sessionId} readOnly pendingImages={row.images ?? []} pendingDocs={row.docs ?? []}
        imageError={null} fileInputRef={inputRef} onFileInputChange={() => {}}
        onRemoveImage={() => {}} onRemoveDoc={() => {}} onDismissError={() => {}} />}
      <AttachmentChips sessionId={sessionId} items={parsed.attachments} />
      {parsed.text && <div className={styles.content}>{parsed.text}</div>}
    </div>}
  </Reorder.Item>;
}

const pinnedRow = (row: QueuedMessage) => Boolean(row.injecting || row.steerCommand || row.editing);

export function QueuedMessages({ sessionId }: { sessionId: string | null }) {
  const { text } = useTranslation();
  const queued = useSendQueue(s => sessionId ? s.queues[sessionId] ?? EMPTY : EMPTY);
  const [collapsed, setCollapsed] = useState(false);
  // A message going into the running turn shows in the transcript at once
  // (PendingSteers); only one that waits or failed stays in this card.
  const rows = queued.filter(row => !steeringIntoTurn(row));
  if (!sessionId || !rows.length) return null;
  const adding = rows.filter(addingToTurn).length;
  const editing = rows.some(row => row.editing);
  const clearable = rows.some(row => !row.editing && (row.steerCommand || !row.injecting));
  return <section className={styles.card} data-queued-messages aria-label={text("Queued messages", "排队消息")}>
    <button type="button" className={styles.heading} disabled={editing} aria-expanded={!collapsed}
      onClick={() => setCollapsed(!collapsed)}>
      <span className={styles.title}>
        {text(`${rows.length} queued ${rows.length === 1 ? "message" : "messages"}`, `${rows.length} 条排队消息`)}
      </span>
      {adding > 0 && <span className={styles.sub}>
        {text(`· ${adding} adding to this turn`, `· ${adding} 条插入当前轮`)}
      </span>}
      <span className="flex-1" />
      <span className={styles.chevron} aria-hidden="true">
        {collapsed ? <ChevronDown size={14} /> : <ChevronUp size={14} />}
      </span>
    </button>
    {!collapsed && <>
      <Reorder.Group as="div" axis="y" className={styles.list} values={rows.map(row => row.id)}
        onReorder={(ids: string[]) => useSendQueue.getState().reorder(sessionId, ids)}>
        {rows.map((row, index) => <QueueRow key={row.id} row={row} sessionId={sessionId}
          movable={!pinnedRow(row) && rows.filter(other => !pinnedRow(other)).length > 1}
          onMove={(delta) => {
            // Step past pinned rows: they keep their slot.
            let target = index + delta;
            while (rows[target] && pinnedRow(rows[target])) target += delta;
            if (!rows[target]) return;
            const ids = rows.map(other => other.id);
            [ids[index], ids[target]] = [ids[target], ids[index]];
            useSendQueue.getState().reorder(sessionId, ids);
          }} />)}
      </Reorder.Group>
      <div className={styles.foot}>
        <Button type="button" variant="destructive" size="xs" disabled={!clearable}
          onClick={async () => {
            // Rows on their way into the turn are taken back first; one the
            // turn already has is not removed.
            await Promise.all(rows.filter(row => row.steerCommand && !row.editing)
              .map(row => takeBackQueuedSteer(sessionId, row.id)));
            const queue = useSendQueue.getState();
            if (queue.clearDrafts(sessionId)) queue.drain(sessionId);
          }}>
          {text("Clear all", "全部清除")}
        </Button>
      </div>
    </>}
  </section>;
}
