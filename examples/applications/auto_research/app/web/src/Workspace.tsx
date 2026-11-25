// An open project: the project switcher, the task list, the page in the middle and the status bar.
import { Check, ChevronDown, GitCommitHorizontal, ChevronRight, CircleAlert, CircleCheck, Circle, FolderOpen, LayoutGrid, Lock, LogOut, RefreshCw } from "lucide-react";
import { useState } from "react";
import { SearchInput } from "@/components/ui/search-input";
import { ConfirmDialog } from "@/components/ui/confirm-dialog";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { HoverTip } from "@/components/ui/tooltip";
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuSeparator, DropdownMenuTrigger } from "@/components/ui/dropdown-menu";
import { MenuRow } from "@/components/ui/menu/menu-parts";
import { GROUP_LABEL, MENU_ITEM_STATES, MENU_PANEL, MENU_SEPARATOR, itemCls } from "@/components/ui/menu/menu-styles";
import { cn } from "@/lib/utils";
import { useApp } from "./App";
import { PHASES, SHOWN, STAND, baseName, dotOf, nextTasks, readingOf, serves, shown, type Dot, type Overview, type Shown } from "./model";
import { ProjectMark } from "./Welcome";
import { OverviewPage } from "./Overview";
import { TaskPage } from "./TaskPage";
import { ChangesPage } from "./Results";

export function StateIcon({ state, className }: { state: Shown; className?: string }) {
  const cls = cn("size-4 shrink-0", className);
  if (state === "pass") return <CircleCheck className={cn(cls, "text-[var(--accent-green)]")} aria-label={SHOWN.pass} />;
  if (state === "fail") return <CircleAlert className={cn(cls, "text-[var(--accent-red)]")} aria-label={SHOWN.fail} />;
  if (state === "blocked") return <Lock className={cn(cls, "text-[var(--accent-orange)]")} aria-label={SHOWN.blocked} />;
  return <Circle className={cn(cls, "text-text-muted [stroke-dasharray:3_3]")} aria-label={SHOWN.never} />;
}

const DOT_LABEL: Record<Dot, string> = { ...STAND, wait: "等你批准", next: "建议下一个" };

/** Where a task stands, as the reading says: done, doing (half full), not begun, waiting on the author, next. */
export function ProgressDot({ dot, className }: { dot: Dot; className?: string }) {
  const cls = cn("size-4 shrink-0", className);
  if (dot === "done") return <CircleCheck className={cn(cls, "text-[var(--accent-green)]")} aria-label={DOT_LABEL.done} />;
  return (
    <svg viewBox="0 0 16 16" className={cls} aria-label={DOT_LABEL[dot]} role="img">
      {dot === "doing" ? <>
        <circle cx="8" cy="8" r="6" fill="none" stroke="var(--warning-soft)" strokeWidth="1.5" />
        <path d="M8 2a6 6 0 0 1 0 12z" fill="var(--warning-soft)" />
      </> : dot === "wait" ? <circle cx="8" cy="8" r="6" fill="var(--primary)" />
        : dot === "next" ? <circle cx="8" cy="8" r="5.5" fill="none" stroke="var(--primary)" strokeWidth="2" />
          : <circle cx="8" cy="8" r="6" fill="none" stroke="var(--text-muted)" strokeWidth="1.5" strokeDasharray="3 3" />}
    </svg>
  );
}
export { DOT_LABEL };

/** What changing the kind of work adds to and takes from the task list. */
export function kindChange(view: Overview, to: string) {
  const from = view.brief.kind;
  return {
    added: view.tasks.filter((t) => serves(t, to) && !serves(t, from)),
    removed: view.tasks.filter((t) => !serves(t, to) && serves(t, from)),
  };
}

// The web app's one list row (ui-list-item: --ui-list-h tall, 8px inset, 12px from icon to text).
const NAV = "ui-list-item w-full shrink-0 text-left whitespace-nowrap text-[var(--nav-color)] hover:text-[var(--nav-color-hover)]";
const NAV_ON = "active text-text-bright font-medium";
const COUNT = "ml-auto shrink-0 text-fs-xs font-normal text-text-muted tabular-nums";

export function Workspace() {
  const { view, route, go, job, saved, home, refresh, openFolder, openProject, closeProject } = useApp();
  const [refreshing, setRefreshing] = useState(false);
  const [showUnused, setShowUnused] = useState(false);
  const [query, setQuery] = useState("");
  const [opened, setOpened] = useState<Record<string, boolean>>({});
  if (!view) return null;
  const kind = view.brief.kind;
  const unused = view.tasks.filter((t) => !serves(t, kind));
  const next = nextTasks(view).map((n) => n.task.name);
  const name = baseName(view.project);
  const others = (saved.recent || []).filter((r) => r.path !== view.project).slice(0, 6);

  // Groups start folded but for the one being worked on: the open task's, else the suggested next one's.
  const here = route.page === "task" ? view.tasks.find((t) => t.name === route.task)?.group : undefined;
  const suggested = view.tasks.find((t) => t.name === next[0])?.group;
  const q = query.trim().toLowerCase();
  const matches = (t: { title: string; name: string }) => !q || t.title.toLowerCase().includes(q) || t.name.includes(q);
  const isOpen = (g: string) => !!q || g === here || (opened[g] ?? g === (here || suggested));
  const toggle = (g: string) => setOpened({ ...opened, [g]: !isOpen(g) });

  const page = route.page === "task" ? <TaskPage key={route.task} name={route.task} />
    : route.page === "changes" ? <ChangesPage /> : <OverviewPage />;

  return (
    <div className="shadcn-scale flex h-full flex-col bg-background text-foreground">
      <header className="flex h-12 shrink-0 items-center gap-2 border-b border-border px-3">
        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <Button variant="elevated" className="max-w-[420px] min-w-0 gap-2 px-2">
              <ProjectMark name={name} path={view.project} className="size-5 text-[9px]" />
              <span className="truncate font-semibold">{name}</span>
              <ChevronDown className="text-text-muted" />
            </Button>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="start" className={cn(MENU_PANEL, "min-w-64")}>
            {others.length ? <div className={GROUP_LABEL}>最近</div> : null}
            {others.map((r) => (
              <DropdownMenuItem key={r.path} className={cn(itemCls(false), MENU_ITEM_STATES)} onSelect={() => openProject(r.path)} title={r.path}>
                <MenuRow icon={<ProjectMark name={r.name} path={r.path} className="size-4 text-[8px]" />} label={r.name} />
              </DropdownMenuItem>
            ))}
            {others.length ? <DropdownMenuSeparator className={MENU_SEPARATOR} /> : null}
            <DropdownMenuItem className={cn(itemCls(false), MENU_ITEM_STATES)} onSelect={openFolder}>
              <MenuRow icon={<FolderOpen className="size-4 text-text-muted" />} label="打开文件夹…" keys={{ key: "O", mod: true }} />
            </DropdownMenuItem>
            <DropdownMenuItem className={cn(itemCls(false), MENU_ITEM_STATES)} onSelect={closeProject}>
              <MenuRow icon={<LogOut className="size-4 text-text-muted" />} label="关闭项目" />
            </DropdownMenuItem>
          </DropdownMenuContent>
        </DropdownMenu>
        {view.brief.kind ? <KindSwitch /> : null}
        <HoverTip label={view.dirty.length ? `有 ${view.dirty.length} 处未提交的改动` : "工作区是干净的"}>
          <Badge variant="secondary" className="font-mono">{view.branch} · {view.head}{view.dirty.length ? " ●" : ""}</Badge>
        </HoverTip>
        <span className="flex-1" />
        <HoverTip label="重新读取项目">
          <Button variant="ghost" size="icon-sm" aria-label="刷新" disabled={refreshing}
            onClick={async () => { setRefreshing(true); await refresh(); setRefreshing(false); }}>
            <RefreshCw className={cn(refreshing && "animate-spin")} />
          </Button>
        </HoverTip>
      </header>

      <div className="flex min-h-0 flex-1">
        {/* As wide as the App's own sidebar (its border included), like the App's secondary rails. */}
        <nav aria-label="任务" className="flex w-[calc(var(--sidebar-width)-1px)] shrink-0 flex-col gap-px overflow-y-auto border-r border-border bg-[var(--bg-secondary)] p-2">
          <SearchInput value={query} onChange={setQuery} placeholder="找任务" aria-label="找任务" className="mb-[7px] shrink-0" />
          {!q ? (
            <button type="button" className={cn(NAV, route.page === "overview" && NAV_ON)} onClick={() => go({ page: "overview" })}>
              <LayoutGrid className="size-4 shrink-0" />概览
            </button>
          ) : null}
          {!q && view.dirty.length ? (
            <button type="button" className={cn(NAV, route.page === "changes" && NAV_ON)} onClick={() => go({ page: "changes" })}>
              <GitCommitHorizontal className="size-4 shrink-0" />改动<span className={COUNT}>{view.dirty.length}</span>
            </button>
          ) : null}
          {PHASES.map(([phase, label]) => {
            const tasks = view.tasks.filter((t) => t.phase === phase && serves(t, kind)).sort((a, b) => a.order - b.order);
            const shownTasks = tasks.filter(matches);
            if (!shownTasks.length) return null;
            const isDone = (name: string) => readingOf(view, name)?.state === "done";
            const counted = "tasks" in view.progress;
            const groups = [...new Set(shownTasks.map((t) => t.group))];
            return (
              <section key={phase} className="flex flex-col gap-px">
                <div className={cn(GROUP_LABEL, "px-2 pt-3")}>
                  <span className="flex-1">{label}</span>{counted ? <span className={COUNT}>{tasks.filter((t) => isDone(t.name)).length}/{tasks.length}</span> : null}
                </div>
                {groups.map((g) => {
                  const all = tasks.filter((t) => t.group === g);
                  const open = isOpen(g);
                  return (
                    <div key={g} className="flex flex-col gap-px">
                      <button type="button" aria-expanded={open} onClick={() => toggle(g)} className={NAV}>
                        <ChevronRight className={cn("size-4 shrink-0 text-text-muted transition-transform duration-200", open && "rotate-90")} />
                        <span className="min-w-0 flex-1 truncate">{g || "其他"}</span>
                        {counted && !q ? <span className={COUNT}>{all.filter((t) => isDone(t.name)).length}/{all.length}</span> : null}
                      </button>
                      {open ? (
                        // A tree: the group's tasks hang from a guide under its chevron.
                        <div className="relative flex flex-col gap-px pl-4 before:absolute before:top-0 before:bottom-0 before:left-[15.5px] before:w-px before:bg-border">
                          {shownTasks.filter((t) => t.group === g).map((t) => <TaskRow key={t.name} name={t.name} next={next} />)}
                        </div>
                      ) : null}
                    </div>
                  );
                })}
              </section>
            );
          })}
          {unused.length && !q ? (
            <section className="flex flex-col gap-px">
              <div className={cn(GROUP_LABEL, "px-2 pt-3")}>这类工作不用</div>
              <button type="button" className={NAV} aria-expanded={showUnused} onClick={() => setShowUnused(!showUnused)}>
                <ChevronRight className={cn("size-4 shrink-0 text-text-muted transition-transform duration-200", showUnused && "rotate-90")} />
                <span className="min-w-0 flex-1 truncate">{showUnused ? "收起" : "看看"}</span><span className={COUNT}>{unused.length}</span>
              </button>
              {showUnused ? (
                <div className="relative flex flex-col gap-px pl-4 before:absolute before:top-0 before:bottom-0 before:left-[15.5px] before:w-px before:bg-border">
                  {unused.map((t) => <TaskRow key={t.name} name={t.name} next={next} muted />)}
                </div>
              ) : null}
            </section>
          ) : null}
          {q && !view.tasks.some((t) => serves(t, kind) && matches(t)) ? (
            <p className="px-2 pt-3 text-fs-sm text-text-muted">没有叫这个的任务</p>
          ) : null}
        </nav>
        <main className="relative min-w-0 flex-1 overflow-hidden">{page}</main>
      </div>

      <footer className="flex h-7 shrink-0 items-center gap-4 overflow-hidden border-t border-border px-3 text-xs whitespace-nowrap text-text-muted [&>*]:shrink-0">
        {job && !job.done ? (
          <button type="button" className="flex items-center gap-1.5 text-[var(--accent-blue)]" onClick={() => go({ page: "task", task: job.task })}>
            <span className="size-2 animate-pulse rounded-full bg-[var(--accent-blue)]" />{job.title} · 检查中
          </button>
        ) : <span className="flex items-center gap-1.5"><span className="size-2 rounded-full bg-[var(--text-dim)]" />空闲</span>}
        <span className="font-mono">{view.branch} · {view.head}</span>
        {view.dirty.length ? <button type="button" className="hover:text-text-bright" onClick={() => go({ page: "changes" })}>{view.dirty.length} 处未提交</button> : null}
        <span className="flex-1" />
        <span className="min-w-0 !shrink truncate font-mono" title={view.project}>{home && view.project.startsWith(home) ? "~" + view.project.slice(home.length) : view.project}</span>
      </footer>
    </div>
  );
}

function TaskRow({ name, next, muted }: { name: string; next: string[]; muted?: boolean }) {
  const { view, route, go, job } = useApp();
  const t = view!.tasks.find((x) => x.name === name)!;
  const s = shown(t);
  const dot = muted ? undefined : dotOf(view!, t.name, next);
  const reading = readingOf(view!, t.name);
  const running = job && !job.done && job.task === t.name;
  const on = route.page === "task" && route.task === t.name;
  return (
    <button type="button" className={cn(NAV, on && NAV_ON, muted && !on && "text-text-muted")} onClick={() => go({ page: "task", task: t.name })}
      title={`${t.title} · ${running ? "正在运行" : dot ? DOT_LABEL[dot] : SHOWN[s]}`}>
      {running ? <RefreshCw className="size-4 shrink-0 animate-spin text-[var(--accent-blue)]" /> : dot ? <ProgressDot dot={dot} /> : <StateIcon state={s} />}
      <span className="min-w-0 flex-1 truncate">{t.title}</span>
      {dot === "wait" ? <span className={COUNT}>批准</span>
        : !dot && (s === "fail" || s === "blocked") ? <span className={COUNT}>{t.problems.length || ""}</span>
          : dot === "doing" && !reading?.approval && t.problems.length ? <span className={COUNT}>{t.problems.length}</span> : null}
    </button>
  );
}

/** The paper's kind of work, beside the project switcher; picking another shows what changes first. */
function KindSwitch() {
  const { view, setBrief, scan } = useApp();
  const [to, setTo] = useState("");
  if (!view) return null;
  const current = view.kinds.find((k) => k.id === view.brief.kind);
  const change = to ? kindChange(view, to) : null;
  const target = view.kinds.find((k) => k.id === to);
  const names = (list: { title: string }[]) => list.map((t) => `「${t.title}」`).join("");
  return (
    <>
      <DropdownMenu>
        <DropdownMenuTrigger asChild>
          <Button variant="elevated" className="gap-1.5 px-2.5">{current?.title || "工作类型"}<ChevronDown className="text-text-muted" /></Button>
        </DropdownMenuTrigger>
        <DropdownMenuContent align="start" className={cn(MENU_PANEL, "min-w-72")}>
          <div className={GROUP_LABEL}>这是什么工作</div>
          {view.kinds.map((k) => (
            <DropdownMenuItem key={k.id} className={cn(itemCls(false), MENU_ITEM_STATES, "h-auto py-1.5")} onSelect={() => k.id !== view.brief.kind && setTo(k.id)}>
              <span className="flex min-w-0 flex-1 flex-col">
                <span>{k.title}</span>
                <span className="text-xs text-text-muted">{k.about}</span>
              </span>
              {k.id === view.brief.kind ? <Check className="size-4 text-text-muted" /> : null}
            </DropdownMenuItem>
          ))}
        </DropdownMenuContent>
      </DropdownMenu>
      {change && target ? (
        <ConfirmDialog title={`换成「${target.title}」？`}
          description={[
            change.added.length ? `多出：${names(change.added)}。` : "",
            change.removed.length ? `收进「这类工作不用」：${names(change.removed)}，它们的文件和记录都保留，换回来原样恢复。` : "",
            !change.added.length && !change.removed.length ? "任务清单不变。" : "",
            "换完会重新盘点一次。",
          ].filter(Boolean).join("")}
          confirmLabel="换并重新盘点"
          onCancel={() => setTo("")}
          onConfirm={async () => { const kind = to; setTo(""); if (await setBrief({ kind })) scan(); }} />
      ) : null}
    </>
  );
}
