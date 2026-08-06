"use client";

/**
 * "You are on a sub-agent's branch" banner.
 *
 * Shown at the top of the transcript whenever the ACTIVE branch's root
 * is a spawn root (`source=agent_spawn`) — i.e. the user took over a
 * sub-agent's conversation (attach-card Switch, DAG agent badge). The
 * attach card that jumped here lives on the MAIN branch's transcript,
 * so once you're on the agent branch there is no card to click — this
 * banner is the way back: one click checks out the tip of the branch
 * the agent was spawned from.
 */

import { useEffect, useState } from "react";

import { useSessionStore } from "@/lib/session-store";
import { useTranslation } from "@/lib/i18n";
import { getSocket, runtimeState } from "@/lib/runtime-bridge/state";

function wsSend(payload: unknown): void {
  const sock = getSocket();
  if (sock && sock.readyState === WebSocket.OPEN) {
    sock.send(JSON.stringify(payload));
  }
}

interface GraphRow {
  id: string;
  role?: string;
  display?: string;
  function?: string;
  source?: string;
  predecessor?: string | null;
  caller?: string | null;
  created_at?: number;
  covers_ids?: unknown;
  spawned_from?: { label?: string | null };
  branch_name?: string;
}

interface BannerInfo {
  name: string;
  backTarget: string;
}

/** A row that continues a conversation chain (not execution machinery). */
function isConvRow(n: GraphRow): boolean {
  if (n.display === "root" || n.display === "runtime") return false;
  if (n.function) return false;
  if (Array.isArray(n.covers_ids)) return false;
  return n.role === "user" || n.role === "assistant";
}

function computeBanner(sessionId: string | null): BannerInfo | null {
  if (!sessionId) return null;
  const conv = runtimeState.conversations[sessionId] as
    | { graph?: GraphRow[]; head_id?: string }
    | undefined;
  const graph = conv?.graph;
  if (!Array.isArray(graph) || !graph.length) return null;
  const rows = (runtimeState._branchesByConv[sessionId] as
    | { head_msg_id?: string; name?: string; active?: boolean }[]
    | undefined) || [];
  const head = rows.find((b) => b.active)?.head_msg_id || conv?.head_id || "";
  if (!head) return null;
  const byId: Record<string, GraphRow> = Object.create(null);
  graph.forEach((n) => { byId[n.id] = n; });
  // Walk to the active branch's root.
  let cur = byId[head];
  const seen = new Set<string>();
  while (cur && cur.predecessor && byId[cur.predecessor]
      && !seen.has(cur.id)) {
    seen.add(cur.id);
    cur = byId[cur.predecessor];
  }
  if (!cur || cur.source !== "agent_spawn" || !cur.caller) return null;
  // The way back: from the spawning turn, follow the newest
  // conversation child down to that branch's tip.
  let back = byId[cur.caller];
  if (!back) return null;
  const hop = new Set<string>();
  for (;;) {
    hop.add(back.id);
    let next: GraphRow | null = null;
    for (const n of graph) {
      if (n.predecessor !== back.id || !isConvRow(n) || hop.has(n.id)) continue;
      if (!next || (n.created_at || 0) > (next.created_at || 0)) next = n;
    }
    if (!next) break;
    back = next;
  }
  const name =
    rows.find((b) => b.head_msg_id === head)?.name?.trim()
    || (cur.spawned_from?.label || "").trim()
    || cur.branch_name?.trim()
    || cur.id.slice(0, 8);
  return { name, backTarget: back.id };
}

export function AgentBranchBanner() {
  const { text } = useTranslation();
  const sessionId = useSessionStore((s) => s.currentSessionId);
  const [info, setInfo] = useState<BannerInfo | null>(null);
  useEffect(() => {
    const refresh = () => setInfo(computeBanner(sessionId));
    refresh();
    window.addEventListener("branches-updated", refresh);
    return () => window.removeEventListener("branches-updated", refresh);
  }, [sessionId]);
  if (!info || !sessionId) return null;
  const goBack = () => {
    wsSend({
      action: "checkout_branch",
      session_id: sessionId,
      head_msg_id: info.backTarget,
    });
    wsSend({ action: "load_session", session_id: sessionId });
  };
  return (
    <div className="agent-branch-banner">
      <span className="agent-branch-banner-label">
        {text("Viewing sub-agent branch", "正在查看子 agent 分支")}
        {" · "}
        {info.name}
      </span>
      <button type="button" className="agent-branch-banner-back"
        onClick={goBack}>
        {text("Back to main conversation", "返回主对话")}
      </button>
    </div>
  );
}
