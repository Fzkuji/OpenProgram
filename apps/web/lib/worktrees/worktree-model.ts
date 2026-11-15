/**
 * Agent worktrees as the input box, the transcript and Activity read them.
 *
 * A branch can run in its own git worktree (`parent_branch`), and a
 * sub-agent spawned with `isolation: "worktree"` runs in one of its own
 * (`parent_job`, plus the branch that spawned it). The worker sends every
 * worktree of a conversation (`session_worktrees`); these helpers pick
 * the rows one branch needs. No imports: the tests load this file alone.
 */

export type WorktreeStatus = "active" | "committing" | "merged" | "discarded" | "kept" | "errored";

export interface WorktreeSummary {
  commits: number;
  files: string[];
  dirty: boolean;
  exists: boolean;
}

export interface WorktreeRow {
  id: string;
  source_repo: string;
  worktree_path: string;
  branch_name: string;
  base_ref?: string;
  base_sha?: string | null;
  status: WorktreeStatus;
  created_at?: number;
  completed_at?: number | null;
  parent_session?: string | null;
  parent_job?: string | null;
  parent_branch?: string | null;
  ephemeral?: boolean;
  merge_sha?: string | null;
  files_changed?: number;
  error?: string | null;
  summary?: WorktreeSummary;
}

const OPEN: WorktreeStatus[] = ["active", "committing", "errored"];

export function isOpen(row: WorktreeRow): boolean {
  return OPEN.includes(row.status);
}

const newestFirst = (a: WorktreeRow, b: WorktreeRow) => (b.created_at ?? 0) - (a.created_at ?? 0);

/** The worktree a branch's own turns run in, or ran in last: the open one
 *  first, else the newest finished one (for the transcript marker). A
 *  conversation-wide worktree (no branch) serves a branch that has none. */
export function branchWorktree(rows: WorktreeRow[], branchId: string | null | undefined): WorktreeRow | null {
  const own = rows.filter(row => !row.parent_job && (branchId ? row.parent_branch === branchId : !row.parent_branch)).sort(newestFirst);
  const open = own.find(isOpen);
  if (open) return open;
  if (branchId) {
    const shared = rows.filter(row => !row.parent_job && !row.parent_branch && isOpen(row)).sort(newestFirst)[0];
    if (shared) return shared;
  }
  return own[0] ?? null;
}

/** Sub-agent worktrees a branch spawned that still wait for merge or discard. */
export function subAgentWorktrees(rows: WorktreeRow[], branchId: string | null | undefined): WorktreeRow[] {
  return rows.filter(row => Boolean(row.parent_job) && isOpen(row)
    && (!branchId || !row.parent_branch || row.parent_branch === branchId)).sort(newestFirst);
}

/** How many files the worktree holds against where it started. */
export function changedFiles(row: WorktreeRow): number {
  if (row.status === "merged") return row.files_changed ?? 0;
  return row.summary?.files.length ?? 0;
}

/** The last segment of a branch or path, for a chip label. */
export function shortName(row: WorktreeRow): string {
  const parts = (row.branch_name || row.worktree_path || row.id).split("/").filter(Boolean);
  return parts[parts.length - 1] ?? row.id;
}

/** The error code a worker refusal starts with (`merge_conflict: ...`). */
export function errorCode(error: string | null | undefined): string {
  const match = /^([a-z_]+):/.exec(error ?? "");
  return match ? match[1] : "";
}

type Text = (en: string, zh: string) => string;

/** One sentence for a refusal, in the reader's words. */
export function errorText(error: string | null | undefined, text: Text): string {
  switch (errorCode(error)) {
    case "merge_conflict":
      return text("The changes conflict with the checkout. The worktree is unchanged; resolve it there and merge again.",
        "改动和工作目录冲突。worktree 保持原样，在里面解决后再合并。");
    case "not_fast_forward":
      return text("The checkout moved on; merge again to add a merge commit.", "工作目录已有新提交，再次合并会生成合并提交。");
    case "not_a_git_repo":
      return text("This conversation's folder is not in a git repository.", "这个会话的目录不在 git 仓库里。");
    case "worktree_dirty":
      return text("The worktree has uncommitted changes.", "worktree 里有未提交的修改。");
    default:
      return error ?? "";
  }
}

/** The transcript marker for a branch's worktree. */
export function markerText(row: WorktreeRow, text: Text): string {
  const files = changedFiles(row);
  const count = text(`${files} ${files === 1 ? "file" : "files"} changed`, `${files} 个文件有改动`);
  switch (row.status) {
    case "merged":
      return text(`Worktree merged into ${row.source_repo} · ${count}`, `worktree 已合并到 ${row.source_repo} · ${count}`);
    case "discarded":
      return text("Worktree discarded", "worktree 已丢弃");
    case "kept":
      return text(`Worktree kept as branch ${row.branch_name}`, `worktree 已保留为分支 ${row.branch_name}`);
    default:
      return text(`Runs in its own worktree · ${row.branch_name} · ${count}`, `在独立 worktree 中运行 · ${row.branch_name} · ${count}`);
  }
}
