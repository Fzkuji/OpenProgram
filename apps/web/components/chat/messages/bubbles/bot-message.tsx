"use client";

/** A row of a conversation with an always-on Agent: the time when it opens
 *  a stretch, then what was said (lib/chat/transcript/bot-view.ts). */
import { useContext, useMemo, type ReactNode } from "react";
import { useShallow } from "zustand/react/shallow";

import { useTranslation } from "@/lib/i18n";
import { selectViewRows, useSessionStore, type ChatMsg } from "@/lib/session-store";
import { parseAttachments, type ParsedAttachment } from "../attachments/user-attachments";
import { AssistantFileCards } from "../attachments/assistant-file-cards";
import { botCitations, botFailure, botReplyFiles, botReplyText, botShows, botWorksAfterText, botTimeLine, botWritten, splitBubbles } from "@/lib/chat/transcript/bot-view";
import { continuedRows, joinedReply } from "@/lib/chat/transcript/restart-continuation";
import { BotViewContext, useRowSessionId } from "../list/transcript-view-scope";
import { useMarkdownReady } from "./markdown";
import { MarkdownText, errorHeadline, useReplyLive } from "./assistant-bubble";
import { ProseFileLinks } from "./prose-file-links";
import { CitationSources } from "./web-sources";
import { UserBubble } from "./user-bubble";

const NO_MESSAGES: ChatMsg[] = [];

export function BotTyping() {
  const { text } = useTranslation();
  return <div className="bot-row"><div className="bot-bubble bot-typing" role="status" aria-label={text("Typing", "正在输入")}><i /><i /><i /></div></div>;
}

/** The line under a reply that ended short; null when none is due. */
function FailureLine({ msg }: { msg: ChatMsg }) {
  const { text } = useTranslation();
  const failure = botFailure(msg);
  if (!failure) return null;
  const line = failure === "interrupted"
    ? text("This message stopped before it finished.", "这条消息没发完就中断了。")
    // Message buttons are not drawn here, so no rewind can be offered.
    : errorHeadline(msg, text, false) ?? text("This reply could not be completed.", "本次回复未能完成。");
  return <div className="bot-bubble bot-failed">{line}</div>;
}

function Reply({ msg, attachments, sessionId, streaming, working }: { msg: ChatMsg; attachments: ParsedAttachment[]; sessionId?: string; streaming: boolean; working: boolean }) {
  useMarkdownReady();
  const { text: written } = parseAttachments(botReplyText(msg));
  const bubbles = useMemo(() => splitBubbles(written, { streaming }), [written, streaming]);
  return <ProseFileLinks sessionId={sessionId} settled={!streaming}><div className="bot-row" data-msg-id={msg.id} role="article">
    {bubbles.map((bubble, index) => <div key={index} className="bot-bubble">
      <MarkdownText text={bubble} streaming={streaming && index === bubbles.length - 1} />
    </div>)}
    <CitationSources citations={botCitations(msg)} />
    {!streaming && attachments.length ? <AssistantFileCards msg={msg} sessionId={sessionId} attachments={attachments} /> : null}
    {working ? <BotTyping /> : null}
    <FailureLine msg={msg} />
  </div></ProseFileLinks>;
}

export function BotMessage({ msg, sessionIdOverride }: { msg: ChatMsg; sessionIdOverride?: string }) {
  const { locale, text } = useTranslation();
  const view = useContext(BotViewContext);
  const rowSessionId = useRowSessionId(sessionIdOverride);
  const at = view?.breaks.get(msg.id);
  // The chat this row belongs to comes from the row list, not the focused chat.
  const sessionId = rowSessionId ?? undefined;
  // A reply carrying on work a restart cut off reads as the one reply.
  const cutOff = useSessionStore(useShallow((s) => msg.rowKind === "assistant" ? continuedRows(selectViewRows(sessionId)(s), msg) : NO_MESSAGES));
  const reply = useMemo(() => joinedReply(cutOff.map(botWritten), botWritten(msg), (content) => parseAttachments(content).text), [cutOff, msg]);
  // File markers are saved per reply, usually only in content; a joined reply keeps every part's.
  const attachments = useMemo(() => botReplyFiles([...cutOff, msg], parseAttachments), [cutOff, msg]);
  const live = useReplyLive(reply, sessionId);
  const streaming = msg.rowKind === "assistant" && live.streaming;
  const typing = streaming && !live.waitingApproval;
  if (!botShows(reply)) return typing ? <BotTyping /> : null;
  let body: ReactNode;
  if (msg.rowKind === "user" || msg.rowKind === "received_message") body = <div className="bot-said"><UserBubble msg={msg} sessionIdOverride={sessionIdOverride} /></div>;
  else body = <Reply msg={reply} attachments={attachments} sessionId={sessionId} streaming={streaming} working={typing && botWorksAfterText(reply)} />;
  return <>
    {at ? <div className="bot-time">{botTimeLine(msg, at, new Date(), locale, text)}</div> : null}
    {body}
  </>;
}
