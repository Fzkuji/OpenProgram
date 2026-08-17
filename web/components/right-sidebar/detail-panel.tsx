"use client";

import type { ReactNode } from "react";
import { useSessionStore } from "@/lib/session-store";
import { useTranslation } from "@/lib/i18n";
import { renderMarkdown, useMarkdownReady } from "../chat/messages/markdown";

export const VIEW_DETAIL = "detail";
export const VIEW_CONTEXT = "context";

export function SessionViewSwitch({ current }: { current: string }) {
  const { text } = useTranslation();
  const selected = useSessionStore((state) => state.nodeSelected);
  const setRightDockView = useSessionStore((state) => state.setRightDockView);
  if (!selected) return null;

  const options = [
    { view: VIEW_DETAIL, label: text("Details", "详情") },
    { view: VIEW_CONTEXT, label: text("Context", "上下文") },
  ];
  return (
    <div
      className="session-view-switch"
      role="tablist"
      aria-label={text("Selected node views", "选中节点的视图")}
    >
      {options.map((option) => (
        <button
          key={option.view}
          type="button"
          role="tab"
          aria-selected={current === option.view}
          className={
            "session-view-switch-btn" +
            (current === option.view ? " is-active" : "")
          }
          onClick={() => setRightDockView(option.view)}
        >
          {option.label}
        </button>
      ))}
    </div>
  );
}

function looksLikeMarkdown(value: string): boolean {
  return /(^|\n)\s*(#{1,6}\s|[-*+]\s|\d+\.\s|>\s|\|)|```|\[[^\]]+\]\([^)]+\)|\*\*[^*]+\*\*/.test(
    value,
  );
}

function DetailBlock({
  title,
  value,
  danger,
}: {
  title: string;
  value: unknown;
  danger?: boolean;
}) {
  useMarkdownReady();
  if (value === undefined || value === null) return null;

  let body: ReactNode;
  if (typeof value !== "string") {
    body = <div className="detail-code">{JSON.stringify(value, null, 2)}</div>;
  } else {
    const trimmed = value.trim();
    let pretty: string | null = null;
    if (/^[[{]/.test(trimmed)) {
      try {
        pretty = JSON.stringify(JSON.parse(trimmed), null, 2);
      } catch {
        pretty = null;
      }
    }
    if (pretty !== null) {
      body = <div className="detail-code">{pretty}</div>;
    } else if (!danger && looksLikeMarkdown(value)) {
      body = (
        <div
          className="detail-code chat-text"
          style={{ whiteSpace: "normal", fontFamily: "inherit" }}
          dangerouslySetInnerHTML={{ __html: renderMarkdown(value) }}
        />
      );
    } else {
      body = (
        <div
          className="detail-code"
          style={danger ? { color: "var(--accent-red)" } : undefined}
        >
          {value}
        </div>
      );
    }
  }

  return (
    <div className="detail-section">
      <div className="detail-section-title">{title}</div>
      {body}
    </div>
  );
}

export function DetailPanel() {
  const { t } = useTranslation();
  const node = useSessionStore((state) => state.detailNode);

  if (!node) {
    return (
      <div id="detailBody" className="detail-body">
        <div className="detail-empty">
          {t("right.no_execution")}
          <br />
          <span>{t("right.no_execution_hint")}</span>
        </div>
      </div>
    );
  }

  const statusIcon =
    node.status === "success" ? "✓" : node.status === "error" ? "✗" : "●";
  const duration =
    node.duration_ms && node.duration_ms > 0
      ? `${Math.round(node.duration_ms)}ms`
      : "running...";
  const filteredParams = node.params
    ? Object.fromEntries(
        Object.entries(node.params).filter(
          ([key]) => key !== "runtime" && key !== "callback",
        ),
      )
    : null;

  return (
    <div id="detailBody" className="detail-body">
      <div className="detail-section">
        <div className="detail-section-title">Status</div>
        <div className={`detail-badge ${node.status}`}>
          {statusIcon} {node.status} &middot; {duration}
        </div>
      </div>

      <div className="detail-section">
        <div className="detail-section-title">Path</div>
        <div className="detail-field-value">{node.path}</div>
      </div>

      <DetailBlock title="Prompt / Docstring" value={node.prompt || null} />
      <DetailBlock
        title="Parameters"
        value={
          filteredParams && Object.keys(filteredParams).length > 0
            ? filteredParams
            : null
        }
      />
      <DetailBlock title="Output" value={node.output ?? null} />
      <DetailBlock title="Error" value={node.error || null} danger />

      {node.node_type === "exec" ? (
        <>
          <DetailBlock
            title="LLM Input"
            value={
              node.params?._content != null
                ? String(node.params._content)
                : null
            }
          />
          <DetailBlock title="LLM Reply" value={node.raw_reply ?? null} />
        </>
      ) : (
        <DetailBlock title="Raw LLM Reply" value={node.raw_reply ?? null} />
      )}

      <DetailBlock
        title={`Attempts (${node.attempts?.length || 0})`}
        value={node.attempts && node.attempts.length > 0 ? node.attempts : null}
      />

      <div className="detail-section">
        <div className="detail-section-title">Render / Compress</div>
        <div className="detail-field-value">
          render: {node.render || "summary"} | compress:{" "}
          {node.compress ? "true" : "false"}
        </div>
      </div>
    </div>
  );
}
