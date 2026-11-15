"use client";

/**
 * The "Thinking…" status shown while a reply has not started: the row of a
 * running tool group (execution-strip.tsx) with its shimmering label, or,
 * with the code rain style on, the green label raining in like a new
 * message (components/chat/welcome/code-rain-style.tsx). It has nothing to
 * open yet, so it is no button and has no ›; the row that can open comes
 * with the reply's first thinking or tool call. The spinning activity mark
 * lives on the chat's sidebar row instead.
 */
import { useTranslation } from "@/lib/i18n";
import { CodeRainText, useCodeRainStyle } from "../../welcome/code-rain-style";

export function ThinkingStatus() {
  const { text } = useTranslation();
  const codeRain = useCodeRainStyle();
  const label = text("Thinking…", "思考中…");
  return (
    <span className="tl-toggle is-static thinking-status">
      {codeRain
        ? <CodeRainText className="tl-label" text={label} />
        : <span className="tl-label tl-label-shimmer">{label}</span>}
    </span>
  );
}
