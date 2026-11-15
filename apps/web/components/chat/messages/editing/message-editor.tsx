"use client";
import { requestSessionLoad } from "@/lib/runtime-bridge/session/session-load";

import { useLayoutEffect, useRef, useState } from "react";
import { X } from "lucide-react";
import { Button } from "@/components/ui/button";
import { HoverTip, TipBody } from "@/components/ui/tooltip";
import { SolarIcon } from "@/components/solar-icons";
import { useSessionStore, type ChatMsg } from "@/lib/session-store";
import { useTranslation } from "@/lib/i18n";
import { absRawFileUrl } from "@/lib/files/files-shared";
import { buildAttachmentEnvelope } from "@/lib/chat/send/attachment-marker";
import { getSocket } from "@/lib/runtime-bridge/state";
import { setRunActive } from "@/lib/runtime-bridge/session/chat-handlers";
import { ChatInputRow } from "../../composer/input/chat-input-row";
import { useFileMention } from "../../composer/attach/mention/use-file-mention";
import { expandAtMentions } from "../../composer/attach/mention/at-mention";
import { useComposerAttachments } from "../../composer/attach/use-composer-attachments";
import { attachmentsBlockSend } from "../../composer/attach/attachment-session-cache";
import { AttachmentStrip } from "../../composer/attach/strip/attachment-strip";
import { readImageFile, type PendingImage } from "../../composer/attach/image/image-attach";
import { AttachmentChips, parseAttachments } from "../attachments/user-attachments";
import styles from "./message-editor.module.css";

export function MessageEditor({ msg, sessionId, onDone }: {
  msg: ChatMsg; sessionId: string | null; onDone: () => void;
}) {
  const { text } = useTranslation();
  const [initial] = useState(() => parseAttachments(msg.content, true));
  const [kept, setKept] = useState(initial.attachments);
  const [value, setValue] = useState(initial.text);
  const [submitting, setSubmitting] = useState(false);
  const saving = useRef(false);
  const [error, setError] = useState<string | null>(null);
  const ref = useRef<HTMLTextAreaElement>(null);
  const [emptyAttachments] = useState(() => ({ images: [], docs: [] }));
  const files = useComposerAttachments(`edit:${sessionId}:${msg.id}`, emptyAttachments);
  const mention = useFileMention({ input: value, setInput: setValue, textareaRef: ref, scope: { sessionId } });
  useLayoutEffect(() => {
    if (!ref.current) return;
    ref.current.style.height = "auto";
    ref.current.style.height = `${Math.min(360, ref.current.scrollHeight)}px`;
  }, [value]);
  const blocked = !sessionId || Boolean(attachmentsBlockSend(files.pendingImages, files.pendingDocs))
    || (!value.trim() && !kept.length && !files.pendingImages.length && !files.pendingDocs.length);

  async function save() {
    if (blocked || saving.current || !sessionId) return;
    saving.current = true; setSubmitting(true); setError(null);
    const restored: PendingImage[] = [];
    try {
      // Replay retained images from their immutable, authorized session copy.
      // Documents keep their exact wire marker (including legacy inline files).
      const markers: string[] = [];
      for (const item of kept) {
        if (item.path && /\.(png|jpe?g|webp|gif)$/i.test(item.filename)) {
          const response = await fetch(absRawFileUrl(item.path, sessionId));
          if (!response.ok) throw new Error(text(`Cannot read ${item.filename}`, `无法读取 ${item.filename}`));
          const image = await readImageFile(await response.blob(), item.filename);
          restored.push({ ...image, sourcePath: item.sourcePath || item.path });
        } else if (item.raw) markers.push(item.raw);
      }
      const envelope = buildAttachmentEnvelope([...restored, ...files.pendingImages], files.pendingDocs);
      const expanded = await expandAtMentions(value.trim(), { sessionId });
      const content = [...markers, ...envelope.mentions, expanded.text].filter(Boolean).join("\n\n");
      const response = await fetch("/api/chat/edit", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ session_id: sessionId, msg_id: msg.id, content,
          attachments: [...envelope.imagesPayload, ...envelope.docsPayload] }),
      });
      const result = await response.json();
      if (!response.ok) throw new Error(result.error || response.statusText);
      if (useSessionStore.getState().currentSessionId === sessionId) setRunActive(true);
      const socket = getSocket();
      if (socket?.readyState === WebSocket.OPEN) requestSessionLoad({ action: "load_session", session_id: sessionId }, true);
      onDone();
    } catch (err) {
      setError(err instanceof Error ? err.message : text("Could not save. Try again.", "保存失败，请重试。"));
    } finally {
      for (const image of restored) if (image.previewUrl) URL.revokeObjectURL(image.previewUrl);
      saving.current = false; setSubmitting(false);
    }
  }

  return <div className={styles.editor} ref={files.composerRootRef} data-file-drop="" data-dragging={files.dragActive}
    onDragOver={e => { if (!saving.current) files.onDragOver(e); }}
    onDragLeave={files.onDragLeave} onDrop={e => {
      e.preventDefault(); e.stopPropagation(); if (!saving.current) files.onDrop(e);
    }}>
    <fieldset disabled={submitting}>
      {!!kept.length && <div className="flex flex-wrap gap-[6px] p-[8px_12px_0]">{kept.map((item, index) =>
        <div className={`${styles.existingItem} flex items-center min-w-0 max-w-full`} key={`${index}:${item.filename}`}>
          <AttachmentChips items={[item]} sessionId={sessionId} />
          <Button variant="ghost" size="icon" type="button" aria-label={text(`Remove ${item.filename}`, `移除 ${item.filename}`)}
            onClick={() => setKept(kept.filter((_, i) => i !== index))}><X size={14} /></Button>
        </div>,
      )}</div>}
      <AttachmentStrip sessionId={sessionId} pendingImages={files.pendingImages} pendingDocs={files.pendingDocs}
        imageError={files.imageError} fileInputRef={files.fileInputRef} onFileInputChange={files.onFileInputChange}
        onRemoveImage={files.removeImage} onRemoveDoc={files.removeDoc} onDismissError={() => files.setImageError(null)} />
      <ChatInputRow textareaRef={ref} input={value} setInput={setValue} autoFocus
        inputId={`edit-message-${msg.id}`} placeholder={text("Edit message", "编辑消息")}
        {...mention} onFocus={() => {}} onBlur={mention.closeMenu}
        onPaste={event => { if (event.clipboardData.files.length) {
          event.preventDefault(); void files.addFiles(Array.from(event.clipboardData.files));
        } }}
        onKeyDown={event => {
          if (event.nativeEvent.isComposing) return;
          if (mention.atToken && mention.fileMatches.length && mention.fileMenuPos) {
            if (event.key === "ArrowDown" || event.key === "ArrowUp") {
              event.preventDefault(); mention.setFileMenuIndex(i => (i + (event.key === "ArrowDown" ? 1 : -1) + mention.fileMatches.length) % mention.fileMatches.length); return;
            }
            if (event.key === "Enter" && !event.metaKey && !event.ctrlKey) {
              event.preventDefault(); mention.pickFile(mention.fileMatches[mention.fileMenuIndex]); return;
            }
            if (event.key === "Escape") { event.preventDefault(); mention.closeMenu(); return; }
          }
          if ((event.metaKey || event.ctrlKey) && event.key === "Enter") { event.preventDefault(); void save(); }
          else if (event.key === "Escape") { event.preventDefault(); if (!saving.current) onDone(); }
        }} />
      {error && <p className={styles.error} role="alert">{error}</p>}
      <div className={`${styles.actions} flex items-center gap-[8px] p-[4px_6px_6px]`}>
        <HoverTip label={<TipBody title={text("Add images or files", "添加图片或文件")} />}>
          <Button type="button" variant="ghost" size="icon-xs" aria-label={text("Add images or files", "添加图片或文件")}
            onClick={files.onPickImages}><SolarIcon name="paperclip" size={20} /></Button>
        </HoverTip>
        <span />
        <Button type="button" variant="elevated" size="xs" onClick={onDone}>{text("Cancel", "取消")}</Button>
        <Button type="button" size="xs" onClick={() => void save()} disabled={blocked || submitting}>
          {submitting ? text("Submitting…", "提交中…") : text("Save & resend", "保存并重新发送")}
        </Button>
      </div>
    </fieldset>
  </div>;
}
