"use client";

/**
 * Collapsible "Thinking" block — same ``.inline-tree`` visual shell
 * as ``RuntimeBlock`` / ``ToolsBlock`` so every collapsible inline
 * card in a message uses the same frame + height + toggle position.
 * Header script glyph: 𝓣  Thinking.
 */
import { useState } from "react";
import { useTranslation } from "@/lib/i18n";
import { renderMarkdown, useMarkdownReady } from "./markdown";

export function ThinkingBlock({
  text,
  streaming,
}: {
  text: string;
  streaming?: boolean;
}) {
  // Default expanded so the user sees thinking content stream in
  // live. Click the header to collapse manually. Without this the
  // block looks "still" while content actually fills underneath —
  // users couldn't tell streaming from a single end-of-reply dump.
  const [collapsed, setCollapsed] = useState(false);
  const [copied, setCopied] = useState(false);
  const { text: tr } = useTranslation();
  useMarkdownReady();
  if (!text) return null;

  function copy(e: React.MouseEvent) {
    e.stopPropagation();
    const done = () => {
      setCopied(true);
      setTimeout(() => setCopied(false), 1200);
    };
    if (navigator.clipboard?.writeText) {
      navigator.clipboard.writeText(text).then(done, done);
    } else { done(); }
  }
  return (
    <div className="inline-tree" data-collapsed={collapsed ? "1" : "0"}>
      <div
        className="inline-tree-header"
        onClick={() => setCollapsed((c) => !c)}
      >
        <span>
          <span className="inline-tree-script" title="thinking">{"𝓣"}</span>
          {"\u00a0\u00a0"}
          {tr("Thinking", "思考")}
          {streaming ? "…" : ""}
        </span>
        <span className="inline-tree-actions">
          <button
            className={"inline-tree-copy" + (copied ? " copied" : "")}
            title={tr("Copy thinking text", "复制思考内容")}
            onClick={copy}
          >
            {copied ? tr("Copied", "已复制") : tr("Copy", "复制")}
          </button>
          <span
            className={"inline-tree-toggle" + (collapsed ? " collapsed" : "")}
          >
            {"▶"}
          </span>
        </span>
      </div>
      <div className={"inline-tree-body" + (collapsed ? " collapsed" : "")}>
        <div className="thinking-text" dangerouslySetInnerHTML={{ __html: renderMarkdown(text.trim()) }} />
      </div>
    </div>
  );
}
