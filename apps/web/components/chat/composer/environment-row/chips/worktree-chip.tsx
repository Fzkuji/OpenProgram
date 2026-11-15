"use client";

import { useEffect, useState } from "react";

import { SolarIcon } from "@/components/solar-icons";
import { HoverTip, TipBody } from "@/components/ui/tooltip";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { buttonVariants } from "@/components/ui/button";
import { ConfirmDialog } from "@/components/ui/confirm-dialog";
import {
  CHECK_SLOT_PAD,
  GROUP_LABEL,
  MENU_NOTE,
  MENU_NOTE_ERROR,
  MENU_PANEL,
  MENU_SEPARATOR,
  MENU_TAG,
  itemCls,
} from "@/components/ui/menu/menu-styles";
import { MenuCheck } from "@/components/ui/menu/menu-check";
import { useSessionStore } from "@/lib/session-store";
import { closeAllPopovers } from "@/lib/runtime-bridge/ui";
import { useTranslation } from "@/lib/i18n";
import { cn } from "@/lib/utils";
import {
  createBranchWorktree,
  discardWorktree,
  keepWorktree,
  mergeWorktree,
  useSessionWorktrees,
} from "@/lib/worktrees/session-worktrees";
import {
  branchWorktree,
  changedFiles,
  errorText,
  isOpen,
  shortName,
  subAgentWorktrees,
  type WorktreeRow,
} from "@/lib/worktrees/worktree-model";
import { useBoundChat } from "../../../top-bar/bound-chat";

/** Where the shown branch's turns run: the conversation's checkout, or a
 *  git worktree of its own (Codex's Local / Worktree choice in the input
 *  box). The menu moves the branch into a worktree; a worktree's own menu
 *  merges it back, keeps it as a git branch or discards it (a deletion,
 *  so it asks first). Sub-agents spawned with isolation list their open
 *  worktrees here too. Shown once the conversation exists and its folder
 *  is in a git repository, or while any worktree of the branch is open. */
export function WorktreeChip() {
  const { text } = useTranslation();
  const { sessionId } = useBoundChat();
  const branchId = useSessionStore((s) => (sessionId ? s.composerSettingsBySession[sessionId]?.active_branch_id ?? null : null));
  const running = useSessionStore((s) => (sessionId ? Boolean(s.runningTasks?.[sessionId]) : false));
  const { rows, sourceRepo } = useSessionWorktrees(sessionId);
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [confirmDiscard, setConfirmDiscard] = useState<WorktreeRow | null>(null);

  useEffect(() => {
    const close = () => setOpen(false);
    window.addEventListener("topbar-close-menus", close);
    return () => window.removeEventListener("topbar-close-menus", close);
  }, []);
  useEffect(() => { setError(null); }, [branchId]);

  const mine = branchWorktree(rows, branchId);
  const own = mine && isOpen(mine) ? mine : null;
  const subs = subAgentWorktrees(rows, branchId);
  if (!sessionId || (!own && !subs.length && !sourceRepo)) return null;

  const label = own ? shortName(own) : text("Checkout", "工作目录");
  const files = own ? changedFiles(own) : 0;
  const detail = own
    ? text(`${own.worktree_path}\nBranch ${own.branch_name} of ${own.source_repo}. The checkout is untouched until you merge.`,
           `${own.worktree_path}\n${own.source_repo} 的分支 ${own.branch_name}。合并前工作目录保持不动。`)
    : text(`This branch works in ${sourceRepo ?? "the conversation's folder"}. Click to give it a worktree of its own.`,
           `这个分支直接在 ${sourceRepo ?? "会话目录"} 里工作。点击可以给它一个独立 worktree。`);

  function onOpenChange(next: boolean) {
    if (next) {
      window.dispatchEvent(new Event("topbar-close-menus"));
      closeAllPopovers();
    }
    setOpen(next);
  }

  async function run(key: string, action: () => Promise<string | null>) {
    if (busy) return;
    setBusy(key);
    setError(null);
    const failed = await action();
    setBusy(null);
    if (failed) {
      setError(errorText(failed, text));
      setOpen(true);
    } else setOpen(false);
  }

  const held = running
    ? text("Available when this branch stops running.", "这个分支停止运行后可用。")
    : null;

  function worktreeItems(row: WorktreeRow) {
    const disabled = Boolean(held || busy);
    const cls = (danger = false) => cn(itemCls(false, danger), disabled && "pointer-events-none opacity-50");
    return (
      <>
        <div className={cls()} aria-disabled={disabled || undefined}
          data-tip={text("Commits what the worktree holds and merges it into the checkout.", "提交 worktree 里的改动并合并到工作目录。")}
          onClick={() => void run(`merge:${row.id}`, () => mergeWorktree(sessionId!, row.id))}>
          <SolarIcon name="git-graph" size={16} fit="fill" className="opacity-70" />
          <span className="min-w-0 flex-1 truncate">{text("Merge into checkout", "合并到工作目录")}</span>
        </div>
        <div className={cls()} aria-disabled={disabled || undefined}
          onClick={() => void run(`squash:${row.id}`, () => mergeWorktree(sessionId!, row.id, true))}>
          <SolarIcon name="git-pull-request" size={16} fit="fill" className="opacity-70" />
          <span className="min-w-0 flex-1 truncate">{text("Squash into one commit", "压成一个提交合并")}</span>
        </div>
        <div className={cls()} aria-disabled={disabled || undefined}
          data-tip={text("Leaves the folder and its git branch for you; the branch goes back to the checkout.", "保留目录和 git 分支给你，这个分支回到工作目录。")}
          onClick={() => void run(`keep:${row.id}`, () => keepWorktree(sessionId!, row.id))}>
          <SolarIcon name="archive" size={16} fit="fill" className="opacity-70" />
          <span className="min-w-0 flex-1 truncate">{text("Keep as a git branch", "保留为 git 分支")}</span>
        </div>
        <div className={cls(true)} aria-disabled={disabled || undefined}
          onClick={() => { if (!disabled) { setOpen(false); setConfirmDiscard(row); } }}>
          <SolarIcon name="trash-bin-minimalistic" size={16} fit="fill" />
          <span className="min-w-0 flex-1 truncate">{text("Discard…", "丢弃…")}</span>
        </div>
      </>
    );
  }

  const fileCount = (row: WorktreeRow) => {
    const count = changedFiles(row);
    return text(`${count} ${count === 1 ? "file" : "files"} changed`, `${count} 个文件有改动`);
  };

  return (
    <>
      <Popover open={open} onOpenChange={onOpenChange}>
        <HoverTip label={<TipBody title={own ? text(`Worktree: ${own.branch_name}`, `Worktree：${own.branch_name}`) : text("Runs in the checkout", "在工作目录中运行")}
          detail={<span className="whitespace-pre-line">{detail}</span>} />}>
          <PopoverTrigger asChild>
            <span role="button" className={cn(buttonVariants({ variant: "elevated", size: "sm" }), "status-badge cursor-pointer")} data-worktree-chip="">
              <SolarIcon name={own ? "git-graph" : "folder"} size={16} fit="fill" aria-hidden="true" />
              <span className="badge-short">{label}</span>
              {own && files ? <span className="text-text-muted">{files}</span> : null}
            </span>
          </PopoverTrigger>
        </HoverTip>
        <PopoverContent side="top" align="start" sideOffset={10} className="w-auto border-0 bg-transparent p-0 shadow-none">
          <div className={`${MENU_PANEL} w-[300px]`}>
            <div className={GROUP_LABEL}>{text("Where this branch runs", "这个分支在哪里运行")}</div>
            <div className={cn(itemCls(false), own ? "" : "cursor-default")}
              onClick={() => { if (own) setOpen(false); }}>
              <SolarIcon name="folder" size={16} fit="fill" aria-hidden="true" />
              <span className="min-w-0 flex-1 truncate">{text("Checkout", "工作目录")}</span>
              {own ? <span className={CHECK_SLOT_PAD} /> : <MenuCheck />}
            </div>
            <div className={cn(itemCls(false), (own || !sourceRepo || busy) && "cursor-default", !own && !sourceRepo && "pointer-events-none opacity-50")}
              data-tip={own ? undefined : text("The branch's next turn runs in a new git worktree; other branches keep the checkout.", "这个分支的下一轮在新的 git worktree 里运行，其他分支仍用工作目录。")}
              onClick={() => { if (!own && sourceRepo) void run("create", () => createBranchWorktree(sessionId!, branchId)); }}>
              <SolarIcon name="git-graph" size={16} fit="fill" aria-hidden="true" />
              <span className="min-w-0 flex-1 truncate">{own ? shortName(own) : text("Own worktree", "独立 worktree")}</span>
              {own ? <MenuCheck /> : <span className={CHECK_SLOT_PAD} />}
            </div>
            {own ? (
              <>
                <div className={MENU_NOTE}>{`${fileCount(own)}${own.summary?.commits ? text(` · ${own.summary.commits} commits`, ` · ${own.summary.commits} 个提交`) : ""}`}</div>
                <div className={MENU_SEPARATOR} />
                {worktreeItems(own)}
              </>
            ) : null}
            {subs.length ? (
              <>
                <div className={MENU_SEPARATOR} />
                <div className={GROUP_LABEL}>{text("Sub-agent worktrees", "子代理的 worktree")}</div>
                {subs.map((row) => (
                  <div key={row.id}>
                    <div className={cn(itemCls(false), "cursor-default")}>
                      <SolarIcon name="git-graph" size={16} fit="fill" className="opacity-70" />
                      <span className="min-w-0 flex-1 truncate">{shortName(row)}</span>
                      <span className={MENU_TAG}>{fileCount(row)}</span>
                    </div>
                    {worktreeItems(row)}
                  </div>
                ))}
              </>
            ) : null}
            {held && (own || subs.length) ? <div className={MENU_NOTE}>{held}</div> : null}
            {busy ? <div className={MENU_NOTE}>{text("Working…", "处理中…")}</div> : null}
            {error ? <div className={cn(MENU_NOTE, MENU_NOTE_ERROR)} role="alert">{error}</div> : null}
          </div>
        </PopoverContent>
      </Popover>
      {confirmDiscard ? (
        <ConfirmDialog
          destructive
          title={text("Discard this worktree?", "丢弃这个 worktree？")}
          description={text(
            `Deletes ${confirmDiscard.worktree_path} and its branch ${confirmDiscard.branch_name}, with ${fileCount(confirmDiscard)}. This can't be undone; the checkout is not touched.`,
            `会删除 ${confirmDiscard.worktree_path} 和分支 ${confirmDiscard.branch_name}（${fileCount(confirmDiscard)}），无法撤销；工作目录不受影响。`,
          )}
          confirmLabel={text("Discard", "丢弃")}
          onCancel={() => setConfirmDiscard(null)}
          onConfirm={() => {
            const row = confirmDiscard;
            setConfirmDiscard(null);
            void run(`discard:${row.id}`, () => discardWorktree(sessionId!, row.id));
          }}
        />
      ) : null}
    </>
  );
}
