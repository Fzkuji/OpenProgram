/* Pure content model for the DAG node card. DOM lifecycle and positioning
 * stay in tooltip.ts; this module only decides what the card says. */

import { translateText } from "@/lib/i18n";
import type { GNode } from "../types";

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
    if (!tokensShown && node.display !== "root") {
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
      rows.push(kv("tokens", `${before} → ${after}`));
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
    rows.push(kv("id", String(node.id).slice(0, 12)));
  }
  return rows;
}

export function clampText(value: string, length: number): string {
  const trimmed = value.replace(/\s+/g, " ").trim();
  if (trimmed.length <= length) return trimmed;
  return trimmed.slice(0, length).replace(/\s+\S*$/, "") + "…";
}
