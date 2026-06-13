/**
 * 解析 composer 当前处于哪个 mode —— 把"输入框是哪种形态"做成一个显式的、
 * 有明确优先级的派生值，而不是散在 JSX 里的嵌套三元。
 *
 * 优先级（docs/design/ui/composer-interaction-modes.md 的冲突规则）：
 *   系统决定（question / approval，队首）> 用户主动开的 fn-form > 普通打字（idle）。
 * 系统决定撞上 fn-form 时，composer 另有一个 effect 取消 fn-form（用户主动开的
 * 丢弃无所谓）；这里的优先级保证即便 effect 还没跑，系统决定也已占住输入区。
 *
 * approval 与 ask/confirm 都来自 pendingDecisions，靠 decision.kind 区分形态。
 */

import type { PendingDecision, AgenticFunction } from "@/lib/session-store";

export type ComposerMode = "idle" | "fn-form" | "question" | "approval";

export function resolveComposerMode(
  activeDecision: PendingDecision | null,
  fnFormFunction: AgenticFunction | null,
): ComposerMode {
  if (activeDecision) {
    return activeDecision.kind === "approval" ? "approval" : "question";
  }
  if (fnFormFunction) return "fn-form";
  return "idle";
}
