/**
 * The page of a standalone application: what the App's application tab is
 * for its window (components/center-tabs/pages/application-tab-pane.tsx).
 *
 * It signs in with the token the launcher put after `#`, frames the
 * application's window (sandboxed, as in the App), answers the window's
 * bridge (`openprogramApp`) from this server's routes, records the
 * microphone for `audio.start` with the App's recorder, and shows a
 * conversation the backend opened (`conversation.show`) in a panel with a
 * follow-up composer. The look is the App's default theme in the
 * computer's light or dark mode; the language is the browser's.
 */
import { useCallback, useEffect, useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import { ArrowUp, X } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { Markdown } from "@/lib/format-utils/markdown";
import { useAudioRecorder } from "@/lib/applications/audio-recorder";
import { DEFAULT_THEME_STYLE, resolveThemePreference } from "@/lib/prefs/appearance/theme-config";
import { useTranslation } from "@/lib/i18n";
import { applicationRequest } from "@/lib/net/applications";

interface Described {
  instance_id: string;
  ui_url: string;
  application: { id: string; title: string; display_title?: string; digest: string; capabilities: string[] };
}

interface Conversation {
  id: string;
  title: string;
  messages: { role: "user" | "assistant"; text: string; at: number }[];
  replying?: boolean;
  error?: string;
}

const dark = window.matchMedia("(prefers-color-scheme: dark)");

/** The App's default look, in the computer's mode, on this page's <html>. */
function applyLook(): void {
  const root = document.documentElement;
  const { theme, resolvedMode } = resolveThemePreference(DEFAULT_THEME_STYLE, "auto", dark.matches);
  root.setAttribute("data-theme", theme);
  root.setAttribute("data-theme-style", DEFAULT_THEME_STYLE);
  root.setAttribute("data-theme-mode", resolvedMode);
  root.lang = navigator.language.toLowerCase().startsWith("zh") ? "zh" : "en";
}

/** What the window applies to its own <html>, as the App's tab sends it. */
function appearance() {
  const root = document.documentElement;
  const attributes: Record<string, string> = {};
  for (const name of root.getAttributeNames()) {
    if (name.startsWith("data-") || name === "lang") attributes[name] = root.getAttribute(name) ?? "";
  }
  return { attributes, properties: {} as Record<string, string> };
}

/** The token after `#` becomes the session cookie; the address keeps no secret. */
async function signIn(): Promise<void> {
  const token = new URLSearchParams(location.hash.slice(1)).get("token");
  if (!token) return;
  history.replaceState(null, "", location.pathname);
  await applicationRequest("/api/standalone/sign-in", "POST", { token });
}

function Shell() {
  const { text } = useTranslation();
  const [described, setDescribed] = useState<Described | null>(null);
  const [signedOut, setSignedOut] = useState(false);
  const [shown, setShown] = useState<string | null>(null);
  const channel = useRef<MessageChannel | null>(null);

  useEffect(() => {
    let disposed = false;
    let retry: ReturnType<typeof setTimeout> | undefined;
    const read = (attempt: number) => {
      applicationRequest<Described>("/api/standalone").then((value) => {
        if (disposed) return;
        setDescribed(value);
        document.title = value.application.display_title || value.application.title;
      }).catch((reason: unknown) => {
        if (disposed) return;
        if (reason instanceof Error && /sign in/.test(reason.message)) { setSignedOut(true); return; }
        retry = setTimeout(() => read(attempt + 1), Math.min(10_000, 500 * 2 ** attempt));
      });
    };
    signIn().catch(() => {}).finally(() => read(0));
    return () => { disposed = true; clearTimeout(retry); };
  }, []);

  const post = useCallback((event: string, value: unknown) => {
    channel.current?.port1.postMessage({ event, value });
  }, []);

  // What the window hears without asking: its state was saved, the recording changed.
  useEffect(() => {
    if (!described) return;
    const events = new EventSource("/api/standalone/events");
    events.addEventListener("state", () => post("state", null));
    events.addEventListener("audio", () => post("audio", null));
    return () => events.close();
  }, [described, post]);
  const instanceId = described?.instance_id ?? "";
  const microphone = useAudioRecorder((state) => state.microphones[instanceId]);
  useEffect(() => { post("audio", null); }, [microphone, post]);

  // The computer's light or dark mode, followed while the page is open.
  useEffect(() => {
    const follow = () => { applyLook(); post("appearance", appearance()); };
    dark.addEventListener("change", follow);
    return () => dark.removeEventListener("change", follow);
  }, [post]);

  function connect(frame: HTMLIFrameElement) {
    if (!described || !frame.contentWindow) return;
    channel.current?.port1.close();
    channel.current?.port2.close();
    const connection = new MessageChannel();
    channel.current = connection;
    const { application } = described;
    const base = `/api/application-instances/${encodeURIComponent(described.instance_id)}`;
    const audioAllowed = () => {
      if (!application.capabilities.includes("audio.record")) throw new Error("application does not declare audio.record");
    };
    connection.port1.onmessage = async ({ data }) => {
      if (!data || typeof data.id !== "string" || data.id.length > 128) return;
      const { id, method } = data;
      const params = data.params ?? {};
      try {
        let result: unknown;
        if (method === "state.load") result = await applicationRequest(`${base}/state`);
        else if (method === "state.save") result = await applicationRequest(`${base}/state`, "PUT", { value: params.value, version: params.version });
        else if (method === "runs.list") result = (await applicationRequest<{ runs: unknown[] }>(base)).runs;
        else if (method === "operation.run") {
          if (typeof params.operation !== "string") throw new Error("An operation name is required");
          result = await applicationRequest(`${base}/operations/${encodeURIComponent(params.operation)}`, "POST", {
            digest: application.digest, input: params.input, request_key: params.request_key,
          });
        } else if (["run.status", "run.cancel", "run.answer"].includes(method)) {
          if (typeof params.id !== "string" || !/^app_[a-f0-9]{32}$/.test(params.id)) throw new Error("Invalid run identifier");
          const runUrl = `/api/application-runs/${params.id}`;
          const run = await applicationRequest<{ instance_id: string }>(runUrl);
          if (run.instance_id !== described.instance_id) throw new Error("This run belongs to another application instance");
          if (method === "run.status") {
            const after = Number.isSafeInteger(params.after) && params.after >= 0 ? params.after : 0;
            result = await applicationRequest(`${runUrl}?after=${after}`);
          } else result = await applicationRequest(`${runUrl}/${method === "run.cancel" ? "cancel" : "answer"}`, "POST", {
            request_id: params.request_id, answer: params.answer,
          });
        } else if (method === "audio.start") {
          audioAllowed();
          if (typeof params.key !== "string") throw new Error("A recording key is required");
          result = await useAudioRecorder.getState().start(described.instance_id, {
            key: params.key,
            tracks: Array.isArray(params.tracks) ? params.tracks.filter((track: unknown) => track === "mic" || track === "system") : undefined,
            max_seconds: Number.isFinite(params.max_seconds) ? params.max_seconds : undefined,
          });
        } else if (method === "audio.stop") {
          audioAllowed();
          result = await useAudioRecorder.getState().stop(described.instance_id);
        } else if (method === "audio.status") {
          audioAllowed();
          result = await useAudioRecorder.getState().status(described.instance_id);
        } else if (method === "conversation.show") {
          if (typeof params.session_id !== "string" || !params.session_id) throw new Error("A conversation is required");
          await applicationRequest(`${base}/conversations/${encodeURIComponent(params.session_id)}/show`, "POST", {});
          setShown(params.session_id);
          result = true;
        } else if (typeof method === "string" && method.startsWith("chat.")) {
          throw new Error("chat is not available in a standalone application");
        } else throw new Error("Unsupported application request");
        connection.port1.postMessage({ id, result });
      } catch (reason) {
        connection.port1.postMessage({ id, error: reason instanceof Error ? reason.message : String(reason) });
      }
    };
    frame.contentWindow.postMessage({ type: "openprogram.application.connect" }, "*", [connection.port2]);
    connection.port1.postMessage({ event: "appearance", value: appearance() });
  }

  if (signedOut) {
    return (
      <div className="flex h-full items-center justify-center p-6 text-center text-fs-base text-muted-foreground">
        {text("Open this application from its own icon.", "请从应用自己的图标打开它。")}
      </div>
    );
  }
  if (!described) return null;
  return (
    <div className="relative h-full w-full">
      <iframe
        title={described.application.display_title || described.application.title}
        src={described.ui_url}
        sandbox="allow-scripts"
        referrerPolicy="no-referrer"
        className="h-full w-full border-0"
        onLoad={(event) => { setShown(null); connect(event.currentTarget); }}
      />
      {shown ? <ConversationPanel sessionId={shown} onClose={() => setShown(null)} /> : null}
    </div>
  );
}

/** A conversation the backend opened: its messages and a follow-up question. */
function ConversationPanel({ sessionId, onClose }: { sessionId: string; onClose: () => void }) {
  const { text } = useTranslation();
  const [conversation, setConversation] = useState<Conversation | null>(null);
  const [draft, setDraft] = useState("");
  const [sending, setSending] = useState(false);
  const [failure, setFailure] = useState("");
  const end = useRef<HTMLDivElement | null>(null);
  const url = `/api/standalone/conversations/${encodeURIComponent(sessionId)}`;

  useEffect(() => {
    let disposed = false;
    const read = () => applicationRequest<Conversation>(url).then((value) => { if (!disposed) setConversation(value); }).catch(() => {});
    void read();
    const events = new EventSource("/api/standalone/events");
    events.addEventListener("conversation", (event) => {
      if (JSON.parse((event as MessageEvent).data) === sessionId) void read();
    });
    return () => { disposed = true; events.close(); };
  }, [sessionId, url]);
  useEffect(() => { end.current?.scrollIntoView({ block: "end" }); }, [conversation?.messages.length, conversation?.replying]);
  useEffect(() => {
    const close = (event: KeyboardEvent) => { if (event.key === "Escape") onClose(); };
    window.addEventListener("keydown", close);
    return () => window.removeEventListener("keydown", close);
  }, [onClose]);

  async function send() {
    const message = draft.trim();
    if (!message || sending) return;
    setSending(true);
    setFailure("");
    try {
      const answer = await applicationRequest<{ error?: string }>(`${url}/messages`, "POST", { text: message });
      setDraft("");
      if (answer.error) setFailure(answer.error);
    } catch (reason) {
      setFailure(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setSending(false);
    }
  }

  const busy = sending || Boolean(conversation?.replying);
  return (
    <div className="absolute inset-0 z-10 flex justify-end bg-black/20" onMouseDown={(event) => { if (event.target === event.currentTarget) onClose(); }}>
      <aside className="flex h-full w-full max-w-[560px] flex-col border-l border-border bg-background text-foreground shadow-raised">
        <header className="flex items-center gap-2 border-b border-border px-4 py-3">
          <h2 className="min-w-0 flex-1 truncate text-fs-lg font-semibold">{conversation?.title ?? ""}</h2>
          <Button variant="ghost" size="icon-sm" aria-label={text("Close", "关闭")} onClick={onClose}><X /></Button>
        </header>
        <div className="min-h-0 flex-1 overflow-y-auto px-4 py-4">
          <div className="flex flex-col gap-4">
            {conversation?.messages.map((message, index) => (
              // The App's message bubbles (styles/chat/messages/bubbles.css).
              <div key={index} className={`message ${message.role}`}>
                <div className="message-content">
                  {message.role === "user" ? message.text : <Markdown source={message.text} escapeRawHtml />}
                </div>
              </div>
            ))}
            {conversation?.replying ? <div className="text-fs-sm text-muted-foreground">{text("Answering…", "正在回答…")}</div> : null}
            {failure || conversation?.error ? <div className="text-fs-sm text-destructive">{failure || conversation?.error}</div> : null}
            <div ref={end} />
          </div>
        </div>
        <form className="flex items-end gap-2 border-t border-border p-3" onSubmit={(event) => { event.preventDefault(); void send(); }}>
          <Textarea
            value={draft}
            rows={1}
            className="max-h-40 min-h-9 flex-1 resize-none"
            placeholder={text("Ask a follow-up question", "继续提问")}
            onChange={(event) => setDraft(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) { event.preventDefault(); void send(); }
            }}
          />
          <Button type="submit" size="icon" aria-label={text("Send", "发送")} disabled={busy || !draft.trim()}><ArrowUp /></Button>
        </form>
      </aside>
    </div>
  );
}

applyLook();
createRoot(document.getElementById("root")!).render(<Shell />);
