"use client";

/**
 * GitChip — the git half of a folder pill in the composer's environment row.
 *
 * Rendered inside its folder's pill (FOLDER_PILL: the project chip, or one
 * working folder chip) as the right-hand segment when that folder is
 * inside a Git checkout. The segment shows the branch (last path segment)
 * and the uncommitted +N −N as a diff badge, which the row's tightest
 * squeeze level reduces to a half-green, half-red dot; its menu:
 *
 *   * opens the uncommitted changes in Review (workspace scope),
 *   * switches or creates a branch — in place when the folder is clean,
 *     in a new worktree beside the repository when it has uncommitted
 *     changes, so nothing in the folder moves (`onUseFolder` decides
 *     what moving onto a worktree means: a draft re-points its project,
 *     a working folder is replaced, a frozen main folder adds the
 *     worktree as an extra working folder),
 *   * when an in-place switch would overwrite changes, explains which
 *     files and offers the worktree route or carrying the changes over
 *     (stash, switch, pop) instead of showing git's refusal raw,
 *   * creates a pull request with `gh` (push + `gh pr create --fill`),
 *     or hands "commit and open a PR" to the agent when there are
 *     uncommitted changes, or opens the branch's existing PR.
 *
 * State is read on demand — on mount, when the menu opens, when a turn
 * ends, when the window regains focus and after any pill's mutation —
 * never polled. Folders sharing one checkout show a single pill
 * (git-chip-registry).
 */
import { useCallback, useEffect, useId, useRef, useState } from "react";

import { SolarIcon } from "@/components/solar-icons";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { HoverTip, TipBody } from "@/components/ui/tooltip";
import { useTranslation } from "@/lib/i18n";
import { wsRequest } from "@/lib/net/ws/ws-request";
import { closeAllPopovers } from "@/lib/runtime-bridge/ui";
import { useSessionStore } from "@/lib/session-store";
import { useCenterTabs } from "@/lib/tabs/center-tabs-store";
import { desktopBridge } from "@/lib/desktop/bridge/bridge-api";
import { cn } from "@/lib/utils";
import { useBoundChat } from "../bound-chat";
import { notifyGitChanged, useGitRepoClaim } from "./git-chip-registry";
import { GROUP_LABEL, MENU_PANEL, MENU_SEPARATOR, MENU_TAG, itemCls } from "@/components/ui/menu/menu-styles";
import { checksLabel, fixChecksPrompt, type PrChecks } from "@/lib/git/pr-checks";

/** A refusal from the worker, with the structured bits the menu acts on. */
interface GitMenuError {
  message: string;
  /** "overwrite": a switch would clobber `files`; "stash_conflict": carried changes stayed in the stash. */
  code?: string;
  files?: string[];
  /** git's own output, shown on request. */
  detail?: string;
  /** The switch that was refused, so the error card can retry it another way. */
  attempt?: { branch: string; create: boolean };
}

type BranchMode = "switch" | "worktree";

export interface GitWorktree {
  path: string;
  branch: string | null;
  detached: boolean;
  is_main: boolean;
  is_current: boolean;
}

export interface GitFolderStatus {
  path: string;
  is_repo: boolean;
  error?: string;
  root?: string;
  repo_name?: string;
  branch?: string | null;
  head?: string | null;
  upstream?: string | null;
  ahead?: number;
  behind?: number;
  changes?: { files: number; untracked: number; conflicts: number; insertions: number; deletions: number };
  is_worktree?: boolean;
  worktrees?: GitWorktree[];
  default_branch?: string | null;
  has_remote?: boolean;
  gh_available?: boolean;
  branches?: string[];
  pr?: {
    number?: number; url: string; state?: string; title?: string; isDraft?: boolean;
    checks?: PrChecks | null;
  } | null;
}



function baseName(path: string): string {
  const parts = path.split("/").filter(Boolean);
  return parts[parts.length - 1] ?? path;
}

function openUrl(url: string) {
  const bridge = desktopBridge();
  if (bridge) bridge.openExternal(url);
  else window.open(url, "_blank", "noopener,noreferrer");
}

export function GitChip({
  path,
  order,
  onUseFolder,
  useFolderLabel,
}: {
  /** The folder this pill describes; null renders nothing. */
  path: string | null;
  /** Claim order among pills on one checkout: project 0, working folders 1+. */
  order: number;
  /** Move this folder slot onto another worktree path. */
  onUseFolder?: (path: string) => void | Promise<void>;
  /** Menu wording for onUseFolder (e.g. "Add as working folder"). */
  useFolderLabel?: { en: string; zh: string };
}) {
  const { text } = useTranslation();
  const id = useId();
  const { sessionId, chatKey } = useBoundChat();
  const openReviewTab = useCenterTabs((s) => s.openReviewTab);
  const running = useSessionStore((s) => (sessionId ? Boolean(s.runningTasks?.[sessionId]) : false));
  const [status, setStatus] = useState<GitFolderStatus | null>(null);
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<GitMenuError | null>(null);
  const [showDetail, setShowDetail] = useState(false);
  const [query, setQuery] = useState("");
  const generation = useRef(0);

  const refresh = useCallback(async (full = false) => {
    if (!path) return;
    const mine = ++generation.current;
    const data = await wsRequest<{ path: string; status: GitFolderStatus }>(
      "git_folder_status",
      { path, include_branches: full, include_pr: full },
      "git_folder_status",
      (d) => d.path === path,
      full ? 15000 : 6000,
    );
    if (mine !== generation.current || !data?.status) return;
    setStatus((prev) => (
      !full && prev?.root === data.status.root
        ? { ...data.status, branches: prev?.branches, pr: prev?.pr }
        : data.status
    ));
  }, [path]);

  useEffect(() => {
    setStatus(null);
    void refresh();
  }, [refresh]);

  // A turn just ended: the agent may have edited, committed or switched.
  const wasRunning = useRef(running);
  useEffect(() => {
    if (wasRunning.current && !running) void refresh();
    wasRunning.current = running;
  }, [running, refresh]);

  useEffect(() => {
    const onChanged = (event: Event) => {
      const root = (event as CustomEvent<{ root?: string | null }>).detail?.root;
      if (!root || root === status?.root) void refresh(open);
    };
    const onFocus = () => void refresh(open);
    const onProject = () => void refresh();
    window.addEventListener("op:git-changed", onChanged);
    window.addEventListener("focus", onFocus);
    window.addEventListener("project-changed", onProject);
    return () => {
      window.removeEventListener("op:git-changed", onChanged);
      window.removeEventListener("focus", onFocus);
      window.removeEventListener("project-changed", onProject);
    };
  }, [refresh, open, status?.root]);

  useEffect(() => {
    const close = () => setOpen(false);
    window.addEventListener("topbar-close-menus", close);
    return () => window.removeEventListener("topbar-close-menus", close);
  }, []);

  const visible = useGitRepoClaim(id, status?.is_repo ? status.root ?? null : null, order);

  function onOpenChange(next: boolean) {
    if (next) {
      window.dispatchEvent(new Event("topbar-close-menus"));
      closeAllPopovers();
      setError(null);
      setShowDetail(false);
      setQuery("");
      void refresh(true);
    }
    setOpen(next);
  }

  function fail(err: GitMenuError) {
    setShowDetail(false);
    setError(err);
  }

  async function run<T extends { ok: boolean; error?: string | null; code?: string; files?: string[]; detail?: string }>(
    label: string,
    action: string,
    payload: Record<string, unknown>,
    responseType: string,
    timeoutMs: number,
  ): Promise<T | null> {
    setBusy(label);
    setError(null);
    const reply = await wsRequest<T & { path: string }>(
      action, { path, ...payload }, responseType, (d) => d.path === path, timeoutMs,
    );
    setBusy(null);
    if (!reply) {
      fail({ message: text("No reply from the worker.", "后端没有响应。") });
      return null;
    }
    if (!reply.ok) {
      fail({
        message: reply.error || text("The git command failed.", "git 命令失败。"),
        code: reply.code,
        files: reply.files,
        detail: reply.detail,
        attempt: action === "git_switch_branch" ? { branch: String(payload.branch), create: Boolean(payload.create) } : undefined,
      });
      return null;
    }
    return reply;
  }

  async function switchBranch(branch: string, create: boolean, carry = false) {
    const reply = await run<{ ok: boolean; error?: string; status?: GitFolderStatus }>(
      create ? "create" : branch, "git_switch_branch", { branch, create, carry }, "git_switch_branch_result", 60000,
    );
    if (reply?.status) {
      setStatus((prev) => ({ ...reply.status!, pr: undefined, branches: reply.status!.branches ?? prev?.branches }));
      setQuery("");
      notifyGitChanged(reply.status.root);
      void refresh(true);
    }
  }

  async function createWorktree(branch: string) {
    const reply = await run<{ ok: boolean; error?: string; worktree?: GitFolderStatus }>(
      "worktree", "git_create_worktree", { branch }, "git_worktree_created", 120000,
    );
    if (reply?.worktree?.path) {
      notifyGitChanged(status?.root);
      await moveToFolder(reply.worktree.path);
    }
  }

  async function moveToFolder(target: string) {
    if (!onUseFolder) return;
    setBusy("use");
    try {
      await onUseFolder(target);
      setOpen(false);
    } catch (err) {
      fail({ message: err instanceof Error ? err.message : String(err) });
    }
    setBusy(null);
  }

  // Clean folder: switch in place. Uncommitted changes: open the branch in
  // a worktree beside the repository instead, so nothing here moves.
  const dirty = (status?.changes?.files ?? 0) > 0;
  const canWorktree = Boolean(onUseFolder);
  const mode: BranchMode = canWorktree && dirty ? "worktree" : "switch";

  /** Create-or-switch for `branch` under the current mode. */
  function goToBranch(branch: string, create: boolean) {
    if (mode === "worktree") void createWorktree(branch);
    else void switchBranch(branch, create);
  }

  async function createPr() {
    const reply = await run<{ ok: boolean; error?: string; url?: string }>(
      "pr", "git_create_pr", {}, "git_pr_created", 180000,
    );
    if (reply?.url) {
      openUrl(reply.url);
      void refresh(true);
    }
  }

  function askAgentForPr() {
    const store = useSessionStore.getState();
    const where = status?.repo_name ? ` in ${status.repo_name}` : "";
    store.setComposerInputFor(
      chatKey ?? store.activeChatKey,
      text(
        `Commit the current changes${where} on a feature branch with a clear message, push it, and open a pull request with gh.`,
        `把${status?.repo_name ? ` ${status.repo_name} 中` : ""}当前的修改提交到一个功能分支（写清楚提交信息），推送后用 gh 创建 pull request。`,
      ),
    );
    store.focusComposer();
    setOpen(false);
  }

  /** Ask the agent to fix the pull request's failing checks (not sent). */
  function askAgentToFixChecks(pr: NonNullable<GitFolderStatus["pr"]>) {
    const store = useSessionStore.getState();
    store.setComposerInputFor(chatKey ?? store.activeChatKey, fixChecksPrompt(pr, text));
    store.focusComposer();
    setOpen(false);
  }

  const allBranches = status?.branches ?? (status?.branch ? [status.branch] : []);
  const needle = query.trim().toLowerCase();
  const branches = needle ? allBranches.filter((b) => b.toLowerCase().includes(needle)) : allBranches;

  if (!path || !status?.is_repo || !visible) return null;

  const changes = status.changes ?? { files: 0, untracked: 0, conflicts: 0, insertions: 0, deletions: 0 };
  const branchLabel = status.branch ?? status.head ?? text("detached", "游离");
  // The pill shows the last path segment ("claude/foo" → "foo"); the tip has it all.
  const shortBranch = branchLabel.split("/").filter(Boolean).pop() ?? branchLabel;
  const typed = query.trim();
  const typedExists = Boolean(typed) && (status.branches ?? []).includes(typed);
  const worktreeByBranch = new Map(
    (status.worktrees ?? []).filter((w) => w.branch && !w.is_current).map((w) => [w.branch!, w]),
  );
  const prBlocked = !status.gh_available
    ? text("Install and sign in to the GitHub CLI (gh) to create pull requests.", "安装并登录 GitHub CLI（gh）后才能创建 PR。")
    : !status.has_remote
      ? text("This repository has no origin remote.", "此仓库没有 origin 远端。")
      : !status.branch
        ? text("Check out a branch first.", "先切换到一个分支。")
        : status.branch === status.default_branch
          ? text(`Switch to a feature branch; you're on ${status.default_branch}.`, `当前在 ${status.default_branch}，请先切到功能分支。`)
          : null;
  const openPr = status.pr && String(status.pr.state ?? "").toUpperCase() === "OPEN" ? status.pr : null;
  const hasLines = Boolean(changes.insertions || changes.deletions);

  return (
    <>
    {/* Its own element, so hovering either segment never repaints the line. */}
    <span className="folder-pill-divider" aria-hidden="true" />
    <Popover open={open} onOpenChange={onOpenChange}>
      <HoverTip
        label={
          <TipBody
            title={`${status.repo_name ?? baseName(path)} · ${branchLabel}`}
            detail={
              <>
                {changes.files
                  ? text(
                      `${changes.files} uncommitted ${changes.files === 1 ? "file" : "files"}${hasLines ? ` · +${changes.insertions} −${changes.deletions}` : ""}`,
                      `${changes.files} 个未提交文件${hasLines ? ` · +${changes.insertions} −${changes.deletions}` : ""}`,
                    )
                  : text("No uncommitted changes", "没有未提交的修改")}
                {status.is_worktree ? <><br />{text(`Worktree · ${status.root}`, `Worktree · ${status.root}`)}</> : null}
                <br />
                {text("Click for branches, worktrees and pull requests", "点击切换分支、worktree 或创建 PR")}
              </>
            }
          />
        }
      >
        <PopoverTrigger asChild>
          <button
            type="button"
            className={cn("folder-pill-seg git-seg", status.is_worktree && "git-seg-worktree")}
            aria-label={text(`Git: ${branchLabel}`, `Git：${branchLabel}`)}
          >
            <SolarIcon name="git-branch" size={16} fit="fill" className="git-icon" />
            <span className="git-seg-label">{shortBranch}</span>
            {hasLines ? (
              <>
                <span className="diff-badge git-seg-badge">
                  <span className="is-add"><span>+</span>{changes.insertions}</span>
                  <span className="is-del"><span>−</span>{changes.deletions}</span>
                </span>
                <span className="git-seg-dot" aria-hidden="true" />
              </>
            ) : changes.files ? (
              // Only binary / line-less changes: a file count, not "+0 −0".
              <>
                <span className="diff-badge git-seg-badge">
                  <span className="is-files">{text(`${changes.files} ${changes.files === 1 ? "file" : "files"}`, `${changes.files} 个文件`)}</span>
                </span>
                <span className="git-seg-dot is-files" aria-hidden="true" />
              </>
            ) : null}
          </button>
        </PopoverTrigger>
      </HoverTip>
      <PopoverContent side="top" align="start" sideOffset={10} className="w-auto border-0 bg-transparent p-0 shadow-none">
        <div className={`${MENU_PANEL} git-menu w-[320px]`}>
          <div className={GROUP_LABEL}>
            <span className="min-w-0 flex-1 truncate">{status.repo_name}</span>
            {status.is_worktree ? <span className={MENU_TAG}>{text("worktree", "worktree")}</span> : null}
          </div>

          {changes.files ? (
            <div
              className={itemCls(false)}
              data-tip={sessionId ? undefined : text("Send a message first to review changes.", "发送消息后才能审阅修改。")}
              onClick={() => sessionId && (openReviewTab(sessionId, undefined, "workspace"), setOpen(false))}
            >
              <SolarIcon name="file-text" size={16} fit="fill" className="opacity-70" />
              <span className="min-w-0 flex-1 truncate">
                {text(`${changes.files} uncommitted ${changes.files === 1 ? "file" : "files"}`, `${changes.files} 个未提交文件`)}
              </span>
              {hasLines ? (
                <span className="diff-badge">
                  <span className="is-add"><span>+</span>{changes.insertions}</span>
                  <span className="is-del"><span>−</span>{changes.deletions}</span>
                </span>
              ) : null}
            </div>
          ) : (
            <div className="git-menu-note">{text("No uncommitted changes", "没有未提交的修改")}</div>
          )}
          {status.upstream && (status.ahead || status.behind) ? (
            <div className="git-menu-note">
              {text(`${status.ahead} ahead, ${status.behind} behind ${status.upstream}`, `比 ${status.upstream} 领先 ${status.ahead}、落后 ${status.behind}`)}
            </div>
          ) : null}

          <div className={MENU_SEPARATOR} />
          <div className={GROUP_LABEL}>
            <span className="min-w-0 flex-1 truncate">{text("Branch", "分支")}</span>
            {mode === "worktree" ? (
              <span className="shrink-0" data-tip={text("This folder has uncommitted changes, so a branch opens in a new folder beside the repository; nothing here moves.", "这里有未提交的修改，所以分支会在仓库旁边的新目录里打开，这个目录的文件保持不动。")}>
                {text("opens in a new worktree", "在新 worktree 里打开")}
              </span>
            ) : null}
          </div>
          {mode === "worktree" && useFolderLabel ? (
            <div className="git-menu-note">{text(useFolderLabel.en, useFolderLabel.zh)}</div>
          ) : null}
          <input
            className="git-menu-input"
            value={query}
            placeholder={text("Find or create a branch…", "查找或新建分支…")}
            onChange={(e) => setQuery(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && typed && !busy) {
                e.preventDefault();
                const elsewhere = worktreeByBranch.get(typed);
                if (elsewhere) void moveToFolder(elsewhere.path);
                else if (typed !== status.branch) goToBranch(typed, !typedExists);
              }
            }}
            spellCheck={false}
            autoComplete="off"
          />
          <div className="git-menu-branches">
            {branches.slice(0, 40).map((b) => {
              const elsewhere = worktreeByBranch.get(b);
              const current = b === status.branch;
              // Right-hand tag only where it adds something: the current
              // branch, and branches already living in another worktree.
              const tag = current ? text("current", "当前") : elsewhere ? text("worktree", "worktree") : null;
              const hint = elsewhere
                ? text(`Already checked out in ${elsewhere.path}; opens that folder`, `已在 ${elsewhere.path} 检出，点击去那个目录`)
                : current
                  ? text("The branch this folder is on", "这个目录当前所在的分支")
                  : mode === "worktree"
                    ? text("Opens in a new worktree beside the repository", "在仓库旁边新建 worktree 打开")
                    : text("Switches this folder to the branch", "把这个目录切到此分支");
              return (
                <div
                  key={b}
                  className={cn(itemCls(false), current && "cursor-default")}
                  data-tip={hint}
                  onClick={() => {
                    if (busy || current) return;
                    if (elsewhere) void moveToFolder(elsewhere.path);
                    else goToBranch(b, false);
                  }}
                >
                  <span className="min-w-0 flex-1 truncate">{b}</span>
                  {tag ? <span className={MENU_TAG}>{tag}</span> : null}
                </div>
              );
            })}
            {status.branches === undefined ? (
              <div className="git-menu-note">{text("Loading branches…", "正在读取分支…")}</div>
            ) : null}
          </div>
          {typed && !typedExists ? (
            <div className={itemCls(false)} onClick={() => !busy && goToBranch(typed, true)}>
              <SolarIcon name="add-circle" size={16} fit="fill" className="opacity-70" />
              <span className="min-w-0 flex-1 truncate">
                {mode === "worktree"
                  ? text(`Create branch “${typed}” in a new worktree`, `在新 worktree 上新建分支“${typed}”`)
                  : text(`Create branch “${typed}”`, `新建分支“${typed}”`)}
              </span>
            </div>
          ) : null}

          <div className={MENU_SEPARATOR} />
          {openPr ? (
            <div className={itemCls(false)} data-tip={openPr.title} onClick={() => openUrl(openPr.url)}>
              <SolarIcon name="square-arrow-right-up" size={16} fit="fill" className="opacity-70" />
              <span className="min-w-0 flex-1 truncate">
                {text(`View pull request #${openPr.number ?? ""}`, `查看 PR #${openPr.number ?? ""}`)}
              </span>
            </div>
          ) : (
            <div
              className={cn(itemCls(false), prBlocked && "pointer-events-auto cursor-default opacity-50")}
              data-tip={prBlocked ?? text("Pushes this branch and opens a PR from its commits.", "推送当前分支，并用它的提交创建 PR。")}
              onClick={() => !prBlocked && !busy && void createPr()}
            >
              <SolarIcon name="git-pull-request" size={16} fit="fill" className="opacity-70" />
              <span className="min-w-0 flex-1 truncate">{text("Create pull request", "创建 PR")}</span>
            </div>
          )}
          {openPr?.checks ? (
            <div className="git-menu-note" aria-live="polite">{checksLabel(openPr.checks, text)}</div>
          ) : null}
          {openPr?.checks?.failed_count ? (
            <div className={itemCls(false)} onClick={() => askAgentToFixChecks(openPr)}>
              <SolarIcon name="chat-round-dots" size={16} fit="fill" className="opacity-70" />
              <span className="min-w-0 flex-1 truncate">{text("Fix failing checks with the agent…", "让 agent 修复未通过的检查…")}</span>
            </div>
          ) : null}
          {changes.files && !openPr ? (
            <div className={itemCls(false)} onClick={askAgentForPr}>
              <SolarIcon name="chat-round-dots" size={16} fit="fill" className="opacity-70" />
              <span className="min-w-0 flex-1 truncate">{text("Commit & open PR with the agent…", "让 agent 提交并创建 PR…")}</span>
            </div>
          ) : null}
          {prBlocked && !openPr ? <div className="git-menu-note">{prBlocked}</div> : null}

          {busy ? <div className="git-menu-note">{text("Working…", "处理中…")}</div> : null}
          {error ? (
            <div className="git-menu-error" role="alert">
              <div className="git-menu-error-title">
                {error.code === "overwrite" && error.attempt
                  ? text(
                    `Can't switch to ${error.attempt.branch}: ${error.files?.length ?? 0} ${error.files?.length === 1 ? "file" : "files"} here would be overwritten`,
                    `切不到 ${error.attempt.branch}：这个目录里有 ${error.files?.length ?? 0} 个文件的修改会被覆盖`,
                  )
                  : error.code === "stash_conflict"
                    ? text(
                      "Switched, but the carried changes conflict. They're kept in the latest stash.",
                      "已切换，但带过来的修改有冲突，仍保存在最新的 stash 里。",
                    )
                    : error.message}
              </div>
              {error.code === "overwrite" && error.files?.length ? (
                <ul className="git-menu-error-files">
                  {error.files.slice(0, 6).map((f) => <li key={f}>{f}</li>)}
                  {error.files.length > 6 ? <li>{text(`and ${error.files.length - 6} more`, `还有 ${error.files.length - 6} 个`)}</li> : null}
                </ul>
              ) : null}
              {error.code === "overwrite" && error.attempt ? (
                <div className="git-menu-error-actions">
                  {canWorktree ? (
                    <button type="button" className="git-menu-error-btn" onClick={() => void createWorktree(error.attempt!.branch)}>
                      {text("Open in a worktree", "改为开 worktree")}
                    </button>
                  ) : null}
                  <button type="button" className="git-menu-error-btn" onClick={() => void switchBranch(error.attempt!.branch, error.attempt!.create, true)}>
                    {text("Carry the changes over", "带着修改切过去")}
                  </button>
                </div>
              ) : null}
              {error.detail ? (
                <button type="button" className="git-menu-error-more" onClick={() => setShowDetail((v) => !v)}>
                  {showDetail ? text("Hide git output", "收起 git 输出") : text("Show git output", "查看 git 输出")}
                </button>
              ) : null}
              {error.detail && showDetail ? <pre className="git-menu-error-detail">{error.detail}</pre> : null}
            </div>
          ) : null}
        </div>
      </PopoverContent>
    </Popover>
    </>
  );
}
