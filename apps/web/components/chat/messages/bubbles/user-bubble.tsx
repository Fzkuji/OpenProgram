"use client";

/**
 * User message bubble — React port of legacy `addUserMessage` markup.
 * Plain text content (escaped); user turns are never markdown-rendered.
 *
 * The hover action bar is the React <MessageActions />; the pencil
 * swaps the content into an inline editor that POSTs `/api/chat/edit`
 * (a React port of legacy `message-actions-edit.js`).
 */
import { useState, type MouseEvent } from "react";
import { CircleAlert } from "lucide-react";

import { useSessionStore, type ChatMsg } from "@/lib/session-store";
import { useTranslation } from "@/lib/i18n";
import { useUserProfile } from "@/lib/prefs/user-profile";

import { MessageActions } from "./message-actions";
import { AttachmentChips, parseAttachments } from "../attachments/user-attachments";
import { MessageEditor } from "../editing/message-editor";
import { RewindContextMenu } from "../../rewind/rewind-context-menu";
import { RecordingCard } from "../../recording/recording-card";
import { renderUserMarkdown } from "./markdown";
import { collapsePastedSpans, pasteSentinel } from "@/lib/chat/send/paste-elements";
import { collapseSecrets, maskSecrets, secretParts, secretSentinel } from "@/lib/chat/transcript/secret-mask";
import { channelName, isChannelSource, speakerName, splitRoomEvent, splitRoomHistory, stripLegacySpeakerLabel } from "@/lib/chat/transcript/channel-speaker";
import { buttonVariants } from "@/components/ui/button";
import { SolarIcon } from "@/components/solar-icons";
import { useRowSessionId } from "../list/transcript-view-scope";

/** Send a message that never reached the backend again, through the send
 *  queue (it goes as soon as the session is idle), in place of its row. */
async function resend(sessionId: string, msg: ChatMsg): Promise<void> {
  const { requeueRejected, useSendQueue } = await import("@/lib/chat/send/send-queue");
  requeueRejected(sessionId, msg.content);
  useSessionStore.getState().applyLocalChange(sessionId, { kind: "local_remove", rowId: msg.id });
  useSendQueue.getState().drain(sessionId);
}

/** Under a send that got no ACK: it says so, and offers to send it again
 *  when the text alone is the message (files need the composer). */
function NotSent({ msg, sessionId, withFiles }: { msg: ChatMsg; sessionId?: string | null; withFiles: boolean }) {
  const { text } = useTranslation();
  return (
    <div className="user-not-sent" role="status">
      <CircleAlert size={14} aria-hidden="true" />
      <span>{text("Not sent", "未发送")}{msg.errorDetail ? `: ${msg.errorDetail}` : ""}</span>
      {sessionId && !withFiles ? (
        <button
          type="button"
          className={buttonVariants({ variant: "ghost", size: "xs" })}
          onClick={() => void resend(sessionId, msg)}
        >
          {text("Retry", "重试")}
        </button>
      ) : null}
    </div>
  );
}

function escapeAttr(value: string): string {
  return value.replace(/&/g, "&amp;").replace(/"/g, "&quot;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}

/** Replace each sentinel left by collapsePastedSpans with a chip button
 *  whose tooltip previews the pasted text. */
function withPasteChips(html: string, spans: { label: string; text: string }[], hint: string): string {
  let out = html;
  spans.forEach((span, index) => {
    const masked = maskSecrets(span.text);
    const preview = masked.slice(0, 400) + (masked.length > 400 ? "…" : "");
    const chip = `<button type="button" class="user-paste-chip" title="${escapeAttr(`${hint}\n\n${preview}`)}">`
      + `<span class="user-paste-chip-icon" aria-hidden="true">¶</span>${escapeAttr(span.label)}</button>`;
    out = out.split(pasteSentinel(index)).join(chip);
  });
  return out;
}

const ICON = '<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" '
  + 'stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">';
const EYE = ICON + '<path d="M2.062 12.348a1 1 0 0 1 0-.696 10.75 10.75 0 0 1 19.876 0 1 1 0 0 1 0 .696 10.75 10.75 0 0 1-19.876 0"/>'
  + '<circle cx="12" cy="12" r="3"/></svg>';
const EYE_OFF = ICON + '<path d="M10.733 5.076a10.744 10.744 0 0 1 11.205 6.575 1 1 0 0 1 0 .696 10.747 10.747 0 0 1-1.444 2.49"/>'
  + '<path d="M14.084 14.158a3 3 0 0 1-4.242-4.242"/>'
  + '<path d="M17.479 17.499a10.75 10.75 0 0 1-15.417-5.151 1 1 0 0 1 0-.696 10.75 10.75 0 0 1 4.446-5.143"/>'
  + '<path d="m2 2 20 20"/></svg>';

/** Replace each secret sentinel with the masked secret and an eye button
 *  that shows or hides it. */
function withSecrets(html: string, secrets: string[], revealed: ReadonlySet<number>, labels: [string, string, string]): string {
  let out = html;
  secrets.forEach((secret, index) => {
    const shown = revealed.has(index);
    const parts = secretParts(secret);
    const body = shown
      ? escapeAttr(secret)
      : `${escapeAttr(parts.shown)}<span class="user-secret-dots" role="img" aria-label="${escapeAttr(labels[2])}">${"●".repeat(parts.dots)}</span>`;
    const span = `<span class="user-secret"><span class="user-secret-text">${body}</span>`
      + `<button type="button" class="user-secret-toggle" data-secret-index="${index}" aria-pressed="${shown}" `
      + `aria-label="${escapeAttr(shown ? labels[1] : labels[0])}">${shown ? EYE_OFF : EYE}</button></span>`;
    out = out.split(secretSentinel(index)).join(span);
  });
  return out;
}

const MD_OPEN = '<span class="md-rendered">';

/** Split a rendered user message into its opening paragraph and the blocks
 *  after it. Each block of `.md-rendered` is a grid item, so a label beside
 *  it can never share its first line; the opening paragraph is lifted out
 *  instead. `lead` is null when the message opens with another block (a
 *  list, a code block); plain text (no markdown wrapper) is all lead. */
export function splitLeadParagraph(html: string): { lead: string | null; rest: string } {
  if (!html.startsWith(MD_OPEN)) return { lead: html, rest: "" };
  if (!html.startsWith(MD_OPEN + "<p>")) return { lead: null, rest: html };
  const start = MD_OPEN.length + 3;
  const end = html.indexOf("</p>", start);
  if (end < 0) return { lead: null, rest: html };
  const after = html.slice(end + 4).replace(/^\s+/, "");
  return {
    lead: html.slice(start, end),
    rest: after === "</span>" ? "" : MD_OPEN + after,
  };
}

/** The message that set a Goal starts with the Goal label, on the first
 *  line of its text: the label and the opening paragraph are one paragraph,
 *  and the blocks after it follow as in any message. The label is drawn,
 *  not part of the message: copy and edit see the objective alone. */
function GoalLabel() {
  const { text } = useTranslation();
  return (
    <span className="user-goal-label">
      <SolarIcon name="target" size={16} motionPreset="none" />
      {text("Goal", "目标")}
    </span>
  );
}

function UserMarkdown({ html, goal, onClick }: { html: string; goal: boolean; onClick: (event: MouseEvent<HTMLDivElement>) => void }) {
  if (!goal) return <div className="user-md" onClick={onClick} dangerouslySetInnerHTML={{ __html: html }} />;
  const { lead, rest } = splitLeadParagraph(html);
  return (
    <div className="user-goal" onClick={onClick}>
      <p className="user-goal-lead">
        <GoalLabel />
        {lead !== null ? <span className="user-md" dangerouslySetInnerHTML={{ __html: lead }} /> : null}
      </p>
      {rest ? <div className="user-md" dangerouslySetInnerHTML={{ __html: rest }} /> : null}
    </div>
  );
}

export function UserBubble({ msg, sessionIdOverride }: { msg: ChatMsg; sessionIdOverride?: string }) {
  const sessionId = useRowSessionId(sessionIdOverride);
  const { text } = useTranslation();
  const [editing, setEditing] = useState(false);
  const profile = useUserProfile();

  // Pull attachment markers out of the prose so they render as chips
  // (Claude-Code style) instead of raw "[attached: …]" / inlined <file>
  // text. msg.content itself is untouched — this is display-only.
  // Pasted spans (Codex text_elements) show as one collapsed chip each
  // until the user expands them; the model always received the full text.
  const [pastesExpanded, setPastesExpanded] = useState(false);
  // A channel message names its sender above the bubble, not in the text.
  const channel = isChannelSource(msg.source) ? msg.source : null;
  const roomEvent = channel ? splitRoomEvent(msg.content) : { roomEvent: false, rest: msg.content };
  const room = channel
    ? splitRoomHistory(stripLegacySpeakerLabel(roomEvent.rest, msg.speaker))
    : { history: [], rest: msg.content };
  const content = room.rest;
  const [historyOpen, setHistoryOpen] = useState(false);
  const sender = channel ? speakerName(msg.speaker) : "";
  const collapsed = !pastesExpanded && msg.pastedElements?.length
    ? collapsePastedSpans(content, msg.pastedElements)
    : { content, spans: [] as { label: string; text: string }[] };
  const { attachments, text: proseText, recordings } = parseAttachments(collapsed.content);
  // Access tokens and private keys show masked, each with an eye button.
  const { content: cleanText, secrets } = collapseSecrets(proseText);
  const [revealState, setRevealState] = useState(() => ({
    sessionId, messageId: msg.id, proseText, indices: new Set<number>(),
  }));
  // Indices identify secrets only within this exact rendered message.
  const sameRevealScope = revealState.sessionId === sessionId
    && revealState.messageId === msg.id && revealState.proseText === proseText;
  const revealed = sameRevealScope ? revealState.indices : new Set<number>();
  if (!sameRevealScope) {
    setRevealState({ sessionId, messageId: msg.id, proseText, indices: revealed });
  }

  // A send still waiting for its ACK, or one that never got it.
  const pending = msg.status === "pending";
  const notSent = msg.status === "error";
  return (
    <RewindContextMenu msg={msg} sessionId={sessionId} enabled={!editing && !pending && !notSent}>
    <div
      className={"message user" + (editing ? " is-editing" : "") + (pending ? " is-pending" : "")}
      data-msg-id={msg.id}
      /* Same per-turn article semantics as the assistant bubble, so
         screen-reader users can step through the transcript turn by
         turn and hear who is speaking. */
      role="article"
      aria-label={sender || profile.name || text("User", "用户")}
    >
      {channel && !editing ? (
        <div className="user-channel-speaker">
          {[
            channelName(channel, text),
            sender,
            // Not addressed to the bot: its reply stays here, unposted.
            roomEvent.roomEvent ? text("overheard", "旁听") : "",
          ].filter(Boolean).join(" · ")}
        </div>
      ) : null}
      <div className="message-content">
        {editing ? (
          <MessageEditor msg={msg} sessionId={sessionId} onDone={() => setEditing(false)} />
        ) : (
          <>
            {room.history.length ? (
              <div className="user-room-history">
                <button
                  type="button"
                  className="user-room-history-toggle"
                  aria-expanded={historyOpen}
                  onClick={() => setHistoryOpen((open) => !open)}
                >
                  {text(
                    `${room.history.length} earlier ${room.history.length === 1 ? "message" : "messages"} in the chat`,
                    `群里之前的 ${room.history.length} 条消息`,
                  )}
                </button>
                {historyOpen ? (
                  <ul className="user-room-history-list">
                    {room.history.map((line, index) => (
                      <li key={index}>
                        <span className="user-room-history-speaker">{line.speaker}</span>
                        {line.content}
                      </li>
                    ))}
                  </ul>
                ) : null}
              </div>
            ) : null}
            {recordings.map((recording) => <RecordingCard key={recording.id} info={recording} />)}
            <AttachmentChips sessionId={sessionId} items={attachments} />
            {cleanText ? (
              <UserMarkdown
                goal={msg.startsGoal === true}
                onClick={(event) => {
                  const target = event.target as Element;
                  if (target.closest?.(".user-paste-chip")) setPastesExpanded(true);
                  const toggle = target.closest?.<HTMLElement>(".user-secret-toggle");
                  if (toggle) {
                    const index = Number(toggle.dataset.secretIndex);
                    setRevealState((prev) => {
                      const next = new Set(prev.indices);
                      if (!next.delete(index)) next.add(index);
                      return { ...prev, indices: next };
                    });
                  }
                }}
                html={withSecrets(
                  withPasteChips(renderUserMarkdown(cleanText), collapsed.spans, text("Show pasted text", "展开粘贴内容")),
                  secrets,
                  revealed,
                  [text("Show secret", "显示密钥"), text("Hide secret", "隐藏密钥"), text("hidden", "已隐藏")],
                )}
              />
            ) : null}
          </>
        )}
      </div>
      {/* Action row at the bottom-right of the user bubble (mirrors the
          assistant's bottom-left). */}
      {notSent && !editing ? (
        <NotSent msg={msg} sessionId={sessionId} withFiles={attachments.length > 0 || recordings.length > 0} />
      ) : null}
      {!editing && !pending && !notSent ? (
        <div className="message-actions-footer">
          <MessageActions sessionIdOverride={sessionIdOverride} msg={msg} onEdit={() => setEditing(true)} />
        </div>
      ) : null}
    </div>
    </RewindContextMenu>
  );
}
