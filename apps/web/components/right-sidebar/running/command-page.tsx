"use client";

/** A command the agent is running, or one a restart cut short. Once it has
 *  returned the page stays and shows what it printed. */
import { useEffect, useRef, useState } from "react";
import { presentTool } from "@/components/chat/messages/execution/tool-presentation";
import { getActivityRunEvents, type ActivityStepEvent } from "@/lib/net/execution/activity-runs-client";
import type { ActiveToolCall } from "@/lib/net/execution/process-client";
import type { ActivityPageProps } from "./activity-page-types";
import { activityGoneFrame, ActivityPageFrame } from "./activity-page";
import { ActivityDetail, ActivityKeyValue, ActivityNote, ActivityRow } from "./activity-page-row";
import { Glyph } from "./running-rows";
import { callKind, duration, when } from "./running-format";

const RETRY_MS = 3000;
const RETRIES = 10;

/** The call's own step, read once it has returned. The transcript can land a
 *  moment after the call leaves the list, so a miss is tried again. */
function useReturnedStep(sessionId: string, call: ActiveToolCall | null, returned: boolean): ActivityStepEvent | null {
  const [step, setStep] = useState<ActivityStepEvent | null>(null);
  const executionId = call?.execution_id ?? null;
  const toolCallId = call?.tool_call_id ?? null;
  useEffect(() => {
    if (!returned || !executionId || !toolCallId) return;
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout>;
    let tries = 0;
    const read = () => {
      tries += 1;
      getActivityRunEvents(sessionId, executionId, controller.signal)
        .then(result => result.events.find((event): event is ActivityStepEvent => event.kind === "step" && event.tool_call_id === toolCallId) ?? null)
        .catch(() => null)
        .then(found => {
          if (controller.signal.aborted) return;
          if (found) setStep(found);
          else if (tries < RETRIES) timer = setTimeout(read, RETRY_MS);
        });
    };
    read();
    return () => { controller.abort(); clearTimeout(timer); };
  }, [sessionId, executionId, toolCallId, returned]);
  return step;
}

export function CommandPage({ target, context }: ActivityPageProps<"command">) {
  const { text, now, processes } = context;
  const listed = (processes.calls ?? []).find(entry => entry.id === target.callId) ?? null;
  const held = useRef<ActiveToolCall | null>(null);
  if (listed) held.current = listed;
  const call = listed ?? held.current;
  const returned = !listed && call !== null && !processes.stale;
  const step = useReturnedStep(context.sessionId, call, returned);
  if (!call) return activityGoneFrame(text);
  const look = presentTool(call.name, undefined, text);
  const kind = callKind(call, text);
  const running = listed?.status === "running";
  const head = running ? { label: `${kind} · ${text("Running", "正在运行")} · ${duration(now - call.started_at, text)}`, tone: "run" as const }
    : listed ? { label: `${kind} · ${text("Interrupted", "已中断")} · ${when(call.started_at, text)}`, tone: "muted" as const }
      : step?.failed ? { label: `${kind} · ${text("Failed", "失败")}`, tone: "err" as const }
        : { label: `${kind} · ${text("Returned", "已返回")}`, tone: "muted" as const };
  return <ActivityPageFrame kind={<Glyph name={look.icon} tone={look.tone} large />} title={call.description || call.name} status={head.label} tone={head.tone} axis={null}>
    {listed && !running ? <ActivityDetail><ActivityNote>{text("The run was interrupted before this call returned.", "这次调用还没返回，运行就中断了。")}</ActivityNote></ActivityDetail> : null}
    {call.prompt ? <ActivityRow id="task" status={`${text("Task", "任务")}: ${call.prompt}`}>
      {() => <ActivityDetail><ActivityKeyValue label={text("Task", "任务")} value={call.prompt!} /></ActivityDetail>}
    </ActivityRow> : null}
    {call.command ? <ActivityRow id="command" status={`${text("Command", "命令")}: ${call.command.trim().replace(/\s+/g, " ")}`}>
      {() => <ActivityDetail><ActivityKeyValue label="" value={call.command} mono /></ActivityDetail>}
    </ActivityRow> : null}
    {step?.result != null ? <ActivityRow id="output" status={text("Output", "输出")} tone={step.failed ? "err" : undefined} defaultOpen>
      {() => <ActivityDetail>
        <ActivityKeyValue label="" value={step.result ?? ""} mono />
        {step.result_truncated ? <ActivityNote>{text("Cut to what was saved.", "只显示保存下来的部分。")}</ActivityNote> : null}
      </ActivityDetail>}
    </ActivityRow> : null}
  </ActivityPageFrame>;
}
