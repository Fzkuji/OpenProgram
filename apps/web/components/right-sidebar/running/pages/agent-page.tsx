"use client";

/** An agent, or one branch of the conversation: its time chart. */
import { useLayoutEffect, useRef, useState } from "react";
import { ChevronLeft, GitBranch } from "lucide-react";
import { Button } from "@/components/ui/button";
import { useConversationAgentShape } from "@/components/agents/agent-avatar";
import type { ActivityPageProps, ActivityScope } from "./activity-page-types";
import { ActivityPageFrame } from "./activity-page";
import { ActivityActionRow, ActivityPanelReact } from "./activity-page-row";
import { AgentChart } from "../agent-chart/agent-chart";
import { AlwaysOnRow, reportNameForRun } from "./always-on-row";
import { useActivityPageKeys } from "./use-activity-page-keys";
import { AgentGlyph } from "../running-rows";
import { nextWakeOf } from "../running-format";
import pageStyles from "./activity-page.module.css";
import styles from "../agent-chart/agent-chart.module.css";

export function AgentPage({ target, context }: ActivityPageProps<"agent">) {
  const { text } = context;
  // A branch's key is its head, which moves with every reply; the runs it
  // already holds say which branch it has become.
  const followed = useRef<ActivityScope | null>(null);
  const held = followed.current?.members;
  const scope = context.scopes.find(item => item.key === target.scopeKey)
    ?? (held?.size ? context.scopes.find(item => item.members && [...held].every(id => item.members!.has(id))) : undefined)
    ?? context.scopeOf(target.scopeKey);
  followed.current = scope;
  const head = context.scopeHeadText(scope);
  const shape = useConversationAgentShape(context.sessionId);
  const [alwaysOnOpen, setAlwaysOnOpen] = useState(false);
  const root = useRef<HTMLDivElement>(null);
  const box = useRef<HTMLDivElement>(null);
  const back = useRef<HTMLButtonElement>(null);
  useLayoutEffect(() => { back.current?.focus({ preventScroll: true }); }, [alwaysOnOpen]);
  // Back is one step: a column of the chart first, then the overview.
  const stepBack = useRef<(() => boolean) | null>(null);
  const goBack = () => { if (!stepBack.current?.()) context.close(); };
  useActivityPageKeys(root, box, goBack);

  // The always-on setting opens over the chart, and going back returns to it.
  const glyph = <AgentGlyph shape={shape} color={scope.color} small />;
  const overlay = alwaysOnOpen
    ? <ActivityPanelReact.Provider value={{ ...context, close: () => setAlwaysOnOpen(false), pull: undefined }}>
      <ActivityPageFrame kind={glyph} title={text("Always-on", "常驻")} axis={null}><AlwaysOnRow context={context} scope={scope} /></ActivityPageFrame>
    </ActivityPanelReact.Provider>
    : null;

  return <>
    {overlay}
    <div ref={root} className={`${pageStyles.root} ${pageStyles.fade}`} hidden={alwaysOnOpen} data-timeline="0" data-both="0">
      <div className="flex h-9 flex-none items-center justify-between px-2">
        <Button ref={back} type="button" variant="ghost" className={`${pageStyles.bk} ${styles.named}`} onClick={goBack} aria-label={text("Back in Activity", "在运行记录里返回")}>
          <ChevronLeft size={14} strokeWidth={1.8} aria-hidden="true" />{glyph}<span>{head.title}</span>
        </Button>
      </div>
      {scope.head && !scope.active ? <ActivityActionRow icon={GitBranch} label={text("Switch to this branch", "切换到这个分支")} note={text("Shows it in the chat", "在聊天里显示它")} onClick={() => context.switchTo(scope)} /> : null}
      <AgentChart head={scope.head} context={context} box={box} reportName={run => reportNameForRun(run, context)} nextWake={nextWakeOf(context.work, scope.active, context.text)} onOpenNextWake={() => setAlwaysOnOpen(true)} stepBack={stepBack} onPull={context.pull} />
    </div>
  </>;
}
