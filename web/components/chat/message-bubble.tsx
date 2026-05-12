"use client";

/**
 * Chat message bubble — user / assistant / system / runtime variants,
 * plus per-row copy / retry / branch actions and an inline token badge.
 */
import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { useRouter } from "next/navigation";
import { Loader2, Copy, RefreshCw, GitBranch, Check } from "lucide-react";
import {
  useSessionStore,
  useMessageById,
  useMessageIds,
  type ChatMsg,
} from "@/lib/session-store";
import { api } from "@/lib/api";
import { cn } from "@/lib/utils";
import { RuntimeBlock } from "./runtime-block";
import { type BranchTokenStats, fmtTokens } from "./tokens";

export function MessageBubble({ msgId, sessionId }: { msgId: string; sessionId: string | null }) {
  // Subscribe to this one message entry. When a streaming delta lands
  // on a *different* msgId, React.memo + this selector keep us from
  // re-rendering. Only the bubble owning the updated id re-renders.
  const msg = useMessageById(msgId);
  // Token row for this specific message — same queryKey as ContextBadge,
  // so React Query serves both from one fetch.
  const messageIds = useMessageIds(sessionId);
  const { data: tokenStats } = useQuery<BranchTokenStats | null>({
    queryKey: ["session-tokens", sessionId, messageIds.length],
    enabled: !!sessionId,
    queryFn: async () => {
      if (!sessionId) return null;
      const r = await fetch(
        `/api/sessions/${encodeURIComponent(sessionId)}/tokens`,
      );
      if (!r.ok) return null;
      return r.json();
    },
    staleTime: 2_000,
  });
  const myTokens = tokenStats?.branch.find((b) => b.message_id === msgId);
  if (!msg) return null;

  const isUser = msg.role === "user";
  const isSystem = msg.role === "system";
  const isRuntime = msg.display === "runtime";

  if (isRuntime) {
    return <RuntimeBlock msg={msg} />;
  }

  // Actions show on any non-system message that has final content
  // (no point retrying a message that's still streaming).
  const actionable =
    !isSystem &&
    sessionId !== null &&
    msg.status !== "streaming" &&
    msg.status !== "pending";

  return (
    <div
      className={cn(
        "group/msg flex flex-col gap-1",
        isUser ? "items-end" : "items-start",
      )}
    >
      <div
        className={cn(
          "max-w-[85%] whitespace-pre-wrap rounded-lg px-3 py-2 text-[13px]",
          isUser && "text-white"
        )}
        style={{
          background: isUser
            ? "var(--user-msg-bg)"
            : msg.status === "error"
              ? "rgba(229, 83, 75, 0.15)"
              : "var(--assistant-msg-bg)",
          color: isUser
            ? "var(--text-bright)"
            : msg.status === "error"
              ? "var(--accent-red)"
              : "var(--text-primary)",
          border: isUser ? "none" : "1px solid var(--border)",
          opacity: isSystem ? 0.7 : 1,
        }}
      >
        {isSystem && msg.status === "pending" && (
          <Loader2 className="mr-2 inline h-3 w-3 animate-spin align-text-bottom" />
        )}
        {msg.content || (msg.status === "streaming" ? "…" : "")}
        {msg.status === "cancelled" && (
          <span
            className="ml-2 text-[10px]"
            style={{ color: "var(--accent-yellow)" }}
          >
            (cancelled)
          </span>
        )}
      </div>
      <div
        className={cn(
          "flex items-center gap-2",
          isUser ? "flex-row-reverse" : "flex-row",
        )}
      >
        {actionable && <MessageActions msg={msg} sessionId={sessionId!} />}
        {myTokens && (myTokens.total > 0 || myTokens.output_tokens > 0) && (
          <MessageTokenBadge stats={myTokens} />
        )}
      </div>
    </div>
  );
}

function MessageTokenBadge({
  stats,
}: {
  stats: BranchTokenStats["branch"][number];
}) {
  // Per-message badge: total tokens for this row + cache slice if any.
  // Source tag exposed via tooltip — 'heuristic' rows are estimates so
  // the UI flags them with a subtle question mark.
  const isEstimate = stats.token_source === "heuristic";
  const parts: string[] = [];
  if (stats.input_tokens) parts.push(`in ${fmtTokens(stats.input_tokens)}`);
  if (stats.output_tokens)
    parts.push(`out ${fmtTokens(stats.output_tokens)}`);
  if (stats.cache_read_tokens)
    parts.push(`cache ${fmtTokens(stats.cache_read_tokens)}`);
  if (stats.cache_write_tokens)
    parts.push(`cw ${fmtTokens(stats.cache_write_tokens)}`);
  const tooltip = [
    `Tokens: ${parts.join(" · ") || "—"}`,
    `Source: ${stats.token_source}${isEstimate ? " (estimated)" : ""}`,
    stats.token_model ? `Model: ${stats.token_model}` : null,
  ]
    .filter(Boolean)
    .join("\n");
  return (
    <span
      className="text-[10px] opacity-0 transition-opacity duration-100 group-hover/msg:opacity-100"
      style={{ color: "var(--text-muted)" }}
      title={tooltip}
    >
      {fmtTokens(stats.total)}
      {isEstimate ? "?" : ""}
    </span>
  );
}

function MessageActions({ msg, sessionId }: { msg: ChatMsg; sessionId: string }) {
  const router = useRouter();
  const truncateFrom = useSessionStore((s) => s.truncateFrom);
  const [copied, setCopied] = useState(false);
  const [busy, setBusy] = useState<null | "retry" | "branch">(null);

  const onCopy = async () => {
    try {
      await navigator.clipboard.writeText(msg.content || "");
      setCopied(true);
      setTimeout(() => setCopied(false), 1200);
    } catch {
      // Clipboard API can fail on non-secure origins; fall back to
      // a temporary textarea so the user still gets copy on localhost.
      const ta = document.createElement("textarea");
      ta.value = msg.content || "";
      document.body.appendChild(ta);
      ta.select();
      try { document.execCommand("copy"); } finally { ta.remove(); }
      setCopied(true);
      setTimeout(() => setCopied(false), 1200);
    }
  };

  const onRetry = async () => {
    setBusy("retry");
    try {
      // Optimistically drop this message + anything after it. Server
      // is about to stream in a fresh reply; the truncate keeps the
      // UI honest until the new WS frames land.
      truncateFrom(sessionId, msg.id);
      await api.retryChat(sessionId, msg.id);
    } catch (e) {
      console.error("retry failed", e);
    } finally {
      setBusy(null);
    }
  };

  const onBranch = async () => {
    setBusy("branch");
    try {
      const r = await api.branchChat(sessionId, msg.id);
      router.push(`/s/${r.session_id}`);
    } catch (e) {
      console.error("branch failed", e);
    } finally {
      setBusy(null);
    }
  };

  return (
    <div
      className={cn(
        "flex items-center gap-0.5 text-[11px] opacity-0",
        "transition-opacity duration-150 group-hover/msg:opacity-100",
      )}
      style={{ color: "var(--text-muted)" }}
    >
      <button
        onClick={onCopy}
        title="Copy"
        className="rounded p-1 hover:bg-[var(--bg-hover)]"
      >
        {copied ? <Check className="h-3.5 w-3.5" /> : <Copy className="h-3.5 w-3.5" />}
      </button>
      <button
        onClick={onRetry}
        disabled={busy !== null}
        title="Retry from this message"
        className="rounded p-1 hover:bg-[var(--bg-hover)] disabled:opacity-40"
      >
        <RefreshCw className={cn("h-3.5 w-3.5", busy === "retry" && "animate-spin")} />
      </button>
      <button
        onClick={onBranch}
        disabled={busy !== null}
        title="Branch into a new conversation"
        className="rounded p-1 hover:bg-[var(--bg-hover)] disabled:opacity-40"
      >
        <GitBranch className="h-3.5 w-3.5" />
      </button>
    </div>
  );
}
