/* Pure content model for the DAG node card. DOM lifecycle and positioning
 * stay in interaction/node/tooltip.ts; this module only decides what the card says. */

import { translateText } from "@/lib/i18n";
import type { GNode } from "../../types";

export type TooltipRow =
  | { kind: "kv"; key: string; value: string }
  | { kind: "block"; key: string; value: string };

export function bodyText(node: GNode): string {
  const value = node.preview ?? node.content ?? node.output ?? "";
  return typeof value === "string" ? value : String(value);
}

function estimateTokens(node: GNode): { n: number; exact: boolean } {
  const meta = (node.llm || {}) as Record<string, unknown>;
  const out = meta.output_tokens;
  if (typeof out === "number" && out > 0) return { n: out, exact: true };
  return { n: Math.max(1, Math.ceil(bodyText(node).length / 4)), exact: false };
}

export function kindLabel(node: GNode, el: Element | null): string {
  if (node.display === "root") return "root";
  if (Array.isArray((node as Record<string, unknown>).covers_ids)) {
    return "context/summary";
  }
  if ((node as Record<string, unknown>).source === "agent_spawn"
      && !node.predecessor) {
    const name = (el?.getAttribute("data-spawn-name") || "").trim();
    return name
      ? translateText(`sub-agent · ${name}`, `子 agent · ${name}`)
      : translateText("sub-agent", "子 agent");
  }
  if (node.message_from) {
    return translateText(`message · from ${node.message_from}`,
      `消息 · 来自 ${node.message_from}`);
  }
  const fn = node.function;
  if (fn === "attach") return "function call · attach";
  if (fn === "merge") return "function call · merge";
  if (node.role === "tool") {
    const name = (node.name as string | undefined) || fn;
    return name ? `function call · ${name}` : "function call";
  }
  return (node.role || "?").toString();
}

function coverageText(el: Element): string {
  if (el.getAttribute("data-failed") === "1") {
    return translateText("failed turn · archived", "失败轮 · 已留档");
  }
  if (el.getAttribute("data-ghost") === "1") {
    return translateText("folded into a summary", "已折叠进摘要");
  }
  if (el.classList.contains("out-of-context")) {
    return translateText("not in coverage", "不在覆盖内");
  }
  return translateText("✓ in coverage", "✓ 在覆盖内");
}

const STATUS_TEXT: Record<string, () => string> = {
  running: () => translateText("running", "运行中"),
  cancelled: () => translateText("cancelled", "已取消"),
  error: () => translateText("failed", "失败"),
  failed: () => translateText("failed", "失败"),
};

const kv = (key: string, value: string): TooltipRow =>
  ({ kind: "kv", key, value });
const block = (key: string, value: string): TooltipRow =>
  ({ kind: "block", key, value });

export function tooltipRows(
  node: GNode,
  el: Element | null,
  detail: boolean,
): TooltipRow[] {
  const rows: TooltipRow[] = [];
  const fn = node.function;
  const role = node.role;

  if (role === "tool") {
    if (node.name) rows.push(kv("name", String(node.name)));
    if (detail && typeof node.input === "string" && node.input) {
      rows.push(block("input", node.input));
    }
    const out = bodyText(node);
    if (out) rows.push(block("output", out));
  } else if (fn === "attach" || fn === "merge") {
    if (node.attach_manual) rows.push(kv("manual", "true"));
    if (node.attach_label) rows.push(kv("label", String(node.attach_label)));
    if (detail) {
      if (node.attach_ref) rows.push(kv("head_id", String(node.attach_ref)));
      if (node.attach_source_commit_id) {
        rows.push(kv("source_commit_id", String(node.attach_source_commit_id)));
      }
    }
    const out = bodyText(node);
    if (out) rows.push(block("output", out));
  } else {
    let tokensShown = false;
    if (role === "assistant" || role === "llm") {
      const meta = (node.llm || {}) as Record<string, unknown>;
      if (typeof meta.model === "string" && meta.model) {
        rows.push(kv("model", meta.model));
      }
      if (typeof meta.input_tokens === "number"
          || typeof meta.output_tokens === "number") {
        rows.push(kv("tokens",
          `${meta.input_tokens ?? "?"} → ${meta.output_tokens ?? "?"}`));
        tokensShown = true;
      }
    }
    // An estimate of nothing is not a number worth showing: a turn
    // cancelled before it said anything would read "1 token".
    if (!tokensShown && node.display !== "root" && bodyText(node).trim()) {
      const tokens = estimateTokens(node);
      rows.push(kv(
        tokens.exact ? "tokens" : translateText("tokens (est.)", "tokens（估）"),
        tokens.n.toLocaleString(),
      ));
    }
    const out = bodyText(node);
    if (out) rows.push(block("output", out));
  }

  if (Array.isArray((node as Record<string, unknown>).covers_ids)) {
    const record = node as Record<string, unknown>;
    const before = record.tokens_before;
    const after = record.tokens_after;
    if (typeof before === "number" && typeof after === "number") {
      rows.push(kv(translateText("tokens (est.)", "token（估算）"), `${before} → ${after}`));
    }
    const at = record.compacted_at;
    if (typeof at === "number" && at > 0) {
      const date = new Date(at > 1e12 ? at : at * 1000);
      rows.push(kv(
        translateText("compacted", "压缩于"),
        date.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }),
      ));
    }
  }

  // Only the states worth a second look get a row; a finished node's
  // status is the default and says nothing.
  const status = String(node.status || "");
  if (status === "running" || status === "cancelled" || status === "error"
      || status === "failed") {
    rows.push(kv(translateText("status", "状态"), STATUS_TEXT[status]()));
  }

  if (el) {
    const threadCount = el.getAttribute("data-thread");
    if (threadCount) {
      rows.push(kv(
        translateText("calls", "调用"),
        translateText(String(threadCount), `${threadCount} 次`),
      ));
    }
    if (detail) {
      const summaryCount = el.getAttribute("data-summary");
      if (summaryCount) {
        rows.push(kv(
          translateText("covers", "覆盖"),
          translateText(`${summaryCount} turns`, `${summaryCount} 轮`),
        ));
      }
      if (node.display !== "root") {
        rows.push(kv(translateText("context", "上下文"), coverageText(el)));
      }
    }
  }
  if (detail && node.display !== "root") {
    // The whole id: the card is where it gets copied from, and the
    // derived ids (``<reply>_t_<call>``) share their first dozen chars.
    rows.push(kv("id", String(node.id)));
  }
  return rows;
}

export function clampText(value: string, length: number): string {
  const trimmed = value.replace(/\s+/g, " ").trim();
  if (trimmed.length <= length) return trimmed;
  return trimmed.slice(0, length).replace(/\s+\S*$/, "") + "…";
}
