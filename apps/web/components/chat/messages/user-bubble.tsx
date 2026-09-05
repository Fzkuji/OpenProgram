"use client";

/**
 * User message bubble — React port of legacy `addUserMessage` markup.
 * Plain text content (escaped); user turns are never markdown-rendered.
 *
 * The hover action bar is the React <MessageActions />; the pencil
 * swaps the content into an inline editor that POSTs `/api/chat/edit`
 * (a React port of legacy `message-actions-edit.js`).
 */
import { useRef, useState } from "react";

import { Button } from "@/components/ui/button";
import { useSessionStore, type ChatMsg } from "@/lib/session-store";
import { useTranslation } from "@/lib/i18n";
import { Avatar } from "@/components/avatar";
import { useUserProfile } from "@/lib/prefs/user-profile";

import { MessageActions } from "./message-actions";
import { useAvatarAlign } from "./use-avatar-align";
import { AttachmentChips, parseAttachments } from "./user-attachments";
import { setRunActive } from "@/lib/runtime-bridge/chat-handlers";
import { getSocket, runtimeState } from "@/lib/runtime-bridge/state";

function EditBox({
  msg,
  onDone,
}: {
  msg: ChatMsg;
  onDone: () => void;
}) {
  const { t, text } = useTranslation();
  const sessionId = useSessionStore((s) => s.currentSessionId);
  const [value, setValue] = useState(msg.content || "");
  const [submitting, setSubmitting] = useState(false);
  const ref = useRef<HTMLTextAreaElement>(null);

  function save() {
    const text = value.trim();
    if (!text || !sessionId || !msg.id) return;
    setSubmitting(true);
    fetch("/api/chat/edit", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        session_id: sessionId,
        msg_id: msg.id,
        content: text,
      }),
    })
      .then((r) => {
        if (!r.ok) {
          return r.json().then((e) => {
            throw new Error(e.error || r.statusText);
          });
        }
        return r.json();
      })
      .then(() => {
        setRunActive(true);
        // Same self-heal contract as retry: the result reloads the view
        // instead of pushing onto the pre-fork mirror (chat-handlers).
        runtimeState._pendingBranchReload[sessionId] = true;
        const sock = getSocket();
        if (sock && sock.readyState === WebSocket.OPEN) {
          sock.send(
            JSON.stringify({ action: "load_session", session_id: sessionId }),
          );
        }
        onDone();
      })
      .catch((err) => {
        setSubmitting(false);
        console.error("[message-edit] submit failed:", err);
      });
  }

  return (
    <>
      <textarea
        ref={ref}
        autoFocus
        className="message-edit-textarea"
        rows={Math.max(2, Math.min(20, value.split("\n").length + 1))}
        value={value}
        onChange={(e) => setValue(e.target.value)}
        onKeyDown={(e) => {
          if ((e.metaKey || e.ctrlKey) && e.key === "Enter") {
            e.preventDefault();
            save();
          } else if (e.key === "Escape") {
            e.preventDefault();
            onDone();
          }
        }}
      />
      <div className="message-edit-actions">
        <Button
          type="button"
          variant="outline"
          size="sm"
          onClick={onDone}
          disabled={submitting}
        >
          {t("sidebar.cancel")}
        </Button>
        <Button
          type="button"
          variant="default"
          size="sm"
          onClick={save}
          disabled={submitting}
        >
          {submitting ? text("Submitting...", "提交中...") : text("Save & resend", "保存并重新发送")}
        </Button>
      </div>
    </>
  );
}

export function UserBubble({ msg }: { msg: ChatMsg }) {
  const { text } = useTranslation();
  const [editing, setEditing] = useState(false);
  const profile = useUserProfile();

  // Pull attachment markers out of the prose so they render as chips
  // (Claude-Code style) instead of raw "[attached: …]" / inlined <file>
  // text. msg.content itself is untouched — this is display-only.
  const { attachments, text: cleanText } = parseAttachments(msg.content);

  // Align the side avatar to the first line of text inside the bubble.
  const { containerRef, avatarTop } = useAvatarAlign(
    `${msg.id}:${msg.content?.length || 0}:${editing}`,
  );

  return (
    <div
      ref={containerRef}
      className={"message user" + (editing ? " is-editing" : "")}
      data-msg-id={msg.id}
      /* Same per-turn article semantics as the assistant bubble, so
         screen-reader users can step through the transcript turn by
         turn and hear who is speaking. */
      role="article"
      aria-label={profile.name || text("User", "用户")}
    >
      <div className="message-header" style={{ top: avatarTop }}>
        {/* "You" avatar + name — from the local user profile
            (/settings/general → You), the counterpart to the agent
            profile. Defaults to a DiceBear glyph seeded "you" so it
            looks identical until the user customises it. */}
        <Avatar
          className="message-avatar user-avatar"
          size={28}
          radius={8}
          name={profile.name}
          config={profile.avatar}
        />
        <div className="message-sender">{profile.name || text("User", "用户")}</div>
      </div>
      <div className="message-content">
        {editing ? (
          <EditBox msg={msg} onDone={() => setEditing(false)} />
        ) : (
          <>
            <AttachmentChips items={attachments} />
            {cleanText}
          </>
        )}
      </div>
      {/* Action row at the bottom-right of the user bubble (mirrors the
          assistant's bottom-left). */}
      {!editing ? (
        <div className="message-actions-footer">
          {msg.steering ? (
            <span className="steering-badge">
              {text("Steered", "已注入")}
            </span>
          ) : null}
          <MessageActions msg={msg} onEdit={() => setEditing(true)} />
        </div>
      ) : null}
    </div>
  );
}
