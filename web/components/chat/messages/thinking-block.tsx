"use client";

/**
 * Collapsible "Thinking" block — React port of the legacy
 * `.chat-thinking` scaffold. Reuses the legacy CSS (05-chat.css): the
 * fold state is driven entirely by the `data-collapsed` attribute, so
 * this component only flips that string and the stylesheet does the
 * show/hide.
 */
import { useState } from "react";
import { useTranslation } from "@/lib/i18n";

export function ThinkingBlock({
  text,
  streaming,
}: {
  text: string;
  streaming?: boolean;
}) {
  // Default expanded so the user sees thinking content stream in
  // live. Click the fold button to collapse manually. Without this
  // the block looks "still" while content actually fills underneath
  // — users couldn't tell streaming from a single end-of-reply dump.
  const [collapsed, setCollapsed] = useState(false);
  const { text: tr } = useTranslation();
  if (!text) return null;

  return (
    <div className="chat-thinking" data-collapsed={collapsed ? "1" : "0"}>
      <button
        type="button"
        className="chat-fold-btn"
        onClick={() => setCollapsed((c) => !c)}
        onMouseDown={(e) => e.preventDefault()}
      >
        <span className="chat-fold-caret">{"▶"}</span>
        <span className="chat-fold-label">{tr("Thinking", "思考")}</span>
        <span className="chat-fold-elapsed">{streaming ? "…" : ""}</span>
      </button>
      <div className="chat-fold-content">{text}</div>
    </div>
  );
}
