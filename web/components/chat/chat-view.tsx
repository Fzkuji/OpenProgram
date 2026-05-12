"use client";

import { useEffect, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { useSearchParams } from "next/navigation";
import { useRouter } from "next/navigation";
import {
  Send, Square, Pause, Play, Loader2, Zap, ChevronDown, Activity,
  Copy, RefreshCw, GitBranch, Check, FileText, Paperclip, Globe, X,
} from "lucide-react";
import {
  useSessionStore,
  useMessageById,
  useMessageIds,
  type ChatMsg,
} from "@/lib/session-store";
import { useWS } from "@/lib/ws";
import { api } from "@/lib/api";
import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import { ContextTreePanel } from "./context-tree-panel";
import { CanvasPanel } from "./canvas-panel";

interface ChatViewProps {
  sessionId: string | null;
}

const THINKING_OPTIONS = ["low", "medium", "high", "xhigh"] as const;
type Effort = (typeof THINKING_OPTIONS)[number];

export function ChatView({ sessionId }: ChatViewProps) {
  const { send } = useWS();
  const wsStatus = useSessionStore((s) => s.wsStatus);
  const messageIds = useMessageIds(sessionId);
  const runningTask = useSessionStore((s) => s.runningTask);
  const paused = useSessionStore((s) => s.paused);
  const providerInfo = useSessionStore((s) => s.providerInfo);
  const currentSessionId = useSessionStore((s) => s.currentSessionId);
  const setCurrentConv = useSessionStore((s) => s.setCurrentConv);
  const appendMessage = useSessionStore((s) => s.appendMessage);

  const searchParams = useSearchParams();
  const [input, setInput] = useState("");
  const [thinking, setThinking] = useState<Effort>("medium");
  const [thinkingOpen, setThinkingOpen] = useState(false);
  const [treeOpen, setTreeOpen] = useState(false);
  const [canvasOpen, setCanvasOpen] = useState(false);
  const [attachments, setAttachments] = useState<{ name: string; mediaType: string; data: string }[]>([]);
  const [webSearch, setWebSearch] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const tree = useSessionStore((s) =>
    sessionId ? s.trees[sessionId] ?? null : null
  );

  // Honor /chat?prefill=... or /chat?run=funcname
  useEffect(() => {
    const prefill = searchParams.get("prefill");
    const runName = searchParams.get("run");
    if (prefill) setInput(prefill);
    else if (runName) setInput(`/run ${runName}`);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  const scrollRef = useRef<HTMLDivElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  // Ask server to load this conversation when mounted
  useEffect(() => {
    if (sessionId && wsStatus === "open") {
      if (currentSessionId !== sessionId) setCurrentConv(sessionId);
      // Legacy `providers.js` reads a bare `currentSessionId` global
      // that's only refreshed by ``chat_ack`` from the server. After
      // a Next.js client-side route change to a different session,
      // the legacy global stays pinned at the OLD session id, so the
      // model picker (legacy code) sends ``session_id`` of the
      // previous conv to ``/api/model``. The current conv's model
      // pick gets silently lost. Mirror the React route into the
      // legacy global here.
      try {
        (window as unknown as { currentSessionId?: string | null }).currentSessionId = sessionId;
      } catch {
        /* ignore */
      }
      send({ action: "load_session", session_id: sessionId });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sessionId, wsStatus]);

  // Scroll on id-list change (new message) and, separately, also on
  // streaming content change of the last bubble — handled by the
  // bubble itself emitting a custom event. For now, id list changes
  // are the primary trigger; the auto-sizer fallback below covers the
  // initial load / stream-start case.
  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight });
  }, [messageIds]);

  // Auto-size textarea
  useEffect(() => {
    const t = textareaRef.current;
    if (!t) return;
    t.style.height = "auto";
    t.style.height = Math.min(t.scrollHeight, 200) + "px";
  }, [input]);

  const busy = runningTask !== null;
  const isRunning = busy && runningTask?.session_id === (sessionId ?? currentSessionId);

  async function onPickFiles(files: FileList | null) {
    if (!files || !files.length) return;
    const next: { name: string; mediaType: string; data: string }[] = [];
    for (const f of Array.from(files)) {
      if (!f.type.startsWith("image/")) continue;
      const data = await new Promise<string>((resolve, reject) => {
        const r = new FileReader();
        r.onload = () => {
          const s = String(r.result || "");
          // strip "data:<mt>;base64,"
          const comma = s.indexOf(",");
          resolve(comma >= 0 ? s.slice(comma + 1) : s);
        };
        r.onerror = () => reject(r.error);
        r.readAsDataURL(f);
      });
      next.push({ name: f.name, mediaType: f.type, data });
    }
    if (next.length) setAttachments((prev) => [...prev, ...next]);
  }

  function submit() {
    const trimmed = input.trim();
    if (busy || wsStatus !== "open") return;
    if (!trimmed && attachments.length === 0) return;
    const text = webSearch && trimmed
      ? `[Use the web_search tool] ${trimmed}`
      : trimmed || "(see attachment)";
    const localId = "u-" + Math.random().toString(36).slice(2, 10);
    const targetConv = sessionId ?? currentSessionId;
    if (targetConv) {
      appendMessage(targetConv, {
        id: localId,
        role: "user",
        content: trimmed + (attachments.length ? ` [+${attachments.length} image${attachments.length > 1 ? "s" : ""}]` : ""),
        status: "done",
      });
    }
    send({
      action: "chat",
      text,
      session_id: sessionId ?? currentSessionId ?? null,
      thinking_effort: thinking,
      attachments: attachments.length
        ? attachments.map((a) => ({ type: "image", media_type: a.mediaType, data: a.data }))
        : undefined,
    });
    setInput("");
    setAttachments([]);
  }

  function stop() {
    const id = runningTask?.session_id ?? sessionId ?? currentSessionId;
    if (!id) return;
    api.stop(id).catch(() => {});
  }

  function togglePause() {
    const id = runningTask?.session_id ?? sessionId ?? currentSessionId;
    if (!id) return;
    (paused ? api.resume(id) : api.pause(id)).catch(() => {});
  }

  return (
    <div className="flex h-screen">
      <div className="flex flex-1 flex-col">
      <header
        className="flex h-12 shrink-0 items-center justify-between border-b px-4"
        style={{ borderColor: "var(--border)" }}
      >
        <div className="flex items-center gap-2">
          <ModelBadge />
          <StatusDot status={wsStatus} />
        </div>
        <div className="flex items-center gap-1">
          <button
            onClick={() => setCanvasOpen((v) => !v)}
            title="Toggle Canvas"
            className="flex h-8 items-center gap-1 rounded-md px-2 text-[11px]"
            style={{
              background: canvasOpen ? "var(--bg-tertiary)" : "transparent",
              color: canvasOpen ? "var(--text-bright)" : "var(--text-secondary)",
            }}
          >
            <FileText className="h-3.5 w-3.5" />
            Canvas
          </button>
          <button
            onClick={() => setTreeOpen((v) => !v)}
            title="Toggle Context Tree"
            className="flex h-8 items-center gap-1 rounded-md px-2 text-[11px]"
            style={{
              background: treeOpen ? "var(--bg-tertiary)" : "transparent",
              color: treeOpen ? "var(--text-bright)" : "var(--text-secondary)",
            }}
          >
            <Activity className="h-3.5 w-3.5" />
            Tree
          </button>
        </div>
      </header>

      <div ref={scrollRef} className="flex-1 overflow-y-auto">
        <div className="mx-auto max-w-3xl space-y-4 p-6">
          {messageIds.length === 0 && (
            <div
              className="flex h-[60vh] items-center justify-center text-center text-[13px]"
              style={{ color: "var(--text-muted)" }}
            >
              <div>
                <div className="mb-2 text-[16px]" style={{ color: "var(--text-secondary)" }}>
                  Start a new conversation
                </div>
                Ask anything, or type <code className="mx-1 rounded px-1" style={{ background: "var(--bg-tertiary)" }}>/run function_name</code> to execute a program.
                {providerInfo?.model && (
                  <div className="mt-1 text-[11px]">
                    Using {providerInfo.provider}/{providerInfo.model}
                  </div>
                )}
              </div>
            </div>
          )}
          {messageIds.map((id) => (
            <MessageBubble key={id} msgId={id} sessionId={currentSessionId} />
          ))}
        </div>
      </div>

      <footer
        className="shrink-0 border-t p-4"
        style={{ borderColor: "var(--border)" }}
      >
        <div className="mx-auto max-w-3xl">
          {attachments.length > 0 && (
            <div className="mb-2 flex flex-wrap gap-2">
              {attachments.map((a, i) => (
                <div
                  key={i}
                  className="flex items-center gap-1 rounded border px-2 py-1 text-[11px]"
                  style={{ background: "var(--bg-tertiary)", borderColor: "var(--border)", color: "var(--text-secondary)" }}
                >
                  {/* eslint-disable-next-line @next/next/no-img-element */}
                  <img
                    src={`data:${a.mediaType};base64,${a.data}`}
                    alt={a.name}
                    className="h-6 w-6 rounded object-cover"
                  />
                  <span className="max-w-[160px] truncate">{a.name}</span>
                  <button
                    onClick={() => setAttachments((prev) => prev.filter((_, j) => j !== i))}
                    className="rounded p-0.5 hover:bg-[var(--bg-hover)]"
                    title="Remove"
                  >
                    <X className="h-3 w-3" />
                  </button>
                </div>
              ))}
            </div>
          )}
          <input
            ref={fileInputRef}
            type="file"
            accept="image/*"
            multiple
            className="hidden"
            onChange={(e) => {
              onPickFiles(e.target.files);
              if (e.target) e.target.value = "";
            }}
          />
          <div
            className="flex items-end gap-2 rounded-lg border p-2"
            style={{
              background: "var(--bg-secondary)",
              borderColor: "var(--border)",
            }}
          >
            <button
              onClick={() => fileInputRef.current?.click()}
              className="flex h-8 w-8 items-center justify-center rounded-md"
              style={{ color: "var(--text-secondary)" }}
              onMouseEnter={(e) => (e.currentTarget.style.background = "var(--bg-hover)")}
              onMouseLeave={(e) => (e.currentTarget.style.background = "transparent")}
              title="Attach image"
            >
              <Paperclip className="h-3.5 w-3.5" />
            </button>
            <button
              onClick={() => setWebSearch((v) => !v)}
              className="flex h-8 items-center gap-1 rounded-md px-2 text-[11px]"
              style={{
                background: webSearch ? "var(--bg-tertiary)" : "transparent",
                color: webSearch ? "var(--accent-blue)" : "var(--text-secondary)",
              }}
              onMouseEnter={(e) => { if (!webSearch) e.currentTarget.style.background = "var(--bg-hover)"; }}
              onMouseLeave={(e) => { if (!webSearch) e.currentTarget.style.background = "transparent"; }}
              title="Web search"
            >
              <Globe className="h-3.5 w-3.5" />
              Search
            </button>
            <div className="relative">
              <button
                onClick={() => setThinkingOpen((v) => !v)}
                className="flex h-8 items-center gap-1 rounded-md px-2 text-[11px]"
                style={{ color: "var(--text-secondary)" }}
                onMouseEnter={(e) => (e.currentTarget.style.background = "var(--bg-hover)")}
                onMouseLeave={(e) => (e.currentTarget.style.background = "transparent")}
                title="Thinking effort"
              >
                <Zap className="h-3 w-3" />
                {thinking}
                <ChevronDown className="h-3 w-3" />
              </button>
              {thinkingOpen && (
                <div
                  className="absolute bottom-full left-0 mb-1 overflow-hidden rounded-md border py-1 shadow-lg"
                  style={{
                    background: "var(--bg-tertiary)",
                    borderColor: "var(--border)",
                  }}
                >
                  {THINKING_OPTIONS.map((opt) => (
                    <button
                      key={opt}
                      onClick={() => {
                        setThinking(opt);
                        setThinkingOpen(false);
                      }}
                      className="flex w-full items-center px-3 py-1.5 text-left text-[12px] transition-colors"
                      style={{
                        color:
                          opt === thinking
                            ? "var(--text-bright)"
                            : "var(--text-primary)",
                      }}
                      onMouseEnter={(e) =>
                        (e.currentTarget.style.background = "var(--bg-hover)")
                      }
                      onMouseLeave={(e) =>
                        (e.currentTarget.style.background = "transparent")
                      }
                    >
                      {opt}
                    </button>
                  ))}
                </div>
              )}
            </div>

            <textarea
              ref={textareaRef}
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && !e.shiftKey) {
                  e.preventDefault();
                  submit();
                }
              }}
              rows={1}
              placeholder={wsStatus === "open" ? "Message..." : "Connecting..."}
              disabled={wsStatus !== "open"}
              className="max-h-[200px] min-h-[32px] flex-1 resize-none bg-transparent px-2 py-1 text-[13px] outline-none disabled:opacity-50"
              style={{ color: "var(--text-primary)" }}
            />

            <ContextBadge sessionId={sessionId} />

            {isRunning ? (
              <>
                <Button
                  size="sm"
                  variant="outline"
                  className="h-8"
                  onClick={togglePause}
                  title={paused ? "Resume" : "Pause"}
                  style={{
                    background: "transparent",
                    borderColor: "var(--border)",
                    color: "var(--text-primary)",
                  }}
                >
                  {paused ? <Play className="h-3.5 w-3.5" /> : <Pause className="h-3.5 w-3.5" />}
                </Button>
                <Button
                  size="sm"
                  variant="destructive"
                  className="h-8"
                  onClick={stop}
                  title="Stop"
                  style={{ background: "var(--accent-red)", color: "#fff" }}
                >
                  <Square className="h-3.5 w-3.5" />
                </Button>
              </>
            ) : (
              <Button
                size="sm"
                className="h-8"
                onClick={submit}
                disabled={(!input.trim() && attachments.length === 0) || wsStatus !== "open"}
                style={{ background: "var(--accent-blue)", color: "#fff" }}
              >
                <Send className="h-3.5 w-3.5" />
              </Button>
            )}
          </div>
          <p
            className="mt-2 text-center text-[10px]"
            style={{ color: "var(--text-muted)" }}
          >
            Enter to send · Shift+Enter for new line
          </p>
        </div>
      </footer>
      </div>
      {canvasOpen && (
        <CanvasPanel onClose={() => setCanvasOpen(false)} />
      )}
      {treeOpen && (
        <ContextTreePanel tree={tree} onClose={() => setTreeOpen(false)} />
      )}
    </div>
  );
}

function ModelBadge() {
  const providerInfo = useSessionStore((s) => s.providerInfo);
  const currentSessionId = useSessionStore((s) => s.currentSessionId);
  const { data: enabledModels } = useQuery({
    queryKey: ["models-enabled"],
    queryFn: api.listEnabledModels,
  });
  const [open, setOpen] = useState(false);

  const current = providerInfo
    ? `${providerInfo.provider ?? ""}/${providerInfo.model ?? ""}`
    : "—";

  async function pick(provider: string, model: string) {
    setOpen(false);
    try {
      // Pass session_id so the backend stamps provider_override /
      // model_override on THIS conversation. Without it the call
      // only nudges the global default and the active conv stays
      // bound to its previously-built runtime — that's the bug
      // where picking "Opus" silently still ran Sonnet.
      await api.switchModel(provider, model, currentSessionId || undefined);
    } catch (e) {
      alert("Switch failed: " + String(e));
    }
  }

  const byProvider = (enabledModels ?? []).reduce<Record<string, { id: string; name: string }[]>>(
    (acc, m) => {
      (acc[m.provider] ??= []).push({ id: m.id, name: m.name });
      return acc;
    },
    {}
  );

  return (
    <div className="relative">
      <button
        onClick={() => setOpen((v) => !v)}
        className="flex h-8 items-center gap-1 rounded-md px-2 text-[12px]"
        style={{ color: "var(--text-secondary)" }}
        onMouseEnter={(e) => (e.currentTarget.style.background = "var(--bg-hover)")}
        onMouseLeave={(e) => (e.currentTarget.style.background = "transparent")}
      >
        <span className="font-mono">{current}</span>
        <ChevronDown className="h-3 w-3" />
      </button>
      {open && (
        <div
          className="absolute left-0 top-full z-20 mt-1 max-h-[400px] w-[320px] overflow-y-auto rounded-md border py-1 shadow-lg"
          style={{
            background: "var(--bg-tertiary)",
            borderColor: "var(--border)",
          }}
        >
          {Object.keys(byProvider).length === 0 && (
            <div
              className="px-3 py-2 text-[12px]"
              style={{ color: "var(--text-muted)" }}
            >
              No enabled models. Go to Settings → LLM Providers.
            </div>
          )}
          {Object.entries(byProvider).map(([provider, models]) => (
            <div key={provider}>
              <div
                className="px-3 py-1 text-[10px] uppercase tracking-wide"
                style={{ color: "var(--text-muted)" }}
              >
                {provider}
              </div>
              {models.map((m) => (
                <button
                  key={m.id}
                  onClick={() => pick(provider, m.id)}
                  className="flex w-full items-center gap-2 px-3 py-1.5 text-left text-[12px] transition-colors"
                  style={{ color: "var(--text-primary)" }}
                  onMouseEnter={(e) =>
                    (e.currentTarget.style.background = "var(--bg-hover)")
                  }
                  onMouseLeave={(e) =>
                    (e.currentTarget.style.background = "transparent")
                  }
                >
                  <span className="flex-1 truncate">{m.name}</span>
                  <span
                    className="font-mono text-[10px]"
                    style={{ color: "var(--text-muted)" }}
                  >
                    {m.id}
                  </span>
                </button>
              ))}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

/**
 * Per-conversation token / context-window indicator.
 *
 * Subscribes to ``tokens[sessionId]`` + ``contextWindow[sessionId]`` so when
 * the user switches branches the badge flips to that branch's own
 * usage. Server tags every ``context_stats`` event with session_id, so the
 * store stays partitioned cleanly. Hidden when no usage yet.
 */
interface BranchTokenStats {
  current_tokens: number;
  context_window: number;
  pct_used: number;
  cache_read_total: number;
  cache_hit_rate: number;
  model: string | null;
  source_mix: Record<string, number>;
  naive_sum: number;
  last_assistant_usage: number;
  branch: Array<{
    message_id: string;
    role: string;
    input_tokens: number;
    output_tokens: number;
    cache_read_tokens: number;
    cache_write_tokens: number;
    total: number;
    token_source: string;
    token_model: string | null;
    timestamp: number;
  }>;
}

function fmtTokens(n: number) {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}K`;
  return `${n}`;
}

function ContextBadge({ sessionId }: { sessionId: string | null }) {
  // Re-fetch whenever the message list grows (cheap GET, ~ms). Driven
  // off messageIds.length rather than a timer so idle sessions don't
  // poll. Falls back to streaming-side store data only if the fetch
  // never returns (network glitch).
  const messageIds = useMessageIds(sessionId);
  const { data } = useQuery<BranchTokenStats | null>({
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

  if (!data || (!data.current_tokens && !data.naive_sum)) return null;

  const current = data.current_tokens || data.naive_sum;
  const window = data.context_window;
  const pct = window ? Math.round((current / window) * 100) : null;
  // Opencode/Claude-Code threshold scheme: dim below 65, yellow 65–85,
  // red above 85. Red signals imminent compaction risk.
  const color =
    pct === null
      ? "var(--text-muted)"
      : pct > 85
        ? "var(--accent-red)"
        : pct > 65
          ? "var(--accent-yellow)"
          : "var(--text-muted)";
  const cachePct = Math.round(data.cache_hit_rate * 100);
  const sourceMix = Object.entries(data.source_mix || {})
    .map(([k, v]) => `${k}: ${v}`)
    .join(", ");
  const tooltip = [
    window
      ? `Context: ${current.toLocaleString()} / ${window.toLocaleString()} (${pct}%)`
      : `Context: ${current.toLocaleString()} tokens`,
    data.cache_read_total
      ? `Cache: ${data.cache_read_total.toLocaleString()} read (${cachePct}% hit rate)`
      : null,
    data.model ? `Model: ${data.model}` : null,
    sourceMix ? `Sources: ${sourceMix}` : null,
  ]
    .filter(Boolean)
    .join("\n");

  return (
    <span
      className="inline-flex items-center gap-1.5 rounded px-2 py-0.5 text-[10px]"
      style={{ background: "var(--bg-tertiary)", color }}
      title={tooltip}
    >
      <span>
        {fmtTokens(current)}
        {window ? `/${fmtTokens(window)}` : ""}
        {pct !== null ? ` (${pct}%)` : ""}
      </span>
      {data.cache_read_total > 0 && (
        <span style={{ color: "var(--accent-green)" }}>
          · cache {cachePct}%
        </span>
      )}
    </span>
  );
}

function StatusDot({ status }: { status: "connecting" | "open" | "closed" }) {
  const color =
    status === "open"
      ? "var(--accent-green)"
      : status === "connecting"
        ? "var(--accent-yellow)"
        : "var(--accent-red)";
  return (
    <span
      className="inline-flex items-center gap-1 text-[10px]"
      style={{ color: "var(--text-muted)" }}
      title={status}
    >
      <span className="h-1.5 w-1.5 rounded-full" style={{ background: color }} />
    </span>
  );
}

function MessageBubble({ msgId, sessionId }: { msgId: string; sessionId: string | null }) {
  // Subscribe to this one message entry. When a streaming delta lands
  // on a *different* msgId, React.memo + this selector keep us from
  // re-rendering. Only the bubble owning the updated id re-renders.
  const msg = useMessageById(msgId);
  // Token row for this specific message — same queryKey as ContextBadge,
  // so React Query serves both from one fetch. Re-uses the
  // messageIds.length cache invalidator implicitly via the shared key.
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

  // Runtime block: distinct card-style rendering with function name header
  if (isRuntime) {
    return <RuntimeBlock msg={msg} />;
  }

  // Actions show on any non-system message that has final content
  // (no point retrying a message that's still streaming — and no
  // point copying an empty placeholder).
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

function RuntimeBlock({ msg }: { msg: ChatMsg }) {
  const [expanded, setExpanded] = useState(true);
  const headerColor =
    msg.status === "error"
      ? "var(--accent-red)"
      : msg.status === "cancelled"
        ? "var(--accent-yellow)"
        : msg.status === "done"
          ? "var(--accent-green)"
          : "var(--accent-blue)";
  return (
    <div
      className="mx-auto w-full max-w-[90%] overflow-hidden rounded-lg border"
      style={{
        background: "var(--bg-secondary)",
        borderColor: "var(--border)",
      }}
    >
      <button
        onClick={() => setExpanded((v) => !v)}
        className="flex w-full items-center gap-2 border-b px-3 py-2 text-left"
        style={{
          background: "var(--bg-tertiary)",
          borderColor: "var(--border)",
        }}
      >
        <span
          className="h-2 w-2 shrink-0 rounded-full"
          style={{ background: headerColor }}
        />
        <span
          className="font-mono text-[12px] font-medium"
          style={{ color: "var(--text-bright)" }}
        >
          {msg.function ?? "runtime"}
        </span>
        <span
          className="text-[10px]"
          style={{ color: "var(--text-muted)" }}
        >
          {msg.status === "streaming"
            ? "running..."
            : msg.status === "done"
              ? "✓"
              : msg.status === "error"
                ? "error"
                : msg.status === "cancelled"
                  ? "cancelled"
                  : "pending"}
        </span>
        <span className="ml-auto text-[10px]" style={{ color: "var(--text-muted)" }}>
          {expanded ? "▼" : "▶"}
        </span>
      </button>
      {expanded && (
        <pre
          className="max-h-[400px] overflow-auto whitespace-pre-wrap p-3 font-mono text-[11px]"
          style={{
            background: "var(--bg-input)",
            color: "var(--text-primary)",
          }}
        >
          {msg.content ||
            (msg.status === "streaming"
              ? <Loader2 className="inline h-3 w-3 animate-spin" />
              : "(empty)")}
        </pre>
      )}
    </div>
  );
}
