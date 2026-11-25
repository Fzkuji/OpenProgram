// What a task run produced and what the author does with it: the result card on a task's
// page, the pending results and uncommitted changes on the overview, and the changes page
// where changes are read, undone and committed.
import { useCallback, useContext, useEffect, useMemo, useState } from "react";
import { Check, ChevronRight, FileText, Folder, GitCommitHorizontal, Undo2, Wrench } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import { HoverTip } from "@/components/ui/tooltip";
import { confirmAction } from "@/components/ui/confirm-dialog";
import { showToast } from "@/lib/format-utils/toast";
import { cn } from "@/lib/utils";
import { explain, useApp } from "./App";
import { op } from "./api";
import { ShowContext, useSend } from "./Desk";
import { RESULT, ago, baseName, type Change, type Edit, type Made, type PastRun, type Result, type Task } from "./model";

const CARD = "rounded-xl border border-border bg-card";
const LABEL = "text-[13px] font-medium text-text-muted";
const COUNT = "text-xs font-normal text-text-muted tabular-nums";


function tone(status: string) {
  return status === "FAIL" || status === "BLOCKED" ? "var(--accent-red)"
    : status === "PASS" ? "var(--accent-green)" : "var(--warning-soft)";
}

/** Runs an action of the result; a change of project files offers to undo it. */
function useAct(reload: () => void) {
  const { touched } = useApp();
  return useCallback(async (name: string, input: object, done?: (out: any) => { text: string; paths?: string[] }) => {
    try {
      const out = await op<any>(name, input);
      reload(); touched();
      const said = done?.(out);
      if (said) {
        showToast(said.text, said.paths?.length ? {
          action: {
            label: "撤销", run: async () => {
              try { await op("revert", { paths: said.paths }); reload(); touched(); } catch (e) { showToast(explain((e as Error).message), { tone: "error" }); }
            },
          },
        } : {});
      }
      return out;
    } catch (e) {
      showToast(explain((e as Error).message), { tone: "error" });
      return null;
    }
  }, [reload, touched]);
}

function EditRow({ edit, onApply, onReject }: { edit: Edit; onApply(): void; onReject(): void }) {
  const done = edit.state !== "pending";
  return (
    <div className="flex items-start gap-3 py-2.5 first:pt-0 last:pb-0 [&+&]:border-t [&+&]:border-border">
      <div className="min-w-0 flex-1">
        <div className="flex items-baseline gap-2 text-[13px]">
          <span className="font-mono text-xs text-text-primary">{edit.file}</span>
          {edit.section ? <span className="truncate text-xs text-text-muted">{edit.section}</span> : null}
        </div>
        {edit.problem ? <div className="mt-0.5 text-[13px] text-text-bright">{edit.problem}</div> : null}
        <div className="mt-1.5 overflow-hidden rounded-md border border-border font-mono text-xs leading-5">
          {edit.kind === "create" ? null : (
            <div className="line-clamp-2 bg-[color-mix(in_oklab,var(--accent-red)_9%,transparent)] px-2 text-text-primary">− {edit.quote}</div>
          )}
          <div className="line-clamp-3 whitespace-pre-wrap bg-[color-mix(in_oklab,var(--accent-green)_10%,transparent)] px-2 text-text-primary">+ {edit.replacement}</div>
        </div>
      </div>
      {done ? (
        <span className="shrink-0 pt-0.5 text-xs text-text-muted">{edit.state === "applied" ? "已应用" : edit.state === "rejected" ? "已忽略" : "已失效"}</span>
      ) : (
        <div className="flex shrink-0 gap-1">
          <Button size="xs" variant="elevated" onClick={onApply}>应用</Button>
          <Button size="xs" variant="ghost" className="text-text-muted" onClick={onReject}>忽略</Button>
        </div>
      )}
    </div>
  );
}

function FileRow({ file, onPlace }: { file: Made; onPlace(): void }) {
  const show = useContext(ShowContext);
  const Icon = file.kind === "folder" ? Folder : FileText;
  const open = () => file.kind === "folder" ? op("reveal", { path: file.path, show: true }).catch(() => {}) : show(file.path);
  return (
    <div className="flex h-8 items-center gap-2.5 text-[13px]">
      <Icon className="size-4 shrink-0 text-text-muted" />
      <button type="button" className="min-w-0 truncate text-left text-text-bright hover:underline" onClick={open} title={file.path}>{file.name}</button>
      {file.place ? <span className="min-w-0 truncate font-mono text-xs text-text-muted">→ {file.place}</span> : null}
      <span className="flex-1" />
      {file.place ? (file.placed
        ? <span className="flex shrink-0 items-center gap-1 text-xs text-text-muted"><Check className="size-3.5" />已放入</span>
        : <Button size="xs" variant="elevated" onClick={onPlace}>放入</Button>)
        : (
          <HoverTip label="在 Finder 中显示">
            <Button size="xs" variant="ghost" className="text-text-muted" onClick={() => op("reveal", { path: file.path, show: true }).catch(() => {})}>显示</Button>
          </HoverTip>
        )}
    </div>
  );
}

/** The newest run of a task: its verdict, the author's questions, the proposed edits, the
 *  files it made and, for a failed check, the problems to fix. */
export function ResultCard({ task, fixable }: { task: Task; fixable: string[] }) {
  const { changes } = useApp();
  const send = useSend();
  const show = useContext(ShowContext);
  const [data, setData] = useState<{ result: Result | null; history: PastRun[] } | null>(null);
  const [tick, setTick] = useState(0);
  const [past, setPast] = useState(false);
  const reload = useCallback(() => setTick((n) => n + 1), []);
  const act = useAct(reload);
  useEffect(() => {
    let alive = true;
    op<{ result: Result | null; history: PastRun[] }>("result", { task: task.name })
      .then((d) => { if (alive) setData(d); }).catch(() => { if (alive) setData({ result: null, history: [] }); });
    return () => { alive = false; };
  }, [task.name, changes, tick, task.last_runs[0]?.at]);
  const r = data?.result;
  if (!r) return null;

  const pendingEdits = r.edits.filter((e) => e.state === "pending");
  const apply = (ids: string[]) => act("apply_edits", { task: r.task, run: r.run, ids }, (out) => {
    const files = [...new Set(r.edits.filter((e) => out.applied.includes(e.id)).map((e) => e.file))];
    return out.applied.length ? { text: `已应用到 ${files.map(baseName).join("、")}`, paths: files } : { text: "这条修改已无法应用" };
  });
  const fix = fixable.length && r.status === "FAIL";
  const older = (data?.history || []).filter((h) => h.run !== r.run);

  return (
    <div className={cn(CARD, "flex shrink-0 flex-col gap-3 px-4 py-3.5")}>
      <div className="flex items-start gap-2.5">
        <span className="mt-[7px] size-2 shrink-0 rounded-full" style={{ background: tone(r.status) }} />
        <div className="min-w-0 flex-1">
          <div className="text-[14px] font-semibold text-text-bright">{r.verdict || RESULT[r.status] || r.status}</div>
        </div>
        {r.report ? <Button size="xs" variant="ghost" className="shrink-0 text-text-muted" onClick={() => show(r.report)}>报告</Button> : null}
      </div>

      {r.questions.length ? (
        <div className="flex flex-col gap-2">
          {r.questions.map((q) => (
            <div key={q.id} className="flex items-center gap-3 rounded-lg border border-border bg-background px-3 py-2">
              <div className="min-w-0 flex-1">
                <div className="truncate text-[13px] text-text-bright" title={q.title}>{q.title}</div>
                {q.detail ? <div className="truncate text-xs text-text-muted">{q.detail}</div> : null}
              </div>
              {q.answer ? (
                <span className="shrink-0 text-xs text-text-muted">已{q.options.find((o) => o.id === q.answer)?.label || q.answer}</span>
              ) : q.options.map((o, i) => (
                <Button key={o.id} size="xs" variant={i ? "ghost" : "elevated"} className={cn("shrink-0", i && "text-text-muted")}
                  onClick={() => act("answer", { task: r.task, run: r.run, question: q.id, option: o.id })}>{o.label}</Button>
              ))}
            </div>
          ))}
        </div>
      ) : null}

      {r.edits.length ? (
        <div className="flex flex-col">
          <div className="mb-2 flex items-center gap-2">
            <span className={LABEL}>修改建议</span><span className={COUNT}>{pendingEdits.length}/{r.edits.length}</span>
            <span className="flex-1" />
            {pendingEdits.length > 1 ? <Button size="xs" variant="ghost" onClick={() => apply(pendingEdits.map((e) => e.id))}>全部应用</Button> : null}
          </div>
          <div className="flex max-h-72 flex-col overflow-y-auto">
            {r.edits.map((e) => (
              <EditRow key={e.id} edit={e} onApply={() => apply([e.id])}
                onReject={() => act("reject_edits", { run: r.run, ids: [e.id] })} />
            ))}
          </div>
        </div>
      ) : null}

      {r.files.length ? (
        <div className="flex flex-col">
          <div className={cn(LABEL, "mb-1")}>产出</div>
          {r.files.map((f) => (
            <FileRow key={f.path} file={f} onPlace={() => act("place_file", { task: r.task, run: r.run, name: f.name },
              (out) => ({ text: `已放入 ${out.placed}`, paths: [out.placed] }))} />
          ))}
        </div>
      ) : null}

      <div className="flex items-center gap-2">
        {fix ? (
          <Button size="sm" onClick={() => send(`把上次检查的 ${fixable.length} 处问题修掉：\n${fixable.slice(0, 30).map((p) => `- ${p}`).join("\n")}`)}>
            <Wrench />修复 {fixable.length} 处
          </Button>
        ) : null}
        {older.length ? (
          <Button size="xs" variant="ghost" className="text-text-muted" onClick={() => setPast(!past)}>
            历史记录 <span className="tabular-nums">{older.length}</span><ChevronRight className={cn("transition-transform", past && "rotate-90")} />
          </Button>
        ) : null}
        <span className="flex-1" />
        {r.pending ? <Button size="xs" variant="ghost" className="text-text-muted" onClick={() => act("set_aside", { run: r.run, value: true })}>归档</Button>
          : r.set_aside ? <Button size="xs" variant="ghost" className="text-text-muted" onClick={() => act("set_aside", { run: r.run, value: false })}>取消归档</Button> : null}
      </div>
      {past ? (
        <div className="-mt-1 flex flex-col">
          {older.map((h) => (
            <button key={h.run} type="button" disabled={!h.report} onClick={() => show(h.report)}
              className="flex h-8 items-center gap-2.5 rounded-md px-1.5 text-left text-[13px] hover:bg-[var(--bg-hover)]">
              <span className="size-1.5 shrink-0 rounded-full" style={{ background: tone(h.status) }} />
              <span className="w-20 shrink-0 text-xs text-text-muted">{h.at ? ago(h.at) : ""}</span>
              <span className="min-w-0 flex-1 truncate text-text-primary">{h.verdict || RESULT[h.status] || h.status || "—"}</span>
            </button>
          ))}
        </div>
      ) : null}
    </div>
  );
}

/** What to press on the overview for a pending result. */
function verbOf(r: Result) {
  if (r.todo.questions) return "确认";
  if (r.todo.edits) return "审阅";
  if (r.todo.places) return "放入";
  return "修复";
}

function whatOf(r: Result) {
  const parts = [
    r.todo.questions ? `${r.todo.questions} 项待确认` : "",
    r.todo.edits ? `${r.todo.edits} 条修改建议` : "",
    r.todo.places ? `${r.todo.places} 个文件待放入` : "",
  ].filter(Boolean);
  return parts.join(" · ") || r.verdict;
}

export function usePending() {
  const { changes } = useApp();
  const [data, setData] = useState<{ results: Result[]; changes: Change[] }>({ results: [], changes: [] });
  useEffect(() => {
    let alive = true;
    op<{ results: Result[]; changes: Change[] }>("pending").then((d) => { if (alive) setData(d); }).catch(() => {});
    return () => { alive = false; };
  }, [changes]);
  return data;
}

/** The overview's list of results that still ask something of the author. */
export function PendingCard({ results }: { results: Result[] }) {
  const { go } = useApp();
  if (!results.length) return null;
  return (
    <div className={cn(CARD, "px-4 py-3.5")}>
      <div className={cn(LABEL, "mb-1")}>待处理 <span className={COUNT}>{results.length}</span></div>
      {results.map((r, i) => (
        <div key={r.run} className={cn("flex items-center gap-3 py-2.5", i && "border-t border-border")}>
          <span className="size-2 shrink-0 rounded-full" style={{ background: tone(r.status) }} />
          <div className="min-w-0 flex-1">
            <div className="text-[13px] font-medium text-text-bright">{r.title}</div>
            <div className="truncate text-xs text-text-muted">{whatOf(r)}</div>
          </div>
          <Button size="sm" variant={i ? "elevated" : "default"} onClick={() => go({ page: "task", task: r.task })}>{verbOf(r)}</Button>
        </div>
      ))}
    </div>
  );
}

function Stat({ c }: { c: Change }) {
  if (c.state === "new") return <span className="text-xs text-text-muted">新文件</span>;
  if (c.state === "deleted") return <span className="text-xs text-text-muted">已删除</span>;
  return (
    <span className="font-mono text-xs tabular-nums">
      <span className="text-[var(--accent-green)]">+{c.added}</span> <span className="text-[var(--accent-red)]">−{c.removed}</span>
    </span>
  );
}

/** The overview's uncommitted changes, with where each came from. */
export function ChangesCard({ items }: { items: Change[] }) {
  const { go } = useApp();
  if (!items.length) return null;
  return (
    <div className={cn(CARD, "px-4 py-3.5")}>
      <div className={cn(LABEL, "mb-1.5")}>改动 <span className={COUNT}>{items.length}</span></div>
      {items.slice(0, 6).map((c) => (
        <div key={c.path} className="flex h-8 items-center gap-2.5 text-[13px]">
          <span className="min-w-0 flex-1 truncate font-mono text-xs text-text-primary" title={c.path}>{c.path}</span>
          {c.by ? <span className="shrink-0 text-xs text-text-muted">{c.by}</span> : null}
          <Stat c={c} />
        </div>
      ))}
      {items.length > 6 ? <div className="text-xs text-text-muted">另有 {items.length - 6} 个</div> : null}
      <div className="mt-2.5 flex gap-2">
        <Button size="sm" onClick={() => go({ page: "changes" })}><GitCommitHorizontal />提交</Button>
        <Button size="sm" variant="ghost" onClick={() => go({ page: "changes" })}>查看</Button>
      </div>
    </div>
  );
}

function DiffView({ text }: { text: string }) {
  const lines = text.split("\n").filter((l, i, all) => !(i === all.length - 1 && !l));
  const body = lines.filter((l) => !/^(diff --git|index |--- |\+\+\+ |new file mode|deleted file mode)/.test(l));
  if (!body.length) return <p className="p-4 text-[13px] text-text-muted">二进制文件，无法显示差异</p>;
  return (
    <div className="font-mono text-xs leading-5">
      {body.map((l, i) => (
        <div key={i} className={cn("whitespace-pre-wrap break-all px-3",
          l.startsWith("@@") ? "bg-[var(--bg-secondary)] text-text-muted"
            : l.startsWith("+") ? "bg-[color-mix(in_oklab,var(--accent-green)_10%,transparent)] text-text-primary"
              : l.startsWith("-") ? "bg-[color-mix(in_oklab,var(--accent-red)_9%,transparent)] text-text-primary" : "text-text-primary")}>
          {l || " "}
        </div>
      ))}
    </div>
  );
}

/** Every uncommitted change: pick the files, read each diff, undo a file, commit the picked ones. */
export function ChangesPage() {
  const { changes: tick, touched, go } = useApp();
  const [items, setItems] = useState<Change[] | null>(null);
  const [suggested, setSuggested] = useState("");
  const [picked, setPicked] = useState<Set<string>>(new Set());
  const [current, setCurrent] = useState("");
  const [diff, setDiff] = useState("");
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    let alive = true;
    op<{ changes: Change[]; message: string }>("changes").then((d) => {
      if (!alive) return;
      setItems(d.changes); setSuggested(d.message);
      setPicked((old) => new Set(d.changes.map((c) => c.path).filter((p) => old.size === 0 || old.has(p))));
      setCurrent((cur) => d.changes.some((c) => c.path === cur) ? cur : d.changes[0]?.path || "");
    }).catch(() => { if (alive) setItems([]); });
    return () => { alive = false; };
  }, [tick]);
  useEffect(() => {
    if (!current) { setDiff(""); return; }
    let alive = true;
    op<{ diff: string }>("diff", { path: current }).then((d) => { if (alive) setDiff(d.diff); }).catch(() => {});
    return () => { alive = false; };
  }, [current, tick]);

  const list = items || [];
  const chosen = useMemo(() => list.filter((c) => picked.has(c.path)).map((c) => c.path), [list, picked]);
  const toggle = (path: string, on: boolean) => setPicked((s) => { const n = new Set(s); if (on) n.add(path); else n.delete(path); return n; });
  const undo = async (c: Change) => {
    const ok = await confirmAction({
      title: `撤销 ${baseName(c.path)} 的改动？`,
      description: c.state === "new" ? "这个新文件会被删除。" : "文件会恢复到上次提交的内容。",
      confirmLabel: "撤销", destructive: true,
    });
    if (!ok) return;
    try { await op("revert", { paths: [c.path] }); touched(); } catch (e) { showToast(explain((e as Error).message), { tone: "error" }); }
  };
  const commit = async () => {
    setBusy(true);
    try {
      const out = await op<{ commit: string; message: string }>("commit", { paths: chosen, message });
      setMessage(""); touched();
      showToast(`已提交 ${out.commit}`);
      if (chosen.length === list.length) go({ page: "overview" });
    } catch (e) { showToast(explain((e as Error).message), { tone: "error" }); }
    finally { setBusy(false); }
  };
  if (!items) return null;
  const shownChange = list.find((c) => c.path === current);

  return (
    <div className="mx-auto flex h-full w-full max-w-[1180px] flex-col gap-4 px-8 pt-5 pb-5">
      <div className="flex items-center gap-3">
        <h1 className="text-xl font-semibold text-text-bright">改动</h1>
        <span className="text-[13px] text-text-muted">{list.length ? `${list.length} 个文件未提交` : "没有未提交的改动"}</span>
      </div>
      {list.length ? (
        <div className="grid min-h-0 flex-1 grid-cols-[320px_minmax(0,1fr)] gap-4">
          <div className="flex min-h-0 flex-col gap-3">
            <div className={cn(CARD, "flex min-h-0 flex-1 flex-col overflow-y-auto p-1.5")}>
              {list.map((c) => (
                <div key={c.path} className={cn("group flex min-h-9 items-center gap-2.5 rounded-lg px-2 py-1", c.path === current ? "bg-[var(--bg-hover)]" : "hover:bg-[var(--bg-hover)]")}>
                  <Checkbox checked={picked.has(c.path)} onCheckedChange={(on) => toggle(c.path, !!on)} aria-label={`提交 ${c.path}`} />
                  <button type="button" className="flex min-w-0 flex-1 flex-col text-left" onClick={() => setCurrent(c.path)}>
                    <span className="truncate text-[13px] text-text-bright" title={c.path}>{baseName(c.path)}</span>
                    <span className="truncate text-xs text-text-muted">{c.path.includes("/") ? c.path.slice(0, c.path.lastIndexOf("/")) : "项目根目录"}{c.by ? ` · ${c.by}` : ""}</span>
                  </button>
                  <Stat c={c} />
                </div>
              ))}
            </div>
            <div className={cn(CARD, "flex flex-col gap-2 p-3")}>
              <Input value={message} onChange={(e) => setMessage(e.target.value)} placeholder={suggested} aria-label="提交说明" />
              <Button disabled={busy || !chosen.length} onClick={commit}><GitCommitHorizontal />提交 {chosen.length} 个文件</Button>
            </div>
          </div>
          <div className={cn(CARD, "flex min-h-0 flex-col overflow-hidden")}>
            {shownChange ? (
              <div className="flex h-10 shrink-0 items-center gap-2 border-b border-border px-3">
                <span className="min-w-0 flex-1 truncate font-mono text-xs text-text-bright">{shownChange.path}</span>
                <Button size="xs" variant="ghost" onClick={() => undo(shownChange)}><Undo2 />撤销</Button>
              </div>
            ) : null}
            <div className="min-h-0 flex-1 overflow-auto bg-background">{diff ? <DiffView text={diff} /> : null}</div>
          </div>
        </div>
      ) : null}
    </div>
  );
}
