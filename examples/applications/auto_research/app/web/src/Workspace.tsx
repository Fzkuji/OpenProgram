// An open project: the project switcher, the task list, the page in the middle and the status bar.
import { ChevronDown, CircleAlert, CircleCheck, Circle, FolderOpen, LayoutGrid, Lock, LogOut, RefreshCw } from "lucide-react";
import { useState } from "react";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { HoverTip } from "@/components/ui/tooltip";
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuSeparator, DropdownMenuTrigger } from "@/components/ui/dropdown-menu";
import { MenuRow } from "@/components/ui/menu/menu-parts";
import { GROUP_LABEL, MENU_ITEM_STATES, MENU_PANEL, MENU_SEPARATOR, itemCls } from "@/components/ui/menu/menu-styles";
import { cn } from "@/lib/utils";
import { useApp } from "./App";
import { PHASES, SHOWN, baseName, shown, type Shown } from "./model";
import { ProjectMark } from "./Welcome";
import { OverviewPage } from "./Overview";
import { TaskPage } from "./TaskPage";

export function StateIcon({ state, className }: { state: Shown; className?: string }) {
  const cls = cn("size-4 shrink-0", className);
  if (state === "pass") return <CircleCheck className={cn(cls, "text-[var(--accent-green)]")} aria-label={SHOWN.pass} />;
  if (state === "fail") return <CircleAlert className={cn(cls, "text-[var(--accent-red)]")} aria-label={SHOWN.fail} />;
  if (state === "blocked") return <Lock className={cn(cls, "text-[var(--accent-orange)]")} aria-label={SHOWN.blocked} />;
  return <Circle className={cn(cls, "text-text-muted [stroke-dasharray:3_3]")} aria-label={SHOWN.never} />;
}

const NAV = "flex h-8 w-full items-center gap-2.5 rounded-md px-2.5 text-left text-sm transition-colors hover:bg-[var(--bg-hover)]";
const NAV_ON = "bg-accent text-text-bright font-medium";

export function Workspace() {
  const { view, route, go, job, saved, home, refresh, openFolder, openProject, closeProject } = useApp();
  const [refreshing, setRefreshing] = useState(false);
  if (!view) return null;
  const name = baseName(view.project);
  const others = (saved.recent || []).filter((r) => r.path !== view.project).slice(0, 6);

  const page = route.page === "task" ? <TaskPage key={route.task} name={route.task} /> : <OverviewPage />;

  return (
    <div className="shadcn-scale flex h-full flex-col bg-background text-foreground">
      <header className="flex h-12 shrink-0 items-center gap-2 border-b border-border px-3">
        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <Button variant="secondary" className="max-w-[420px] min-w-0 gap-2 px-2">
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
        <nav aria-label="任务" className="flex w-[248px] shrink-0 flex-col gap-0.5 overflow-y-auto border-r border-border bg-[var(--bg-secondary)] px-2 py-3">
          <button type="button" className={cn(NAV, route.page === "overview" && NAV_ON)} onClick={() => go({ page: "overview" })}>
            <LayoutGrid className="size-4 text-text-muted" />概览
          </button>
          {PHASES.map(([phase, label]) => {
            const tasks = view.tasks.filter((t) => t.phase === phase);
            if (!tasks.length) return null;
            return (
              <div key={phase} className="flex flex-col gap-0.5">
                <div className={cn(GROUP_LABEL, "px-2.5 pt-4 pb-1")}>{label}</div>
                {tasks.map((t) => {
                  const s = shown(t);
                  const running = job && !job.done && job.task === t.name;
                  const on = route.page === "task" && route.task === t.name;
                  return (
                    <button key={t.name} type="button" className={cn(NAV, on && NAV_ON)} onClick={() => go({ page: "task", task: t.name })}
                      title={`${t.title} · ${running ? "正在运行" : SHOWN[s]}`}>
                      {running ? <RefreshCw className="size-4 shrink-0 animate-spin text-[var(--accent-blue)]" /> : <StateIcon state={s} />}
                      <span className="flex-1 truncate">{t.title}</span>
                      {s === "fail" || s === "blocked" ? <span className="text-xs text-text-muted">{t.problems.length || ""}</span> : null}
                    </button>
                  );
                })}
              </div>
            );
          })}
        </nav>
        <main className="relative min-w-0 flex-1 overflow-hidden">{page}</main>
      </div>

      <footer className="flex h-7 shrink-0 items-center gap-4 border-t border-border px-3 text-xs text-text-muted">
        {job && !job.done ? (
          <button type="button" className="flex items-center gap-1.5 text-[var(--accent-blue)]" onClick={() => go({ page: "task", task: job.task })}>
            <span className="size-2 animate-pulse rounded-full bg-[var(--accent-blue)]" />{job.title} · 检查中
          </button>
        ) : <span className="flex items-center gap-1.5"><span className="size-2 rounded-full bg-[var(--text-dim)]" />空闲</span>}
        <span className="font-mono">{view.branch} · {view.head}</span>
        {view.dirty.length ? <span>{view.dirty.length} 处未提交</span> : null}
        <span className="flex-1" />
        <span className="truncate font-mono" title={view.project}>{home && view.project.startsWith(home) ? "~" + view.project.slice(home.length) : view.project}</span>
      </footer>
    </div>
  );
}
