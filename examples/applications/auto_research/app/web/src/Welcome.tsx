// Before a project is open: open a folder, pick a recent project or one of OpenProgram's.
import { useEffect, useState } from "react";
import { FlaskConical, FolderOpen, LayoutGrid, TriangleAlert, X } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Kbd } from "@/components/ui/kbd";
import { HoverTip } from "@/components/ui/tooltip";
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger } from "@/components/ui/dropdown-menu";
import { GROUP_LABEL, ITEM_DESC, ITEM_TITLE, ITEM_TWO_LINE, MENU_ITEM_STATES, MENU_PANEL, itemCls } from "@/components/ui/menu/menu-styles";
import { cn } from "@/lib/utils";
import { op } from "./api";
import { useApp } from "./App";
import { ago, initials, tildePath } from "./model";

const TINTS = ["bg-[color-mix(in_oklab,var(--accent-blue)_14%,transparent)] text-[var(--accent-blue)]",
  "bg-[color-mix(in_oklab,var(--accent-green)_14%,transparent)] text-[var(--accent-green)]",
  "bg-[color-mix(in_oklab,var(--accent-purple)_14%,transparent)] text-[var(--accent-purple)]",
  "bg-[color-mix(in_oklab,var(--accent-orange)_14%,transparent)] text-[var(--accent-orange)]"];
const tint = (path: string) => TINTS[[...path].reduce((n, c) => n + c.charCodeAt(0), 0) % TINTS.length];

export function ProjectMark({ name, path, className }: { name: string; path: string; className?: string }) {
  return <span className={cn("inline-flex shrink-0 items-center justify-center rounded-md font-semibold", tint(path), className)}>{initials(name)}</span>;
}

export function Welcome({ error, onDismiss }: { error: string; onDismiss(): void }) {
  const { saved, home, openFolder, openProject, forget } = useApp();
  const [projects, setProjects] = useState<{ name: string; path: string }[]>([]);
  const [opening, setOpening] = useState("");
  const recent = saved.recent || [];

  useEffect(() => { op<{ projects: { name: string; path: string }[] }>("projects").then((r) => setProjects(r.projects)).catch(() => {}); }, []);

  const open = async (path: string) => { setOpening(path); await openProject(path); setOpening(""); };

  return (
    <div className="shadcn-scale h-full overflow-y-auto bg-background text-foreground">
      <div className="mx-auto flex max-w-[960px] flex-wrap gap-14 px-6 pt-24 pb-12">
        <div className="flex min-w-0 flex-[1_1_300px] flex-col gap-8">
          <div className="flex flex-col gap-3">
            <span className="inline-flex size-12 items-center justify-center rounded-xl bg-foreground text-background"><FlaskConical className="size-6" /></span>
            <h1 className="text-[28px] font-semibold tracking-tight text-text-bright">Auto Research</h1>
            <p className="text-[15px] leading-6 text-text-muted">按任务和 agent 聊着写论文：它在论文仓库里改文件，图、PDF 和文档就在聊天上方预览。</p>
          </div>

          <div className="flex flex-col gap-1">
            <div className={cn(GROUP_LABEL, "px-2.5 pb-1.5")}>开始</div>
            <Button size="lg" className="h-10 justify-start gap-3 px-3 text-[15px]" onClick={openFolder}>
              <FolderOpen className="size-[18px]" />
              <span className="flex-1 text-left">打开文件夹…</span>
              <Kbd className="bg-primary-foreground/15 text-primary-foreground">⌘O</Kbd>
            </Button>
            <DropdownMenu>
              <DropdownMenuTrigger asChild>
                <Button variant="ghost" size="lg" className="h-10 justify-start gap-3 px-3 text-[15px]" disabled={!projects.length}>
                  <LayoutGrid className="size-[18px] text-text-muted" />
                  <span className="flex-1 text-left">从 OpenProgram 项目中选</span>
                  {projects.length ? <span className="text-xs text-text-muted">{projects.length}</span> : null}
                </Button>
              </DropdownMenuTrigger>
              <DropdownMenuContent align="start" className={cn(MENU_PANEL, "max-w-[420px]")}>
                {projects.map((p) => (
                  <DropdownMenuItem key={p.path} className={cn(itemCls(false), ITEM_TWO_LINE, MENU_ITEM_STATES)} onSelect={() => open(p.path)}>
                    <ProjectMark name={p.name} path={p.path} className="mt-0.5 size-5 text-[9px]" />
                    <span className="min-w-0 flex-1"><span className={ITEM_TITLE}>{p.name}</span><span className={cn(ITEM_DESC, "truncate whitespace-nowrap font-mono")}>{tildePath(p.path, home)}</span></span>
                  </DropdownMenuItem>
                ))}
              </DropdownMenuContent>
            </DropdownMenu>
          </div>

          <p className="text-[13px] leading-5 text-text-muted">文件夹需要是 Git 仓库。agent 直接改项目里的文件，改动随时能用 Git 看和撤回。</p>
        </div>

        <div className="flex min-w-0 flex-[1.4_1_420px] flex-col gap-2">
          <div className={cn(GROUP_LABEL, "px-2.5 pb-1.5")}>最近</div>

          {error ? (
            <div role="alert" className="flex items-center gap-3 rounded-lg border border-[color-mix(in_oklab,var(--accent-orange)_40%,transparent)] bg-[color-mix(in_oklab,var(--accent-orange)_10%,transparent)] px-3 py-2.5 text-[13px] text-text-bright">
              <TriangleAlert className="size-4 shrink-0 text-[var(--accent-orange)]" />
              <span className="flex-1">{error}</span>
              <Button variant="ghost" size="sm" onClick={() => { onDismiss(); openFolder(); }}>换一个文件夹</Button>
            </div>
          ) : null}

          {recent.length ? (
            <div className="overflow-hidden rounded-xl border border-border bg-card">
              {recent.map((r) => (
                <div key={r.path} className="group relative border-b border-border last:border-b-0">
                  <button type="button" onClick={() => open(r.path)} disabled={!!opening}
                    className="flex w-full items-center gap-3.5 px-4 py-3.5 pr-12 text-left transition-colors hover:bg-[var(--bg-hover)] disabled:opacity-60">
                    <ProjectMark name={r.name} path={r.path} className="size-9 text-[14px]" />
                    <span className="flex min-w-0 flex-1 flex-col gap-0.5">
                      <span className="truncate text-[15px] font-medium text-text-bright">{r.name}</span>
                      <span className="truncate font-mono text-xs text-text-muted">{tildePath(r.path, home)}</span>
                    </span>
                    <span className="flex shrink-0 flex-col items-end gap-1">
                      {r.branch ? <Badge variant="secondary" className="font-mono">{r.branch} · {r.head}</Badge> : null}
                      <span className="text-xs text-text-muted">{opening === r.path ? "正在打开…" : ago(r.at)}</span>
                    </span>
                  </button>
                  <HoverTip label="从列表里移除（不删文件）">
                    <Button variant="ghost" size="icon-xs" aria-label={`从最近列表移除 ${r.name}`} onClick={() => forget(r.path)}
                      className="absolute top-1/2 right-3 -translate-y-1/2 opacity-0 group-hover:opacity-100 focus-visible:opacity-100">
                      <X />
                    </Button>
                  </HoverTip>
                </div>
              ))}
            </div>
          ) : (
            <div className="flex flex-col items-start gap-3 rounded-xl border border-dashed border-border px-5 py-8 text-sm text-text-muted">
              还没有打开过项目。打开论文所在的文件夹，它会出现在这里。
              <Button variant="elevated" onClick={openFolder}><FolderOpen />打开文件夹…</Button>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
