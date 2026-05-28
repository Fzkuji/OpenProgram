"use client";

/**
 * SpawnedFromCard — rendered above a sub-branch's first user msg.
 *
 * Mirror of the main-lane ``AttachCard``: where AttachCard says
 * "this turn spawned the listfn branch", SpawnedFromCard says
 * "this branch was spawned from <main turn>". Clicking Switch
 * checks out the caller turn so the user can jump back to the
 * branch that started this one.
 */
import type { ChatMsg } from "@/lib/session-store";

import { useSessionStore } from "@/lib/session-store";
import { useTranslation } from "@/lib/i18n";

function wsSend(payload: unknown): void {
  const w = window as unknown as { ws?: WebSocket };
  if (w.ws && w.ws.readyState === WebSocket.OPEN) {
    w.ws.send(JSON.stringify(payload));
  }
}

export function SpawnedFromCard({ msg }: { msg: ChatMsg }) {
  const sessionId = useSessionStore((s) => s.currentSessionId);
  const { text } = useTranslation();
  const sf = msg.spawnedFrom;
  if (!sf || !sf.callerId) return null;

  const label = sf.label || sf.callerId.slice(0, 8);

  function switchBack() {
    if (!sessionId || !sf || !sf.callerId) return;
    wsSend({
      action: "checkout_branch",
      session_id: sessionId,
      head_msg_id: sf.callerId,
    });
    wsSend({ action: "load_session", session_id: sessionId });
  }

  return (
    <div className="attach-card" data-spawned-from={sf.callerId}>
      <div className="attach-card-header">
        <div className="attach-card-icon" aria-hidden="true">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor"
               strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <polyline points="20 7 14 13 20 19" />
            <line x1="4" y1="5" x2="12" y2="5" />
            <line x1="4" y1="19" x2="12" y2="19" />
          </svg>
        </div>
        <div className="attach-card-meta">
          <div className="attach-card-label">
            {text("Spawned from:", "创建自：")} <span className="attach-card-source">{label}</span>
          </div>
          <div className="attach-card-sub">
            {text("this branch was started by a task() call on another turn", "这个分支由另一个回合中的 task() 调用创建")}
          </div>
        </div>
        <button
          type="button"
          className="attach-card-open"
          onClick={switchBack}
          aria-label={text("Switch back to the calling turn", "切回调用它的回合")}
          title={text("Switch back to the calling turn", "切回调用它的回合")}
        >
          {text("Switch", "切换")}
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor"
               strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"
               aria-hidden="true">
            <line x1="7" y1="17" x2="17" y2="7" />
            <polyline points="7 7 17 7 17 17" />
          </svg>
        </button>
      </div>
    </div>
  );
}
