// CI checks on a branch's pull request, as the git folder status reports
// them (openprogram/state/worktree/folder_git.py `_checks_summary`), and the
// words the git menu uses for them. Plain TypeScript so tests load it.

export interface PrChecks {
  total: number;
  passed: number;
  pending: number;
  failed_count: number;
  failed: { name: string; url: string | null }[];
}

export interface PullRequestSummary {
  number?: number;
  url: string;
  checks?: PrChecks | null;
}

type Text = (en: string, zh: string) => string;

/** "Checks: 1 failing · 2 running · 5 passed", leaving out zero counts. */
export function checksLabel(checks: PrChecks, text: Text): string {
  const parts = [
    checks.failed_count ? text(`${checks.failed_count} failing`, `${checks.failed_count} 个未通过`) : "",
    checks.pending ? text(`${checks.pending} running`, `${checks.pending} 个运行中`) : "",
    checks.passed ? text(`${checks.passed} passed`, `${checks.passed} 个已通过`) : "",
  ].filter(Boolean);
  return text(`Checks: ${parts.join(" · ")}`, `检查：${parts.join(" · ")}`);
}

/** The instruction "Fix failing checks with the agent…" puts in the composer. */
export function fixChecksPrompt(pr: PullRequestSummary, text: Text): string {
  const names = (pr.checks?.failed ?? []).map((check) => check.name).join(", ");
  const more = (pr.checks?.failed_count ?? 0) > (pr.checks?.failed.length ?? 0) ? text(" and others", " 等") : "";
  const ref = pr.number ? `#${pr.number}` : pr.url;
  return text(
    `Pull request ${ref} (${pr.url}) has failing CI checks: ${names}${more}. Read their logs with gh (gh pr checks, gh run view --log-failed), fix the causes, run the relevant tests locally, and push the fix to this branch.`,
    `PR ${ref}（${pr.url}）有未通过的 CI 检查：${names}${more}。用 gh 查看日志（gh pr checks、gh run view --log-failed），修复原因，在本地跑相关测试，然后把修复推送到当前分支。`,
  );
}
