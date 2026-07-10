"use client";

/**
 * Collapsible "Thinking" block — same ``.inline-tree`` visual shell
 * as ``RuntimeBlock`` / ``ToolsBlock`` so every collapsible inline
 * card in a message uses the same frame + height + toggle position.
 * Header icon: shared step-icons ThinkingIcon.
 */
import { useEffect, useRef, useState } from "react";
import { useTranslation } from "@/lib/i18n";
import { renderMarkdown, useMarkdownReady } from "./markdown";
import { BrainIcon } from "@/components/animated-icons";

export function ThinkingBlock({
  text,
  streaming,
}: {
  text: string;
  streaming?: boolean;
}) {
  // Expanded WHILE streaming so the user sees thinking fill in live, then
  // auto-collapse once it's done (thinking is verbose; a finished reply
  // shouldn't be buried under it). The user can still toggle manually — once
  // they do, we stop auto-collapsing so we don't fight their choice.
  const [collapsed, setCollapsed] = useState(!streaming);
  const [copied, setCopied] = useState(false);
  const userToggled = useRef(false);
  const { text: tr } = useTranslation();
  useMarkdownReady();

  // Auto-collapse when streaming ends (unless the user took control).
  useEffect(() => {
    if (!streaming && !userToggled.current) setCollapsed(true);
  }, [streaming]);

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
    <div className="inline-tree is-thinking" data-collapsed={collapsed ? "1" : "0"}>
      <div
        className="inline-tree-header"
        onClick={() => { userToggled.current = true; setCollapsed((c) => !c); }}
      >
        <span>
          <span className="inline-tree-icon" title="thinking"><BrainIcon size={15} /></span>
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
