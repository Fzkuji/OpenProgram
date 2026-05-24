"use client";

/**
 * AttachCard — rendered for assistant rows whose ``function === "attach"``.
 *
 * An attach row marks that this turn referenced another agent's branch
 * (via a spawn / attach operation). The two agents are peers — neither
 * is parent or child of the other — so the label is just "agent",
 * not "sub-agent". The "Open" link navigates to that peer's own chat.
 */
import type { ChatMsg } from "@/lib/session-store";

import { renderMarkdown, useMarkdownReady } from "./markdown";

export function AttachCard({ msg }: { msg: ChatMsg }) {
  useMarkdownReady();
  const attach = msg.attach || {};
  const subId = attach.session_id || "";
  const label = (attach.label || "").trim();
  const preview = msg.content || "(no output)";
  const headTag = attach.head_id ? attach.head_id.slice(0, 8) : "";

  function open() {
    if (!subId) return;
    const nav = (window as unknown as { __navigate?: (p: string) => void })
      .__navigate;
    if (nav) nav("/s/" + subId);
    else window.location.href = "/s/" + subId;
  }

  return (
    <div className="attach-card" data-peer-session-id={subId}>
      <div className="attach-card-header">
        <div className="attach-card-icon" aria-hidden="true">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor"
               strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <polyline points="4 17 10 11 4 5" />
            <line x1="12" y1="19" x2="20" y2="19" />
          </svg>
        </div>
        <div className="attach-card-meta">
          <div className="attach-card-label">
            spawn{label ? ` · ${label}` : ""}
          </div>
          <div className="attach-card-sub" title={subId}>
            {subId || "(no session id)"}
            {headTag ? <span className="attach-card-head">@{headTag}</span> : null}
          </div>
        </div>
        {subId ? (
          <button
            type="button"
            className="attach-card-open"
            onClick={open}
            aria-label="Open peer agent session"
          >
            Open
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor"
                 strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"
                 aria-hidden="true">
              <line x1="7" y1="17" x2="17" y2="7" />
              <polyline points="7 7 17 7 17 17" />
            </svg>
          </button>
        ) : null}
      </div>
      <div
        className="attach-card-preview chat-text"
        dangerouslySetInnerHTML={{ __html: renderMarkdown(preview) }}
      />
    </div>
  );
}
