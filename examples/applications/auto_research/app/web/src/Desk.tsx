// The page of a task, or of the whole project: what it says on top, its files in the middle,
// the conversation's last exchange under them and the composer at the bottom.
import { createContext, useContext, useMemo, useState, type ReactNode } from "react";
import { SquareArrowOutUpRight } from "lucide-react";
import { Button } from "@/components/ui/button";
import { HoverTip } from "@/components/ui/tooltip";
import { cn } from "@/lib/utils";
import { useApp } from "./App";
import { ArtifactPanel, useArtifacts } from "./Artifacts";
import { Messages, useConversation } from "./Conversation";
import type { ChatFeed } from "./api";
import type { Artifact } from "./model";

/** A short, stable name for a folder inside a conversation key. */
function folderKey(path: string) {
  let h = 0x811c9dc5;
  for (let i = 0; i < path.length; i++) { h ^= path.charCodeAt(i); h = Math.imul(h, 0x01000193); }
  return (h >>> 0).toString(16);
}

/** The files this conversation's agent wrote or edited, as it named them. */
function editedBy(feed: ChatFeed | null): string[] {
  const paths = new Set<string>();
  for (const m of feed?.messages || []) for (const t of m.tools) if (/write|edit|patch/i.test(t.tool) && t.input) paths.add(t.input);
  return [...paths];
}
const sameFile = (edited: string, path: string) => edited === path || edited.endsWith("/" + path);

// A task page shows what the task delivers first; the project page what changed.
const ORDER: Record<string, Artifact["group"][]> = {
  task: ["deliverables", "changed", "outputs", "paper"],
  project: ["changed", "paper", "deliverables", "outputs"],
};

/** The marker a proposal ends with; the page answers it with one button. */
export const GO_AHEAD = "就这样改";

/** The open conversation's send, for buttons of the page's header. */
const SendContext = createContext<(text: string) => void>(() => {});
export const useSend = () => useContext(SendContext);
/** Opens a project file in the page's viewer. */
export const ShowContext = createContext<(path: string) => void>(() => {});

export function Desk({ scope, phase, header, notice, body, note, hint, onReplyEnded }: {
  /** The task's name, or "" for the project. */
  scope: string; phase?: string;
  header: ReactNode; notice?: ReactNode; note: string;
  /** The empty composer's placeholder. */
  hint: string;
  /** What the page shows in place of the files, above the conversation. */
  body?: ReactNode;
  /** Called when a reply of this conversation ends. */
  onReplyEnded?: () => void;
}) {
  const { view, changes, touched } = useApp();
  const project = view?.project || "";
  const key = `${scope || "project"}:${folderKey(project)}`;
  const all = useArtifacts(scope, changes);
  const chat = useConversation(key, project, note, hint, () => { touched(); onReplyEnded?.(); });
  const edited = editedBy(chat.feed).join("\n");

  // The project page shows every uncommitted change; a task page shows its own files
  // and, among the changes, only the ones its own conversation made.
  const items = useMemo(() => {
    const mine = edited ? edited.split("\n") : [];
    const order = ORDER[scope ? "task" : "project"];
    // The paper belongs to the project page and to the tasks that write or publish it.
    const paper = !scope || phase === "writing" || phase === "release";
    return all.filter((a) => (paper || a.group !== "paper") && (!scope || a.group !== "changed" || mine.some((e) => sameFile(e, a.path))))
      .sort((a, b) => order.indexOf(a.group) - order.indexOf(b.group));
  }, [all, scope, phase, edited]);
  const messages = chat.feed?.messages || [];
  const last = messages[messages.length - 1];
  // The agent proposed a change and waits: one button says go.
  const proposal = !chat.feed?.running && last?.role === "assistant" && last.text.includes(GO_AHEAD);
  const send = (text: string) => { chat.send(text).catch(console.error); };
  // With a conversation under way the files keep the top part and the talk gets the rest.
  const talking = messages.length > 0;
  // A file the page asks to see (a result's output or report): the viewer turns to it.
  const [want, setWant] = useState({ path: "", n: 0 });
  const show = (path: string) => setWant((w) => ({ path, n: w.n + 1 }));

  return (
    <SendContext.Provider value={send}>
    <ShowContext.Provider value={show}>
    <div className="flex h-full flex-col">
      <div className="mx-auto flex w-full max-w-[1180px] min-h-0 flex-1 flex-col gap-4 px-8 pt-5">
        <div className="flex shrink-0 items-start gap-4">
          <div className="min-w-0 flex-1">{header}</div>
          {chat.feed?.messages.length ? (
            <HoverTip label="在聊天页打开这段对话，看完整过程">
              <Button variant="ghost" size="icon-sm" aria-label="在聊天页打开" onClick={chat.reveal}><SquareArrowOutUpRight /></Button>
            </HoverTip>
          ) : null}
        </div>
        {notice && !body && items.length ? (
          <div className={cn("-mx-2 flex min-h-0 flex-col gap-4 overflow-y-auto px-2 pb-2", talking ? "h-[45%] shrink-0" : "flex-1")}>
            {notice}
            <div className="flex h-[440px] shrink-0 flex-col"><ArtifactPanel scope={key} items={items} want={want} /></div>
          </div>
        ) : notice ? <div className="-mx-2 max-h-[45%] shrink-0 overflow-y-auto px-2">{notice}</div> : null}
        {notice && !body && items.length ? null : body ? <div className={cn("-mx-2 min-h-0 overflow-y-auto px-2 pb-2", talking ? "max-h-[45%] shrink-0" : "flex-1")}>{body}</div>
          : items.length ? <div className={cn("flex min-h-0 flex-col", talking ? "h-[40%] shrink-0" : "flex-1")}><ArtifactPanel scope={key} items={items} want={want} /></div> : null}
        <div className={cn("overflow-y-auto", talking || !(body || items.length) ? "min-h-0 flex-1" : "max-h-[34%] shrink-0")}>
          <Messages feed={chat.feed} />
          {proposal ? (
            <div className="mt-2 flex gap-2">
              <Button size="sm" onClick={() => send(GO_AHEAD)}>{GO_AHEAD}</Button>
            </div>
          ) : null}
        </div>
      </div>
      <div className="mx-auto w-full max-w-[1180px] shrink-0 px-2">
        <div ref={chat.slot} style={{ height: chat.composerHeight }} />
      </div>
    </div>
    </ShowContext.Provider>
    </SendContext.Provider>
  );
}
