"use client";

import { useEffect, useRef, useState } from "react";
import { useSearchParams } from "next/navigation";
import {
  Send, Square, Pause, Play, Zap, ChevronDown, Activity,
  FileText, Paperclip, Globe, X,
} from "lucide-react";
import {
  useSessionStore,
  useMessageIds,
} from "@/lib/session-store";
import { useWS } from "@/lib/ws";
import { api } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { ContextTreePanel } from "./context-tree-panel";
import { CanvasPanel } from "./canvas-panel";
import { ModelBadge } from "./model-badge";
import { StatusDot } from "./status-dot";
import { ContextBadge } from "./context-badge";
import { MessageBubble } from "./message-bubble";

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

