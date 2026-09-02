"use client";

/**
 * Running panel — session-grouped global view of current executions for the
 * right sidebar. Polls GET /api/running, groups items by session, identifies
 * parallel branches by execution ID, and keeps unscoped processes under Other.
 * Session headers open the corresponding conversation tab.
 */
import { useEffect, useRef, useState } from "react";

import { useTranslation } from "@/lib/i18n";
import { useSessionStore } from "@/lib/session-store";
import { useCenterTabs } from "@/lib/state/center-tabs-store";

type RunningItem = {
  kind: "execution" | "tool" | "job" | "process" | "run";
  id: string;
  session_id?: string | null;
  execution_id?: string | null;
  label: string;
  status: string;
  started_at: number | null;
  pid?: number;
  capabilities?: { pause?: boolean; step?: boolean };
  event_cursor?: { next_sequence?: number };
  snapshot?: Record<string, unknown>;
};

const POLL_MS = 3000;

function formatElapsed(seconds: number): string {
  const s = Math.max(0, Math.floor(seconds));
  if (s < 60) return `${s}s`;
  if (s < 3600) return `${Math.floor(s / 60)}m ${s % 60}s`;
  return `${Math.floor(s / 3600)}h ${Math.floor((s % 3600) / 60)}m`;
}

export function RunningPanel({ active }: { active: boolean }) {
  const { text } = useTranslation();
  const conversations = useSessionStore((s) => s.conversations);
  const [items, setItems] = useState<RunningItem[]>([]);
  const [loaded, setLoaded] = useState(false);
  // Server clock at fetch time + local clock at fetch time, so elapsed
  // stays correct even when the two clocks disagree.
  const baseRef = useRef<{ serverNow: number; fetchedAt: number }>({
    serverNow: 0,
    fetchedAt: 0,
  });
  const [, setTick] = useState(0);

  useEffect(() => {
    if (!active) return;
    let stop = false;
    async function poll() {
      try {
        const res = await fetch("/api/running");
        if (!res.ok) return;
        const data = (await res.json()) as {
          items: RunningItem[];
          now: number;
        };
        if (stop) return;
        baseRef.current = { serverNow: data.now, fetchedAt: Date.now() };
        setItems(data.items || []);
        setLoaded(true);
      } catch {
        /* worker unreachable — keep last snapshot */
      }
    }
    poll();
    const pollTimer = setInterval(poll, POLL_MS);
    const tickTimer = setInterval(() => setTick((t) => t + 1), 1000);
    return () => {
      stop = true;
      clearInterval(pollTimer);
      clearInterval(tickTimer);
    };
  }, [active]);

  const elapsedOf = (item: RunningItem): string | null => {
    if (!item.started_at) return null;
    const { serverNow, fetchedAt } = baseRef.current;
    const drift = (Date.now() - fetchedAt) / 1000;
    return formatElapsed(serverNow - item.started_at + drift);
  };

  const kindLabel = (kind: RunningItem["kind"]) =>
    kind === "execution"
      ? text("Execution", "执行")
      : kind === "tool"
      ? text("Tool", "工具")
      : kind === "job"
        ? text("Job", "任务")
        : kind === "run"
          ? text("Run", "运行")
          : text("Process", "进程");

  const sessionGroups = new Map<string, RunningItem[]>();
  const otherItems: RunningItem[] = [];
  for (const item of items) {
    if (!item.session_id) {
      otherItems.push(item);
      continue;
    }
    const group = sessionGroups.get(item.session_id);
    if (group) group.push(item);
    else sessionGroups.set(item.session_id, [item]);
  }

  const renderItem = (item: RunningItem) => {
    const elapsed = elapsedOf(item);
    const branch = item.session_id && item.execution_id
      ? `${text("branch", "分支")} ${item.execution_id.slice(-8)}`
      : "";
    const processPid = !item.session_id && item.kind === "process" && item.pid != null
      ? `pid ${item.pid}`
      : "";
    return (
      <div
        key={`${item.kind}:${item.id}`}
        title={item.label}
        style={{
          padding: "8px 10px",
          marginBottom: 4,
          borderRadius: 8,
          border: "1px solid var(--border)",
          fontSize: 12,
        }}
      >
        <div
          style={{
            display: "flex",
            alignItems: "center",
            gap: 6,
            marginBottom: 2,
          }}
        >
          {/* breathing dot — same look as the sidebar running dot */}
          <span
            style={{
              width: 7,
              height: 7,
              borderRadius: "50%",
              background:
                item.status === "cancelling"
                  ? "var(--warning, #e5a50a)"
                  : "var(--accent, #3b82f6)",
              flexShrink: 0,
              animation: "convRunningBreathe 1.6s ease-in-out infinite",
            }}
          />
          <span
            style={{
              color: "var(--text-dim)",
              textTransform: "uppercase",
              fontSize: 10,
              letterSpacing: 0.5,
              flexShrink: 0,
            }}
          >
            {kindLabel(item.kind)}
          </span>
          <span
            style={{
              overflow: "hidden",
              textOverflow: "ellipsis",
              whiteSpace: "nowrap",
              flex: 1,
            }}
          >
            {item.label}
          </span>
          {branch && (
            <span style={{ color: "var(--text-dim)", fontSize: 11, flexShrink: 0 }}>
              {branch}
            </span>
          )}
        </div>
        <div
          style={{
            display: "flex",
            justifyContent: "space-between",
            color: "var(--text-dim)",
            fontSize: 11,
          }}
        >
          <span>{processPid}</span>
          <span style={{ flexShrink: 0, fontVariantNumeric: "tabular-nums" }}>
            {item.status !== "running" && item.status !== "cancelling"
              ? item.status
              : elapsed || ""}
          </span>
        </div>
      </div>
    );
  };

  if (!loaded) {
    return (
      <div style={{ padding: 16, fontSize: 13, color: "var(--text-dim)" }}>
        {text("Loading…", "加载中…")}
      </div>
    );
  }
  if (items.length === 0) {
    return (
      <div style={{ padding: 16, fontSize: 13, color: "var(--text-dim)" }}>
        {text("Nothing is running right now", "当前没有正在运行的任务")}
      </div>
    );
  }

  return (
    <div style={{ overflowY: "auto", padding: "4px 8px" }}>
      {[...sessionGroups.entries()].map(([sessionId, groupItems]) => {
        const title = conversations[sessionId]?.title || sessionId.slice(0, 12);
        return (
          <div key={sessionId} style={{ marginBottom: 8 }}>
            <button
              type="button"
              onClick={() => useCenterTabs.getState().openSessionTab(sessionId, title)}
              title={title}
              style={{
                width: "100%",
                padding: "8px 10px 6px",
                border: 0,
                background: "transparent",
                color: "var(--text)",
                cursor: "pointer",
                font: "inherit",
                fontSize: 12,
                fontWeight: 600,
                overflow: "hidden",
                textAlign: "left",
                textOverflow: "ellipsis",
                whiteSpace: "nowrap",
              }}
            >
              {title}
            </button>
            {groupItems.map(renderItem)}
          </div>
        );
      })}
      {otherItems.length > 0 && (
        <div>
          <div
            style={{
              padding: "8px 10px 6px",
              color: "var(--text)",
              fontSize: 12,
              fontWeight: 600,
            }}
          >
            {text("Other", "其他")}
          </div>
          {otherItems.map(renderItem)}
        </div>
      )}
    </div>
  );
}
