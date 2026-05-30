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
import { useUserProfile } from "@/lib/user-profile";

import { MessageActions } from "./message-actions";

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
        (
          window as unknown as { setRunActive?: (a: boolean) => void }
        ).setRunActive?.(true);
        const w = window as Window & { ws?: WebSocket };
        if (w.ws && w.ws.readyState === WebSocket.OPEN) {
          w.ws.send(
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

  return (
    <div
      className={"message user" + (editing ? " is-editing" : "")}
      data-msg-id={msg.id}
    >
      <div className="message-header">
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
        <MessageActions msg={msg} onEdit={() => setEditing(true)} />
      </div>
      <div className="message-content">
        {editing ? (
          <EditBox msg={msg} onDone={() => setEditing(false)} />
        ) : (
          msg.content
        )}
      </div>
    </div>
  );
}
