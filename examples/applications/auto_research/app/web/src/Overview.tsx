// The project's own page. Until the paper's kind of work is set it asks what kind it is,
// with the reader's suggestion; then it shows the reading of where each task stands for
// the author to confirm; after that, what the project is, what to do next and the
// progress of each phase. A conversation about the whole project runs under it.
import { useEffect, useRef, useState } from "react";
import { ChevronDown, LoaderCircle, RefreshCw, Sparkles } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { HoverTip } from "@/components/ui/tooltip";
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger } from "@/components/ui/dropdown-menu";
import { MENU_ITEM_STATES, MENU_PANEL, itemCls } from "@/components/ui/menu/menu-styles";
import { cn } from "@/lib/utils";
import { useApp } from "./App";
import { Desk } from "./Desk";
import { ChangesCard, PendingCard, usePending } from "./Results";
import {
  PHASES, RESULT, SHOWN, SOURCE, STAND, ago, daysLeft, dollars, nextTasks, readingOf, serves, shown,
  type Overview, type Progress, type Reading, type Stand, type Task,
} from "./model";
import { DOT_LABEL, ProgressDot } from "./Workspace";

const CARD = "rounded-xl border border-border bg-card px-4 py-3.5";
const CARD_TITLE = "mb-2 text-[13px] font-medium text-text-muted";

/** What the agent is told beside each message of the project's conversation. */
function projectNote(view: Overview) {
  const kind = view.kinds.find((k) => k.id === view.brief.kind);
  const line = (t: Task) => {
    const r = readingOf(view, t.name);
    if (r) return `- ${t.title} (${t.name}): ${STAND[r.state]}${r.approval ? ", waiting for the author's approval" : ""}. ${r.evidence}${r.missing ? ` Missing: ${r.missing}` : ""}`;
    const last = t.last_runs[0];
    return `- ${t.title} (${t.name}): ${SHOWN[shown(t)]}${last ? `, last check ${RESULT[last.status] || last.status} ${ago(last.at)}` : ""}${t.problems[0] ? `; first problem: ${t.problems[0]}` : ""}`;
  };
  return [
    `The author is talking to you from the Auto Research app, about their research paper project as a whole.`,
    `Project folder: ${view.project} (Git repository, ${view.branch} at ${view.head}${view.dirty.length ? `, ${view.dirty.length} uncommitted changes` : ""}).`,
    kind ? `Kind of work: ${kind.title} (${kind.id}): ${kind.about}.${view.brief.claim ? ` Main claim: ${view.brief.claim}` : ""} Both are in workflow/project.md.` : `The kind of work is not set yet.`,
    view.paper_pdf ? `The built paper: ${view.paper_pdf}.` : "",
    ...PHASES.map(([phase, label]) => {
      const tasks = view.tasks.filter((t) => t.phase === phase && serves(t, view.brief.kind));
      return tasks.length ? `${label} tasks:\n${tasks.map(line).join("\n")}` : "";
    }),
    `Where each task stands is kept in workflow/progress.json. The tasks' files live under workflow/. Change the project's files directly when the author asks for changes, and say which files you changed. A request that belongs to one task is best done on that task's page; say so. Never write "Approved: yes" yourself: approval is the author's. Never invent data, numbers, results or references.`,
  ].filter(Boolean).join("\n");
}

function Busy({ what }: { what: string }) {
  return (
    <div className="flex items-center gap-2 text-[13px] text-text-muted">
      <LoaderCircle className="size-4 animate-spin" />{what}
    </div>
  );
}

/** Step one: what kind of work this is, with the reader's suggestion; each kind says what it adds and drops. */
function KindPicker() {
  const { view, reading, suggestKind, setBrief, scan } = useApp();
  const asked = useRef(false);
  const [picked, setPicked] = useState("");
  const s = view!.suggestion;
  useEffect(() => {
    if (!asked.current && !s.kind && !reading) { asked.current = true; suggestKind(); }
  }, [s.kind, reading, suggestKind]);
  const choice = picked || s.kind || "";
  const all = view!.tasks;
  const choose = async () => {
    if (await setBrief({ kind: choice, ...(s.claim && !view!.brief.claim ? { claim: s.claim } : {}) })) scan();
  };
  return (
    <div className="flex flex-col gap-4 pb-4">
      {reading === "kind" ? <Busy what="正在读论文，看看这是什么工作…" /> : s.kind ? (
        <div className="rounded-xl bg-[color-mix(in_oklab,var(--primary)_8%,transparent)] px-4 py-3 text-[13px]">
          <div className="flex items-center gap-2 font-medium text-text-bright">
            <Sparkles className="size-4 text-primary" />建议：{view!.kinds.find((k) => k.id === s.kind)?.title}
          </div>
          <p className="mt-1 text-text-primary">{s.reason}</p>
          {s.basis ? <p className="mt-0.5 text-xs text-text-muted">依据：{s.basis}</p> : null}
        </div>
      ) : null}
      <div className="grid grid-cols-[repeat(auto-fill,minmax(190px,1fr))] gap-3">
        {view!.kinds.map((k) => {
          const dropped = all.filter((t) => !serves(t, k.id));
          const on = choice === k.id;
          return (
            <button key={k.id} type="button" aria-pressed={on} onClick={() => setPicked(k.id)}
              className={cn(CARD, "relative flex flex-col gap-1 text-left transition-colors hover:border-[var(--text-dim)]",
                on && "border-primary ring-1 ring-primary")}>
              {s.kind === k.id ? <span className="absolute top-3 right-3 text-xs text-primary">建议</span> : null}
              <span className="text-[14px] font-medium text-text-bright">{k.title}</span>
              <span className="text-xs text-text-muted">{k.about}</span>
              <span className="mt-2 text-xs text-text-muted">
                {dropped.length ? <>不用：{dropped.map((t) => t.title).join("、")}</> : "列出全部任务"}
              </span>
            </button>
          );
        })}
      </div>
      <div className="flex items-center gap-2">
        <Button disabled={!choice || !!reading} onClick={choose}>
          定为{view!.kinds.find((k) => k.id === choice)?.title || "这一类"}，开始盘点
        </Button>
        {s.kind && reading !== "kind" ? (
          <Button variant="ghost" onClick={suggestKind}><RefreshCw />重新读</Button>
        ) : null}
      </div>
    </div>
  );
}

function ChangeState({ row }: { row: Reading }) {
  const { setProgress } = useApp();
  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button variant="ghost" size="xs" className="-mr-1 text-text-muted">改成<ChevronDown /></Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end" className={cn(MENU_PANEL, "min-w-32")}>
        {(["done", "doing", "todo"] as Stand[]).filter((s) => s !== row.state).map((s) => (
          <DropdownMenuItem key={s} className={cn(itemCls(false), MENU_ITEM_STATES)} onSelect={() => setProgress({ task: row.task, state: s })}>
            <ProgressDot dot={s} className="size-3.5" />{STAND[s]}
          </DropdownMenuItem>
        ))}
      </DropdownMenuContent>
    </DropdownMenu>
  );
}

/** Step two: the reading, task by task, in three columns, each with what was seen. */
function ScanReview({ progress }: { progress: Progress | null }) {
  const { view, reading, scan, go } = useApp();
  const asked = useRef(false);
  useEffect(() => {
    if (!progress && !reading && !asked.current) { asked.current = true; scan(); }
  }, [progress, reading, scan]);
  if (reading === "scan" || !progress) return <Busy what="正在盘点：读论文、图表、实验文件和每个任务的检查…" />;
  const title = (name: string) => view!.tasks.find((t) => t.name === name)?.title || name;
  return (
    <div className="grid grid-cols-3 gap-3 pb-4">
      {(["done", "doing", "todo"] as Stand[]).map((state) => {
        const rows = progress.tasks.filter((r) => r.state === state);
        return (
          <div key={state} className="flex min-w-0 flex-col gap-2">
            <div className="flex items-center gap-1.5 text-[13px] font-medium text-text-bright">
              <ProgressDot dot={state} className="size-3.5" />{STAND[state]}<span className="font-normal text-text-muted">{rows.length}</span>
            </div>
            {rows.map((r) => (
              <div key={r.task} className={cn(CARD, "flex flex-col gap-1 py-2.5")}>
                <div className="flex items-center gap-1.5">
                  <button type="button" className="truncate text-left text-[13px] font-medium text-text-bright hover:underline" onClick={() => go({ page: "task", task: r.task })}>{title(r.task)}</button>
                  <Badge variant={r.source === "check" ? "success" : r.source === "author" ? "secondary" : "accent"}>{SOURCE[r.source]}</Badge>
                  <span className="flex-1" />
                  <ChangeState row={r} />
                </div>
                <p className="text-xs text-text-primary">{r.evidence}</p>
                {r.approval ? <p className="text-xs text-text-muted">还差：你批准</p> : r.missing ? <p className="text-xs text-text-muted">还差：{r.missing}</p> : null}
              </div>
            ))}
          </div>
        );
      })}
    </div>
  );
}

/** After the reading is confirmed: what to do next and each phase's progress. */
function Board({ progress }: { progress: Progress }) {
  const { view, go } = useApp();
  const next = nextTasks(view!);
  const pending = usePending();
  const verb = (r: Reading) => (r.approval ? "批准" : r.state === "doing" ? "继续" : "开始");
  return (
    <div className="grid grid-cols-[1.25fr_1fr] items-start gap-4 pb-4">
      <div className="flex flex-col gap-4">
      <PendingCard results={pending.results} />
      <div className={CARD}>
        <div className={CARD_TITLE}>接下来</div>
        {next.length ? next.map(({ task, reading }, i) => (
          <div key={task.name} className={cn("flex items-center gap-3 py-2.5", i && "border-t border-border")}>
            <ProgressDot dot={reading.approval ? "wait" : reading.state === "todo" ? "next" : reading.state} />
            <div className="min-w-0 flex-1">
              <div className="text-[13px] font-medium text-text-bright">{task.title}</div>
              <div className="line-clamp-2 text-xs text-text-muted">{reading.approval ? "只差你批准" : reading.missing || reading.evidence}</div>
            </div>
            <Button size="sm" variant={i ? "elevated" : "default"} onClick={() => go({ page: "task", task: task.name })}>{verb(reading)}</Button>
          </div>
        )) : <p className="text-[13px] text-text-muted">这类工作的任务都完成了。</p>}
      </div>
      </div>
      <div className="flex flex-col gap-4">
      <ChangesCard items={pending.changes} />
      <div className={CARD}>
        <div className={CARD_TITLE}>进度</div>
        {PHASES.map(([phase, label]) => {
          const rows = view!.tasks.filter((t) => t.phase === phase && serves(t, view!.brief.kind))
            .map((t) => progress.tasks.find((r) => r.task === t.name)).filter(Boolean) as Reading[];
          if (!rows.length) return null;
          const count = (s: Stand) => rows.filter((r) => r.state === s).length;
          const words = (["done", "doing"] as Stand[]).filter((s) => count(s)).map((s) => `${count(s)} ${s === "done" ? "完成" : "进行中"}`);
          return (
            <div key={phase} className="mb-3 last:mb-0">
              <div className="flex justify-between text-[13px] text-text-primary">{label}<span className="text-xs text-text-muted">{words.join(" · ") || "还没开始"}</span></div>
              <div className="mt-1.5 flex gap-[3px]">
                {rows.map((r) => (
                  <HoverTip key={r.task} label={`${view!.tasks.find((t) => t.name === r.task)?.title} · ${r.approval ? DOT_LABEL.wait : STAND[r.state]}`}>
                    <button type="button" aria-label={view!.tasks.find((t) => t.name === r.task)?.title} onClick={() => go({ page: "task", task: r.task })}
                      className={cn("h-1.5 flex-1 rounded-full",
                        r.state === "done" ? "bg-[var(--accent-green)]" : r.approval ? "bg-primary"
                          : r.state === "doing" ? "bg-(--warning-soft)" : "bg-[var(--bg-tertiary)]")} />
                  </HoverTip>
                ))}
              </div>
            </div>
          );
        })}
      </div>
      </div>
    </div>
  );
}

export function OverviewPage() {
  const { view, reading, scan, setProgress } = useApp();
  if (!view) return null;
  const kind = view.kinds.find((k) => k.id === view.brief.kind);
  const progress = "tasks" in view.progress ? (view.progress as Progress) : null;
  // A reading made for another kind no longer fits the task list: read again.
  const current = progress && progress.kind === view.brief.kind ? progress : null;
  const step = !kind ? "kind" : !current || !current.confirmed || reading === "scan" ? "scan" : "board";
  const days = view.deadline ? daysLeft(view.deadline) : null;

  const header = step === "kind" ? (
    <div className="flex flex-col gap-1">
      <h1 className="text-xl font-semibold text-text-bright">这是什么工作？</h1>
      <p className="text-[13px] text-text-muted">定下来以后才列任务；以后随时能在左上角换。</p>
    </div>
  ) : step === "scan" ? (
    <div className="flex items-start gap-3">
      <div className="min-w-0 flex-1">
        <h1 className="text-xl font-semibold text-text-bright">盘点结果</h1>
        <p className="text-[13px] text-text-muted">
          {current && reading !== "scan" ? `${kind!.title} · ${ago(current.at)}读了论文、文件和每个任务的检查。不对的直接改，确认后左边和概览都按它显示。` : `${kind!.title} · 看看每个任务做到哪了。`}
        </p>
      </div>
      {current && reading !== "scan" ? (
        <div className="flex shrink-0 gap-2">
          <Button variant="ghost" onClick={scan}><RefreshCw />重新盘点</Button>
          <Button onClick={() => setProgress({ confirm: true })}>就是这样</Button>
        </div>
      ) : null}
    </div>
  ) : (
    <div className="flex items-start gap-3">
      <div className="min-w-0 flex-1">
        <h1 className="text-xl font-semibold text-text-bright">概览</h1>
        <p className="line-clamp-2 text-[13px] text-text-muted">
          {[kind!.title, days !== null ? (days >= 0 ? `截稿还有 ${days} 天` : `截稿已过 ${-days} 天`) : "", view.brief.claim ? `主张：${view.brief.claim}` : "",
            view.spend?.calls ? `模型花费 ${dollars(view.spend.cost)}（${view.spend.runs} 次运行）` : ""].filter(Boolean).join(" · ")}
        </p>
      </div>
      {current!.changed.length ? (
        <HoverTip label={current!.changed.slice(0, 8).join("\n") + (current!.changed.length > 8 ? `\n……共 ${current!.changed.length} 个` : "")}>
          <Button variant="ghost" className="shrink-0 text-text-muted" onClick={scan}><RefreshCw />盘点后有 {current!.changed.length} 个文件改过，重新盘点</Button>
        </HoverTip>
      ) : (
        <Button variant="ghost" className="shrink-0 text-text-muted" onClick={scan}><RefreshCw />重新盘点</Button>
      )}
    </div>
  );

  const body = step === "kind" ? <KindPicker /> : step === "scan" ? <ScanReview progress={current} /> : <Board progress={current!} />;
  return (
    <Desk scope="" header={header} body={body} note={projectNote(view)}
      hint={step === "kind" ? "也可以直接说：这是一篇……" : "说说接下来要做什么；只做某一个任务时，先在左边点开它"} />
  );
}

