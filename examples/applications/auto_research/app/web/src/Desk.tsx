// The page of a task, or of the whole project: what it says on top, its files in the middle,
// the conversation's last exchange under them and the composer at the bottom.
import { useMemo, type ReactNode } from "react";
import { SquareArrowOutUpRight } from "lucide-react";
import { Button } from "@/components/ui/button";
import { HoverTip } from "@/components/ui/tooltip";
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

const ORDER: Record<string, Artifact["group"][]> = {
  writing: ["changed", "paper", "deliverables", "outputs"],
  other: ["changed", "deliverables", "outputs", "paper"],
};

export function Desk({ scope, phase, header, notice, note, empty }: {
  /** The task's name, or "" for the project. */
  scope: string; phase?: string;
  header: ReactNode; notice?: ReactNode; note: string; empty: string;
}) {
  const { view, changes, touched } = useApp();
  const project = view?.project || "";
  const key = `${scope || "project"}:${folderKey(project)}`;
  const all = useArtifacts(scope, changes);
  const chat = useConversation(key, project, note, touched);
  const edited = editedBy(chat.feed).join("\n");

  // The project page shows every uncommitted change; a task page shows its own files
  // and, among the changes, only the ones its own conversation made.
  const items = useMemo(() => {
    const mine = edited ? edited.split("\n") : [];
    const order = ORDER[phase === "writing" ? "writing" : "other"];
    // The paper belongs to the project page and to the tasks that write or publish it.
    const paper = !scope || phase === "writing" || phase === "release";
    return all.filter((a) => (paper || a.group !== "paper") && (!scope || a.group !== "changed" || mine.some((e) => sameFile(e, a.path))))
      .sort((a, b) => order.indexOf(a.group) - order.indexOf(b.group));
  }, [all, scope, phase, edited]);

  return (
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
        {notice}
        <ArtifactPanel scope={key} items={items} />
        <div className={items.length ? "max-h-[34%] shrink-0 overflow-y-auto" : "min-h-0 flex-1 overflow-y-auto"}>
          <Messages feed={chat.feed} empty={empty} />
        </div>
      </div>
      <div className="mx-auto w-full max-w-[1180px] shrink-0 px-2">
        <div ref={chat.slot} style={{ height: chat.composerHeight }} />
      </div>
    </div>
  );
}
