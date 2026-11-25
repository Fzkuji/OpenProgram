// The conversation of one task, or of the whole project: the host draws the composer in the
// place this page leaves for it; the page draws the messages, the last exchange first.
import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { ChevronUp, LoaderCircle } from "lucide-react";
import { cn } from "@/lib/utils";
import type { ChatFeed, ChatMessage } from "./api";
import { markdown } from "./text";

export interface Conversation {
  feed: ChatFeed | null;
  /** The composer's height, which the page keeps free at its bottom. */
  composerHeight: number;
  slot: React.RefObject<HTMLDivElement | null>;
  reveal(): void;
  /** Send a message as the author's own, after a reply that is running. */
  send(text: string): Promise<void>;
}

/** Opens the conversation kept under `key`, working in `folder`, keeps telling
 *  it what the next message is about (`note`) and puts `hint` in the empty composer. */
export function useConversation(key: string, folder: string, note: string, hint: string, onReplyEnded: () => void): Conversation {
  const app = window.openprogramApp;
  const [feed, setFeed] = useState<ChatFeed | null>(null);
  const [composerHeight, setComposerHeight] = useState(132);
  const [sessionId, setSessionId] = useState("");
  const slot = useRef<HTMLDivElement | null>(null);
  const ended = useRef(onReplyEnded);
  ended.current = onReplyEnded;

  useEffect(() => {
    let alive = true;
    setFeed(null);
    setSessionId("");
    app.chat.open(key, folder).then((opened) => { if (alive) setSessionId(opened.session_id); }).catch(console.error);
    return () => { alive = false; };
  }, [app, key, folder]);

  useEffect(() => {
    if (!sessionId) return;
    let running = false;
    const stopChat = app.on("chat", (value) => {
      if (value.session_id !== sessionId) return;
      setFeed(value);
      if (running && !value.running) ended.current();
      running = value.running;
    });
    const stopComposer = app.on("composer", (value) => setComposerHeight(value.height));
    return () => { stopChat(); stopComposer(); };
  }, [app, sessionId]);

  useEffect(() => {
    if (sessionId) app.chat.note(note).catch(console.error);
  }, [app, sessionId, note]);

  useEffect(() => {
    if (sessionId) app.chat.hint(hint).catch(console.error);
  }, [app, sessionId, hint]);

  // The composer goes where the slot is: across its width, its bottom on the slot's bottom.
  useLayoutEffect(() => {
    const element = slot.current;
    if (!element || !sessionId) return;
    let last = "";
    const place = () => {
      const r = element.getBoundingClientRect();
      const rect = { left: Math.round(r.left), right: Math.round(r.right), bottom: Math.round(r.bottom) };
      const id = JSON.stringify(rect);
      if (id !== last) { last = id; app.chat.place(rect).catch(console.error); }
    };
    place();
    const observer = new ResizeObserver(place);
    observer.observe(element);
    observer.observe(document.body);
    window.addEventListener("resize", place);
    return () => { observer.disconnect(); window.removeEventListener("resize", place); };
  }, [app, sessionId]);

  useEffect(() => () => { app.chat.place(null).catch(() => {}); }, [app]);

  return {
    feed, composerHeight, slot, reveal: () => { app.chat.reveal().catch(console.error); },
    send: async (text) => { if (sessionId) await app.chat.send(text); },
  };
}

const TOOL_WORDS: Record<string, string> = {
  read: "读", read_file: "读", view: "读", grep: "搜索", glob: "找文件", ls: "看目录", list: "看目录",
  write: "写", write_file: "写", edit: "改", edit_file: "改", apply_patch: "改", multi_edit: "改",
  bash: "运行", shell: "运行", exec: "运行", web_search: "搜索网页", web_fetch: "读网页",
};
const toolWord = (tool: string) => TOOL_WORDS[tool.toLowerCase()] || tool;

/** The messages of the last exchange: the author's last message and the replies after it. */
function lastExchange(messages: ChatMessage[]): number {
  for (let i = messages.length - 1; i >= 0; i--) if (messages[i].role === "user") return i;
  return 0;
}

function Steps({ message, running }: { message: ChatMessage; running: boolean }) {
  if (!message.tools.length) return null;
  if (!running) {
    const edited = [...new Set(message.tools.filter((t) => /write|edit|patch/i.test(t.tool)).map((t) => t.input))].filter(Boolean);
    return (
      <div className="text-xs text-text-muted">
        用了 {message.tools.length} 步{edited.length ? <>，改了 {edited.slice(0, 4).map((p, i) => <code key={i} className="mx-0.5 font-mono">{p.split("/").pop()}</code>)}{edited.length > 4 ? ` 等 ${edited.length} 个文件` : ""}</> : null}
      </div>
    );
  }
  return (
    <ol className="flex flex-col gap-0.5 text-xs text-text-muted">
      {message.tools.slice(-3).map((t, i, shown) => (
        <li key={i} className="flex min-w-0 items-center gap-1.5">
          {t.status === "running" || (i === shown.length - 1 && running)
            ? <LoaderCircle className="size-3 shrink-0 animate-spin text-[var(--accent-blue)]" />
            : <span className="size-3 shrink-0 text-center leading-3">·</span>}
          <span className="shrink-0">{toolWord(t.tool)}</span>
          <span className="truncate font-mono">{t.input}</span>
        </li>
      ))}
    </ol>
  );
}

export function Messages({ feed }: { feed: ChatFeed | null }) {
  const [all, setAll] = useState(false);
  const end = useRef<HTMLDivElement>(null);
  const messages = feed?.messages || [];
  const from = all ? 0 : lastExchange(messages);
  const shown = messages.slice(from);
  const lastText = shown.length ? shown[shown.length - 1].text.length + shown[shown.length - 1].tools.length : 0;
  useEffect(() => { end.current?.scrollIntoView({ block: "end" }); }, [shown.length, lastText, all]);

  if (!feed) return null;
  if (!messages.length) return null;
  return (
    <div className="flex flex-col gap-3">
      {from > 0 ? (
        <button type="button" onClick={() => setAll(true)} className="flex items-center gap-1 self-center text-xs text-text-muted hover:text-text-bright">
          <ChevronUp className="size-3.5" />更早的 {from} 条
        </button>
      ) : null}
      {shown.map((m, i) => {
        const live = feed.running && i === shown.length - 1 && m.role === "assistant";
        return m.role === "user" ? (
          <div key={m.id} className="flex justify-end">
            <div className="max-w-[78%] whitespace-pre-wrap break-words rounded-2xl bg-[var(--bg-tertiary)] px-3.5 py-2 text-sm text-text-bright">{m.text}</div>
          </div>
        ) : (
          <div key={m.id} className="flex max-w-[92%] flex-col gap-1.5">
            {m.text ? <div className={cn("md", live && "opacity-95")} dangerouslySetInnerHTML={{ __html: markdown(m.text) }} /> : null}
            <Steps message={m} running={live} />
          </div>
        );
      })}
      {feed.running && shown[shown.length - 1]?.role === "user"
        ? <LoaderCircle className="size-4 animate-spin text-text-muted" aria-label="正在回复" /> : null}
      <div ref={end} />
    </div>
  );
}
