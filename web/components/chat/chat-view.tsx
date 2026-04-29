"use client";

import { useEffect, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { useSearchParams } from "next/navigation";
import { useRouter } from "next/navigation";
import {
  Send, Square, Pause, Play, Loader2, Zap, ChevronDown, Activity,
  Copy, RefreshCw, GitBranch, Check, FileText,
} from "lucide-react";
import {
  useConvStore,
  useMessageById,
  useMessageIds,
  type ChatMsg,
} from "@/lib/conv-store";
import { useWS } from "@/lib/ws";
import { api } from "@/lib/api";
import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import { ContextTreePanel } from "./context-tree-panel";
import { CanvasPanel } from "./canvas-panel";

interface ChatViewProps {
  convId: string | null;
}

const THINKING_OPTIONS = ["off", "minimal", "low", "medium", "high", "xhigh"] as const;
type Effort = (typeof THINKING_OPTIONS)[number];

export function ChatView({ convId }: ChatViewProps) {
  const { send } = useWS();
  const wsStatus = useConvStore((s) => s.wsStatus);
  const messageIds = useMessageIds(convId);
  const runningTask = useConvStore((s) => s.runningTask);
  const paused = useConvStore((s) => s.paused);
  const providerInfo = useConvStore((s) => s.providerInfo);
  const currentConvId = useConvStore((s) => s.currentConvId);
  const setCurrentConv = useConvStore((s) => s.setCurrentConv);
  const appendMessage = useConvStore((s) => s.appendMessage);

  const searchParams = useSearchParams();
  const [input, setInput] = useState("");
  const [thinking, setThinking] = useState<Effort>("medium");
  const [thinkingOpen, setThinkingOpen] = useState(false);
  const [treeOpen, setTreeOpen] = useState(false);
  const [canvasOpen, setCanvasOpen] = useState(false);
  const tree = useConvStore((s) =>
    convId ? s.trees[convId] ?? null : null
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
    if (convId && wsStatus === "open") {
      if (currentConvId !== convId) setCurrentConv(convId);
      send({ action: "load_conversation", conv_id: convId });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [convId, wsStatus]);

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
  const isRunning = busy && runningTask?.conv_id === (convId ?? currentConvId);

  function submit() {
    const text = input.trim();
    if (!text || busy || wsStatus !== "open") return;
    const localId = "u-" + Math.random().toString(36).slice(2, 10);
    // Optimistically append user message
    const targetConv = convId ?? currentConvId;
    if (targetConv) {
      appendMessage(targetConv, {
        id: localId,
        role: "user",
        content: text,
        status: "done",
      });
    }
    send({
      action: "chat",
      text,
      conv_id: convId ?? currentConvId ?? null,
      thinking_effort: thinking,
    });
    setInput("");
  }

  function stop() {
    const id = runningTask?.conv_id ?? convId ?? currentConvId;
    if (!id) return;
    api.stop(id).catch(() => {});
  }

  function togglePause() {
    const id = runningTask?.conv_id ?? convId ?? currentConvId;
    if (!id) return;
    (paused ? api.resume(id) : api.pause(id)).catch(() => {});
  }

  return (
    <div className="flex h-screen">
      <div className="flex flex-1 flex-col">
      <header
        className="flex h-12 shrink-0 items-center justify-between border-b px-4"
        style={{ borderColor: "var(--border-color)" }}
      >
        <div className="flex items-center gap-2">
          <ModelBadge />
          <ContextBadge convId={convId} />
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
            <MessageBubble key={id} msgId={id} convId={currentConvId} />
          ))}
        </div>
      </div>

      <footer
        className="shrink-0 border-t p-4"
        style={{ borderColor: "var(--border-color)" }}
      >
        <div className="mx-auto max-w-3xl">
          <div
            className="flex items-end gap-2 rounded-lg border p-2"
            style={{
              background: "var(--bg-secondary)",
              borderColor: "var(--border-color)",
            }}
          >
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
                    borderColor: "var(--border-color)",
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
                    borderColor: "var(--border-color)",
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
                disabled={!input.trim() || wsStatus !== "open"}
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
  const providerInfo = useConvStore((s) => s.providerInfo);
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
      await api.switchModel(provider, model);
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
            borderColor: "var(--border-color)",
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
 * Subscribes to ``tokens[convId]`` + ``contextWindow[convId]`` so when
 * the user switches branches the badge flips to that branch's own
 * usage. Server tags every ``context_stats`` event with conv_id, so the
 * store stays partitioned cleanly. Hidden when no usage yet.
 */
function ContextBadge({ convId }: { convId: string | null }) {
  const tokens = useConvStore(
    (s) => (convId ? s.tokens[convId] : undefined),
  );
  const window = useConvStore(
    (s) => (convId ? s.contextWindow[convId] : undefined),
  );
  if (!tokens || !tokens.input) return null;
  const fmt = (n: number) =>
    n >= 10000 ? `${(n / 1000).toFixed(1)}k` : `${n}`;
  const pct = window ? Math.round((tokens.input / window) * 100) : null;
  const color =
    pct === null
      ? "var(--text-muted)"
      : pct > 85
      ? "var(--accent-red)"
      : pct > 65
      ? "var(--accent-yellow)"
      : "var(--text-muted)";
  return (
    <span
      className="inline-flex items-center gap-1 rounded px-2 py-0.5 text-[10px]"
      style={{ background: "var(--bg-tertiary)", color }}
      title={
        window
          ? `${tokens.input.toLocaleString()} / ${window.toLocaleString()} tokens (${pct}%)`
          : `${tokens.input.toLocaleString()} tokens`
      }
    >
      {fmt(tokens.input)}
      {window ? `/${fmt(window)} (${pct}%)` : ""}
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

function MessageBubble({ msgId, convId }: { msgId: string; convId: string | null }) {
  // Subscribe to this one message entry. When a streaming delta lands
  // on a *different* msgId, React.memo + this selector keep us from
  // re-rendering. Only the bubble owning the updated id re-renders.
  const msg = useMessageById(msgId);
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
    convId !== null &&
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
          border: isUser ? "none" : "1px solid var(--border-color)",
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
      {actionable && <MessageActions msg={msg} convId={convId!} />}
    </div>
  );
}

function MessageActions({ msg, convId }: { msg: ChatMsg; convId: string }) {
  const router = useRouter();
  const truncateFrom = useConvStore((s) => s.truncateFrom);
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
      truncateFrom(convId, msg.id);
      await api.retryChat(convId, msg.id);
    } catch (e) {
      console.error("retry failed", e);
    } finally {
      setBusy(null);
    }
  };

  const onBranch = async () => {
    setBusy("branch");
    try {
      const r = await api.branchChat(convId, msg.id);
      router.push(`/c/${r.conv_id}`);
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
        borderColor: "var(--border-color)",
      }}
    >
      <button
        onClick={() => setExpanded((v) => !v)}
        className="flex w-full items-center gap-2 border-b px-3 py-2 text-left"
        style={{
          background: "var(--bg-tertiary)",
          borderColor: "var(--border-color)",
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
