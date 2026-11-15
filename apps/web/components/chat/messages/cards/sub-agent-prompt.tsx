"use client";

/** A prompt in a sub-agent's own conversation: what it was asked, one line
 *  that opens to the whole text (styles in sub-agent-transcript.css). */
import { useState } from "react";

import { promptBody } from "@/lib/chat/transcript/sub-agent-transcripts";
import { useTranslation } from "@/lib/i18n";
import type { ChatMsg } from "@/lib/session-store";

export function PromptTurn({ msg }: { msg: ChatMsg }) {
  const { text } = useTranslation();
  const [open, setOpen] = useState(false);
  const prompt = promptBody(msg.content || "");
  if (!prompt.text) return null;
  return (
    <button
      type="button"
      className="sub-agent-prompt"
      data-open={open ? "1" : "0"}
      aria-expanded={open}
      onClick={() => setOpen((value) => !value)}
    >
      <span className="sub-agent-prompt-label">
        {prompt.message ? text("Message", "消息") : text("Task", "任务")}
      </span>
      <span className="sub-agent-prompt-text">{prompt.text}</span>
    </button>
  );
}
