// One task: its state and check on top, its files, and the conversation that does its work.
import { useEffect, useState } from "react";
import { ChevronRight, Lock, Play, SlidersHorizontal, Square } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Checkbox } from "@/components/ui/checkbox";
import { SimpleSelect } from "@/components/ui/select";
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { HoverTip } from "@/components/ui/tooltip";
import { cn } from "@/lib/utils";
import { useApp, type Job } from "./App";
import { Desk } from "./Desk";
import { PHASES, RESULT, SHOWN, ago, shown, stageLabel, type Overview, type Task } from "./model";
import { StateIcon } from "./Workspace";

interface Form { whole: boolean; section: string; baseline: string; scope: string; phases: string[]; direction: string; reader_level: string; purpose: string; our_work: string; preset: string; reviewer_model: string }
const forms = new Map<string, Form>();
const formOf = (name: string): Form => forms.get(name) || { whole: true, section: "", baseline: "HEAD", scope: "changes", phases: [], direction: "", reader_level: "", purpose: "", our_work: "", preset: "", reviewer_model: "" };
const LABELS: Record<string, string> = { direction: "方向", reader_level: "读者水平", purpose: "用途", our_work: "我们的工作", preset: "预设", reviewer_model: "评审模型" };

function inputsOf(task: Task, f: Form) {
  const out: Record<string, string> = {};
  for (const name of task.inputs) {
    if (name === "draft_dir") continue;
    if (name === "section") out.section = task.required.includes("section") || !f.whole ? f.section : "";
    else if (name === "phases") out.phases = f.phases.join(",");
    else out[name] = (f as any)[name] || "";
  }
  return out;
}

/** What the agent is told beside each message of this task's conversation. */
export function taskNote(view: Overview, task: Task, job: Job | null) {
  const last = task.last_runs[0];
  const lines = [
    `The author is talking to you from the Auto Research app, on one task of their research paper project. Keep to this task unless they ask otherwise.`,
    `Project folder: ${view.project} (Git repository, ${view.branch} at ${view.head}${view.dirty.length ? `, ${view.dirty.length} uncommitted changes` : ""}).`,
    `Task: ${task.title} (${task.name}), ${PHASES.find(([p]) => p === task.phase)?.[1] || task.phase} phase.`,
    task.artifacts.length ? `Its files: ${task.artifacts.join(", ")}.` : task.workflow ? `It has no files of its own; its check runs the ${task.workflow} workflow.` : "",
    task.done ? `Done when: ${task.done}` : "",
    task.next ? `Next step the check gives: ${task.next}` : "",
    `State: ${SHOWN[shown(task)]}${last ? `; last check ${RESULT[last.status] || last.status} ${ago(last.at)}${last.report ? `, report at ${last.report}` : ""}` : "; never checked"}${job && !job.done && job.task === task.name ? "; a check is running now" : ""}.`,
    task.problems.length ? `What the last check found:\n${task.problems.slice(0, 20).map((p) => `- ${p}`).join("\n")}` : "",
    view.paper_pdf ? `The built paper: ${view.paper_pdf}.` : "",
    `Change the project's files directly when the author asks for changes, and say which files you changed. Never write "Approved: yes" yourself: approval is the author's. Never invent data, numbers, results or references. The author runs the task's check from the app's 运行检查 button.`,
  ];
  return lines.filter(Boolean).join("\n");
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return <div className="flex flex-col gap-1.5"><Label className="text-[13px]">{label}</Label>{children}</div>;
}

function Running({ job, onStop }: { job: Job; onStop(): void }) {
  const [now, setNow] = useState(Date.now());
  useEffect(() => { const t = setInterval(() => setNow(Date.now()), 1000); return () => clearInterval(t); }, []);
  const seconds = Math.max(0, Math.round((now - job.started) / 1000));
  const step = [...job.steps].reverse().find((s) => s.state === "run") || job.steps[job.steps.length - 1];
  return (
    <span className="flex items-center gap-2 text-[13px] text-text-primary">
      <span className="size-2 animate-pulse rounded-full bg-[var(--accent-blue)]" />
      正在检查{step ? `：${stageLabel(step.label)}` : ""}
      <span className="font-mono text-xs text-text-muted tabular-nums">{Math.floor(seconds / 60)}:{String(seconds % 60).padStart(2, "0")}</span>
      <Button variant="ghost" size="xs" onClick={onStop}><Square />停止</Button>
    </span>
  );
}

export function TaskPage({ name }: { name: string }) {
  const { view, job, go, check, stop } = useApp();
  const [f, setF] = useState<Form>(() => formOf(name));
  const [settings, setSettings] = useState(false);
  const [allProblems, setAllProblems] = useState(false);
  useEffect(() => { forms.set(name, f); }, [name, f]);
  const task = view?.tasks.find((t) => t.name === name);
  if (!view || !task) return null;

  const set = (patch: Partial<Form>) => setF((cur) => ({ ...cur, ...patch }));
  const state = shown(task);
  const last = task.last_runs[0];
  const mine = job && !job.done && job.task === task.name ? job : null;
  const busy = !!job && !job.done;
  const inputs = inputsOf(task, f);
  const missing = task.required.filter((n) => !inputs[n]);
  const needsSection = task.inputs.includes("section") && !task.required.includes("section") && !f.whole && !f.section;
  const formInputs = task.inputs.filter((n) => n !== "draft_dir");
  const helper = task.blocked_by ? view.tasks.find((t) => t.name !== task.name && !t.workflow && t.gates.length === 1 && t.gates[0] === task.blocked_by) : undefined;
  const run = () => { if (missing.length || needsSection) { setSettings(true); return; } check(task, inputs); };

  const header = (
    <div className="flex flex-col gap-1">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
        <h1 className="text-xl font-semibold text-text-bright">{task.title}</h1>
        <span className="flex items-center gap-1.5 text-[13px] text-text-muted">
          <StateIcon state={state} className="size-3.5" />{SHOWN[state]}
          {last ? <>，上次检查{ago(last.at)}</> : null}
        </span>
        <span className="flex-1" />
        {mine ? <Running job={mine} onStop={stop} /> : (
          <div className="flex items-center gap-1">
            {formInputs.length ? (
              <Popover open={settings} onOpenChange={setSettings}>
                <HoverTip label="检查的设置">
                  <PopoverTrigger asChild>
                    <Button variant="ghost" size="icon-sm" aria-label="检查的设置"><SlidersHorizontal /></Button>
                  </PopoverTrigger>
                </HoverTip>
                <PopoverContent align="end" className="shadcn-scale flex w-[340px] flex-col gap-4 p-4">
                  {task.inputs.includes("section") ? (task.required.includes("section") ? (
                    <Field label="章节">
                      <SimpleSelect value={f.section} onValueChange={(v) => set({ section: v })} label="章节" placeholder="选一个章节"
                        options={view.sections.map((s) => ({ value: s, label: s }))} />
                    </Field>
                  ) : (
                    <Field label="范围">
                      <ToggleGroup type="single" variant="outline" size="sm" value={f.whole ? "whole" : "section"} onValueChange={(v) => v && set({ whole: v === "whole" })} className="self-start">
                        <ToggleGroupItem value="whole">整篇论文</ToggleGroupItem>
                        <ToggleGroupItem value="section">一个章节</ToggleGroupItem>
                      </ToggleGroup>
                      {!f.whole ? <SimpleSelect value={f.section} onValueChange={(v) => set({ section: v })} label="章节" placeholder="选一个章节"
                        options={view.sections.map((s) => ({ value: s, label: s }))} /> : null}
                    </Field>
                  )) : null}
                  {formInputs.filter((n) => LABELS[n]).map((n) => (
                    <Field key={n} label={`${LABELS[n]}${task.required.includes(n) ? "" : "（可选）"}`}>
                      <Input value={(f as any)[n]} onChange={(e) => set({ [n]: e.target.value } as Partial<Form>)} />
                    </Field>
                  ))}
                  {task.inputs.includes("baseline") ? <Field label="对比基线"><Input value={f.baseline} onChange={(e) => set({ baseline: e.target.value })} className="font-mono" /></Field> : null}
                  {task.inputs.includes("scope") ? (
                    <Field label="检查范围">
                      <ToggleGroup type="single" variant="outline" size="sm" value={f.scope} onValueChange={(v) => v && set({ scope: v })} className="self-start">
                        <ToggleGroupItem value="changes">只查改动</ToggleGroupItem><ToggleGroupItem value="paper">全部段落</ToggleGroupItem>
                      </ToggleGroup>
                    </Field>
                  ) : null}
                  {task.inputs.includes("phases") ? (
                    <Field label="检查阶段（不选就是全部）">
                      {view.phases.map((p) => (
                        <label key={p.id} className="flex items-center gap-2 text-[13px]">
                          <Checkbox checked={f.phases.includes(p.id)} onCheckedChange={(on) => set({ phases: on ? [...new Set([...f.phases, p.id])].sort() : f.phases.filter((x) => x !== p.id) })} />
                          {p.title}
                        </label>
                      ))}
                    </Field>
                  ) : null}
                  <Button disabled={busy || !!missing.length || needsSection} onClick={() => { setSettings(false); check(task, inputs); }}><Play />运行检查</Button>
                </PopoverContent>
              </Popover>
            ) : null}
            <HoverTip label={busy ? `「${job!.title}」还在检查` : "按这个任务的标准检查项目"}>
              <Button size="sm" variant="outline" disabled={busy} onClick={run}><Play />运行检查</Button>
            </HoverTip>
          </div>
        )}
      </div>
      {task.done ? <p className="line-clamp-2 text-[13px] leading-5 text-text-muted">通过的条件：{task.done}</p> : null}
    </div>
  );

  const problems = task.problems;
  const notice = (state === "fail" || state === "blocked") && (problems.length || task.next) ? (
    <div className={cn("shrink-0 rounded-xl border px-4 py-3 text-[13px]",
      state === "blocked" ? "border-[color-mix(in_oklab,var(--accent-orange)_40%,transparent)] bg-[color-mix(in_oklab,var(--accent-orange)_7%,transparent)]"
        : "border-[color-mix(in_oklab,var(--accent-red)_35%,transparent)] bg-[color-mix(in_oklab,var(--accent-red)_6%,transparent)]")}>
      <div className="mb-1 flex items-center gap-2 font-medium text-text-bright">
        {state === "blocked" ? <Lock className="size-3.5 text-[var(--accent-orange)]" /> : null}
        {state === "blocked" ? "先要满足前置条件" : `还差 ${problems.length} 处`}
        <span className="flex-1" />
        {problems.length > 3 ? <button type="button" className="text-xs font-normal text-text-muted hover:text-text-bright" onClick={() => setAllProblems(!allProblems)}>{allProblems ? "收起" : `全部 ${problems.length} 条`}</button> : null}
      </div>
      <ul className={cn("list-disc pl-5 text-text-primary", allProblems && "max-h-40 overflow-y-auto")}>
        {(allProblems ? problems : problems.slice(0, 3)).map((p, i) => <li key={i} className={allProblems ? "" : "truncate"}>{p}</li>)}
      </ul>
      {state === "blocked" && task.next ? <p className="mt-1 text-text-muted">{task.next}</p> : null}
      {helper ? <Button variant="link" size="xs" className="mt-1 h-auto px-0" onClick={() => go({ page: "task", task: helper.name })}>去「{helper.title}」<ChevronRight /></Button> : null}
    </div>
  ) : null;

  return (
    <Desk scope={task.name} phase={task.phase} header={header} notice={notice}
      note={taskNote(view, task, job)}
      empty={`在下面说要求：agent 在这个项目文件夹里干活，知道「${task.title}」要交付什么、上次检查哪里没过。`} />
  );
}
