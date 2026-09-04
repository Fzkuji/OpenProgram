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
import { jsonFetch } from "@/lib/net/fetch-client";
import type { SelfUpdate } from "@/lib/self-update";
import { SelfUpdateCard } from "@/components/chat/messages/self-update-card";

type RunningItem = {
  kind: "tool" | "job" | "process" | "run" | "self_update";
  update?: SelfUpdate;
  id: string;
  session_id?: string | null;
  execution_id?: string | null;
  label: string;
  status: string;
  started_at: number | null;
  pid?: number;
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
  const [stale, setStale] = useState(false);
  const [updateError, setUpdateError] = useState(false);
  const updateSyncedAt = useRef<number | null>(null);
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
    let request: AbortController | null = null;
    let pollTimer: ReturnType<typeof setTimeout>;
    async function poll() {
      if (stop || request) return;
      clearTimeout(pollTimer);
      const controller = new AbortController();
      request = controller;
      const timeout = setTimeout(() => controller.abort(), 15000);
      try {
        const data = await jsonFetch<{
          items: RunningItem[];
          now: number;
          self_update_error?: string | null;
        }>("/api/running", { signal: controller.signal, cache: "no-store" });
        if (stop || controller.signal.aborted) return;
        baseRef.current = { serverNow: data.now, fetchedAt: Date.now() };
        // A partial projection failure must not silently remove the last update.
        setItems((previous) => data.self_update_error
          ? [...data.items.filter((item) => item.kind !== "self_update"), ...previous.filter((item) => item.kind === "self_update")]
          : data.items);
        setUpdateError(Boolean(data.self_update_error));
        if (!data.self_update_error) updateSyncedAt.current = Date.now();
        setStale(false);
        setLoaded(true);
      } catch {
        if (!stop) setStale(true);
      } finally {
        clearTimeout(timeout);
        request = null;
        if (!stop) pollTimer = setTimeout(poll, POLL_MS);
      }
    }
    poll();
    window.addEventListener("online", poll);
    const tickTimer = setInterval(() => setTick((t) => t + 1), 1000);
    return () => {
      stop = true;
      request?.abort();
      clearTimeout(pollTimer);
      window.removeEventListener("online", poll);
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
    kind === "tool"
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
    if (item.kind === "self_update" && item.update) {
      return <SelfUpdateCard key={`self_update:${item.id}`} update={item.update} />;
    }
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

  if (!loaded && !stale) {
    return (
      <div style={{ padding: 16, fontSize: 13, color: "var(--text-dim)" }}>
        {text("Loading…", "加载中…")}
      </div>
    );
  }
  if (items.length === 0 && !stale && !updateError) {
    return (
      <div style={{ padding: 16, fontSize: 13, color: "var(--text-dim)" }}>
        {text("Nothing is running right now", "当前没有正在运行的任务")}
      </div>
    );
  }

  return (
    <div style={{ overflowY: "auto", padding: "4px 8px" }}>
      {(stale || updateError) && <p role="status" style={{ padding: 8, fontSize: 12 }}>
        {text("Status unavailable. Displayed results may be stale; reconnecting automatically.", "状态不可用。显示的结果可能已过时；正在自动重连。")}
        {(updateError ? updateSyncedAt.current : baseRef.current.fetchedAt) ? <> {text("Last sync", "最近同步")}: {new Date(updateError ? updateSyncedAt.current! : baseRef.current.fetchedAt).toLocaleString()}</> : null}
      </p>}
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
