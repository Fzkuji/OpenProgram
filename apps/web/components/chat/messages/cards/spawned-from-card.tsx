"use client";

/**
 * SpawnedFromCard — rendered above a sub-branch's first user msg.
 *
 * Mirror of the main-lane ``AttachCard``: where AttachCard says
 * "this turn spawned the listfn branch", SpawnedFromCard says
 * "this branch was spawned from <main turn>". Clicking Switch
 * checks out the caller turn so the user can jump back to the
 * branch that started this one.
 *
 * On the sub-agent branch the head is on, the AgentBranchBanner already
 * names the agent and leads back; the card would repeat it, so it stays
 * out.
 */
import { useRef } from "react";

import type { ChatMsg } from "@/lib/session-store";

import { selectRow, useSessionStore } from "@/lib/session-store";
import { useTranslation } from "@/lib/i18n";
import { runtimeState } from "@/lib/runtime-bridge/state";
import { sendRuntimeCommand } from "@/lib/runtime-bridge/session/session-load";
import {
  type AnimatedNavIconHandle,
  ArrowUpRightIcon,
} from "@/components/animated-icons";
import { useRowSessionId } from "../list/transcript-view-scope";

/** Commands go out as they are; a `load_session` goes through the managed
 *  session read, since the transcript applies only the reply to a read it
 *  registered. */
function wsSend(payload: unknown): void {
  sendRuntimeCommand(payload);
}

export function SpawnedFromCard({ msg }: { msg: ChatMsg }) {
  const sessionId = useRowSessionId();
  const { text } = useTranslation();
  const switchIconRef = useRef<AnimatedNavIconHandle>(null);
  const sf = msg.spawnedFrom;
  if (!sf || !sf.callerId) return null;
  const onBranch = sessionId && (runtimeState.conversations[sessionId] as
    | { spawned_branch?: { root_id?: string } | null }
    | undefined)?.spawned_branch?.root_id === msg.id;
  if (onBranch) return null;

  const label = sf.label || sf.callerId.slice(0, 8);

  function switchBack() {
    if (!sessionId || !sf || !sf.callerId) return;
    const state = useSessionStore.getState();
    const firstReply = (state.messageOrder[sessionId] || [])
      .map((id) => selectRow(sessionId, id)(state))
      .find((row) => row?.role === "assistant" && row.calledBy === msg.id)?.id;
    if (firstReply) {
      runtimeState._pendingExpandAttach = {
        head: firstReply,
        anchor: sf.callerId,
      };
    }
    // Back to the conversation the agent was spawned from, at its newest
    // turn (the server names it with the load); the reload scrolls to the
    // spawning reply.
    const spawned = (runtimeState.conversations[sessionId] as
      | { spawned_branch?: { root_id?: string; back_target?: string } | null }
      | undefined)?.spawned_branch;
    const target = spawned?.root_id === msg.id && spawned.back_target
      ? spawned.back_target
      : sf.callerId;
    wsSend({
      action: "checkout_branch",
      session_id: sessionId,
      head_msg_id: target,
    });
    wsSend({ action: "load_session", session_id: sessionId });
  }

  return (
    <div className="attach-row" data-spawned-root={msg.id}>
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
            onMouseEnter={() => switchIconRef.current?.startAnimation?.()}
            onMouseLeave={() => switchIconRef.current?.stopAnimation?.()}
            aria-label={text("Switch back to the calling turn", "切回调用它的回合")}
            data-tip={text("Switch back to the calling turn", "切回调用它的回合")}
          >
            {text("Switch", "切换")}
            <ArrowUpRightIcon ref={switchIconRef} size={14} />
          </button>
        </div>
      </div>
    </div>
  );
}
