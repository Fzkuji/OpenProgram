// The project's own page: what to do next, the paper and what changed, and a conversation about the whole project.
import { useApp } from "./App";
import { Desk } from "./Desk";
import { PHASES, RESULT, SHOWN, ago, shown, type Overview, type Task } from "./model";
import { StateIcon } from "./Workspace";

/** What the agent is told beside each message of the project's conversation. */
function projectNote(view: Overview) {
  const line = (t: Task) => {
    const last = t.last_runs[0];
    return `- ${t.title} (${t.name}): ${SHOWN[shown(t)]}${last ? `, last check ${RESULT[last.status] || last.status} ${ago(last.at)}` : ""}${t.problems[0] ? `; first problem: ${t.problems[0]}` : ""}`;
  };
  return [
    `The author is talking to you from the Auto Research app, about their research paper project as a whole.`,
    `Project folder: ${view.project} (Git repository, ${view.branch} at ${view.head}${view.dirty.length ? `, ${view.dirty.length} uncommitted changes` : ""}).`,
    view.paper_pdf ? `The built paper: ${view.paper_pdf}.` : "",
    ...PHASES.map(([phase, label]) => {
      const tasks = view.tasks.filter((t) => t.phase === phase);
      return tasks.length ? `${label} tasks:\n${tasks.map(line).join("\n")}` : "";
    }),
    `The tasks' files live under workflow/. Change the project's files directly when the author asks for changes, and say which files you changed. Never write "Approved: yes" yourself: approval is the author's. Never invent data, numbers, results or references.`,
  ].filter(Boolean).join("\n");
}


export function OverviewPage() {
  const { view, go } = useApp();
  if (!view) return null;
  const tasks = view.tasks;
  const count = (s: string) => tasks.filter((t) => shown(t) === s).length;
  const rank = { fail: 0, blocked: 1, never: 2, pass: 3 } as const;
  const next = [...tasks].filter((t) => shown(t) !== "pass").sort((a, b) => rank[shown(a)] - rank[shown(b)]).slice(0, 5);

  const header = (
    <div className="flex flex-col gap-2.5">
      <div className="flex items-baseline gap-3">
        <h1 className="text-xl font-semibold text-text-bright">概览</h1>
        <span className="text-[13px] text-text-muted">
          {tasks.length} 个任务：{(["pass", "fail", "blocked", "never"] as const).filter((s) => count(s)).map((s) => `${count(s)} 个${SHOWN[s]}`).join("，")}
        </span>
      </div>
      {next.length ? (
        <div className="flex flex-wrap gap-2">
          {next.map((t) => (
            <button key={t.name} type="button" onClick={() => go({ page: "task", task: t.name })} title={t.problems[0] || t.next}
              className="flex max-w-[280px] items-center gap-1.5 rounded-full border border-border bg-card px-2.5 py-1 text-[13px] text-text-primary hover:border-[var(--text-dim)] hover:text-text-bright">
              <StateIcon state={shown(t)} className="size-3.5" />
              <span className="truncate">{t.title}</span>
            </button>
          ))}
        </div>
      ) : null}
    </div>
  );

  return (
    <Desk scope="" header={header} note={projectNote(view)}
      empty="在下面说要求：agent 在这个项目文件夹里干活，知道每个任务现在的状态。只做一个任务时，从左边点进那个任务再说。" />
  );
}
