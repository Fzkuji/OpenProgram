"use client";

/** A background program: how far it is, its command, its output against time. */
import { processIsActive, stopProcess, type ManagedProcess } from "@/lib/net/execution/process-client";
import { updatedTime } from "../debugger/debugger-presentation";
import type { ActivityPageProps, ActivityPanelContext, ActivityTone } from "./activity-page-types";
import { CircleX } from "lucide-react";
import { activityGoneFrame, ActivityPageFrame } from "./activity-page";
import { barOf, programAxis, tickOf } from "./activity-page-geometry";
import { ActivityActionRow, ActivityDayRow, ActivityDetail, ActivityInListReact, ActivityKeyValue, ActivityNote, ActivityRow } from "./activity-page-row";
import { clockTime, dayName, durationWords } from "./activity-run-wording";
import { clock, ProgramOutput, ProgressBar, progressLook, type ProgressLook } from "./program-progress";
import { Glyph } from "./running-rows";
import { commandLine, programBarTone, programName, programState, programStatus, programTitle, programTone } from "./running-format";
import styles from "./activity-page.module.css";

const failedProgram = (item: ManagedProcess) => item.status === "failed" || (item.exit_code != null && item.exit_code !== 0);

/** While it runs, its progress in level 1's words; after, when and how long. */
function headStatus(item: ManagedProcess, look: ProgressLook | null, context: ActivityPanelContext, running: boolean): { label: string; tone: ActivityTone } {
  const { text, now } = context;
  if (running) return { label: programStatus(item, look, now, text), tone: programTone(item) };
  if (processIsActive(item)) return { label: programState(item, text), tone: "muted" };
  const failed = failedProgram(item);
  const clean = item.status === "completed" || (item.status === "exited" && item.exit_code === 0);
  return {
    label: [
      clean ? "" : programState(item, text), clockTime(item.started_at),
      item.ended_at != null ? durationWords(item.ended_at - item.started_at, text) : "",
      item.exit_code != null ? text(`exit code ${item.exit_code}`, `退出码 ${item.exit_code}`) : "",
    ].filter(Boolean).join(" · "),
    tone: failed ? "err" : "muted",
  };
}

export function ProgramPage({ target, context }: ActivityPageProps<"program">) {
  const { text, now, processes } = context;
  const item = processes.detail?.process.id === target.processId
    ? processes.detail.process
    : processes.items.find(entry => entry.id === target.processId);
  if (!item) return processes.loaded && !processes.stale
    ? activityGoneFrame(text)
    : <ActivityPageFrame kind={<Glyph name="code-square" tone="shell" large />} title={text("Program", "程序")} status={processes.stale ? text("Program details unavailable.", "暂时无法读取程序详情。") : text("Loading program…", "正在读取程序…")} axis={null}>{null}</ActivityPageFrame>;

  const look = progressLook(item.progress);
  const active = processIsActive(item) && item.status !== "unknown";
  const end = item.ended_at ?? now;
  const seconds = Math.max(1, end - item.started_at);
  const axis = programAxis(seconds);
  const head = headStatus(item, look, context, active);
  const bar = active || look?.fraction != null
    ? <span className="block m-[5px_0_2px]"><ProgressBar fraction={look?.fraction ?? null} tone={programBarTone(item, look)} noteClassName={styles.barNote}
      note={active ? look?.eta && text(`${look.eta} left`, `剩 ${look.eta}`) : item.ended_at != null ? clock(item.ended_at - item.started_at) : null} /></span>
    : null;
  const pendingKey = `program:${item.id}`;
  const stop = () => void context.act(async () => { await stopProcess(item.id, context.sessionId); processes.refresh(); },
    text("Could not stop the program. Its status will update automatically; try again once it is available.", "未能停止程序，状态将自动更新，恢复后可重试。"), pendingKey);
  const time = clockTime(item.started_at, true);
  const figures: [string, string][] = active
    ? [[text("Elapsed", "已用时间"), clock(now - item.started_at)], [text("Speed", "速度"), look?.rate ?? "—"], [text("Left", "剩余"), look?.eta ?? "—"]]
    : [[text("Took", "用时"), clock(end - item.started_at)], [text("Ended", "结束"), updatedTime(end)], [text("Exit code", "退出码"), item.exit_code != null ? String(item.exit_code) : "—"]];
  const progressWords = look?.count ?? (active ? text("Working…", "运行中…") : programState(item, text));
  const showsProgress = active || look !== null;
  return <ActivityPageFrame kind={<Glyph name="code-square" tone="shell" large />} title={programTitle(item, text)} status={head.label} tone={head.tone} headExtra={bar} axis={axis}>
    {processes.stale ? <ActivityDetail><ActivityNote>{text("Program updates are unavailable. Showing saved results and retrying automatically.", "程序更新暂时不可用，保留上次记录并自动重试。")}</ActivityNote></ActivityDetail> : null}
    {["unknown", "lost"].includes(item.status) ? <ActivityDetail><ActivityNote>{text("The program's supervisor is unavailable, so whether it is still running is not known.", "程序的监督进程不可用，不知道它是否还在运行。")}</ActivityNote></ActivityDetail> : null}
    {processIsActive(item) ? <ActivityActionRow icon={CircleX} label={text("Stop program", "停止程序")} note={text("Stops only this program", "只停止这个程序")}
      disabled={processes.stale || item.can_stop === false} busy={context.pendingKey === pendingKey} onClick={stop} /> : null}
    <ActivityInListReact.Provider value={true}>
      <ActivityDayRow name={dayName(item.started_at, text, now)} axis={axis} />
      {showsProgress ? <ActivityRow id="progress" status={`${text("Progress", "进度")}: ${[look?.stage, progressWords, look?.fraction != null ? `${Math.floor(look.fraction * 100)}%` : ""].filter(Boolean).join(" · ")}`}>
        {() => <ActivityDetail>
          {figures.map(([label, value]) => <ActivityKeyValue key={label} label={label} value={value} />)}
          {active && item.last_line ? <ActivityKeyValue label={text("Last line", "最后一行")} value={item.last_line} mono /> : null}
        </ActivityDetail>}
      </ActivityRow> : null}
      <ActivityRow id="command" status={`${text("Command", "命令")}: ${commandLine(item)}`} time={time} mark={{ kind: "tick", ...tickOf(0, axis), tone: "muted" }}>
        {() => <ActivityDetail><ActivityKeyValue label="" value={item.command} mono /></ActivityDetail>}
      </ActivityRow>
      <ActivityRow id="output" status={active ? `${text("Output", "输出")} · ${text("Live", "实时")}` : text("Output", "输出")} tone={active ? "run" : undefined} defaultOpen
        time={time} mark={{ kind: "bar", ...barOf(0, item.ended_at != null ? seconds : null, axis), tone: head.tone === "err" ? "err" : active ? "run" : "muted" }}>
        {() => <ActivityDetail>
          {item.truncated ? <ActivityNote>{text("Earlier output was truncated; the process record is retained.", "较早的输出已截断，程序记录仍然保留。")}</ActivityNote> : null}
          <div className={styles.programOutput}>
            <ProgramOutput output={processes.detail?.output || ""} live={active} empty={processes.detail ? text("No output yet.", "还没有输出。") : text("Loading output…", "正在读取输出…")} />
          </div>
        </ActivityDetail>}
      </ActivityRow>
      <ActivityRow id="technical" status={text("Technical details", "技术详情")}>
        {() => <ActivityDetail>
          {item.title ? <ActivityKeyValue label={text("Program", "程序")} value={programName(item, text)} /> : null}
          <ActivityKeyValue label={text("Program ID", "程序 ID")} value={item.id} />
          <ActivityKeyValue label={text("Folder", "工作目录")} value={item.cwd || "—"} />
          <ActivityKeyValue label={text("Environment", "运行环境")} value={item.backend_id} />
          {item.execution_id ? <ActivityKeyValue label={text("Execution", "执行")} value={item.execution_id} /> : null}
        </ActivityDetail>}
      </ActivityRow>
    </ActivityInListReact.Provider>
  </ActivityPageFrame>;
}
