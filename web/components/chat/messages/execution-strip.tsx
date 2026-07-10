"use client";

/**
 * ExecutionStrip — 一轮里连续的 thinking/tool 块（含 Spawned 卡）收进
 * 一个容器卡。
 *
 * 对话层常驻、执行痕迹默认收起（与 DAG viewport 的 ⚒N 聚合同一原则）。
 * 头部与 inline-tree 同级的观感（撑得住"这是整轮过程"），标签写清内容
 * 量（思考几段、每个工具各调几次、spawn 了谁）；展开后内部块经
 * `.exec-strip-body` 的 CSS 扁平化成细行，Spawned 行嵌在它的调用行
 * 后面。流式进行中的一轮不走这里——正在跑的过程实时平铺，轮次落定后
 * 由 assistant-bubble 切到本组件。
 */
import { useState } from "react";

import type { AssistantBlock } from "@/lib/session-store";
import { useTranslation } from "@/lib/i18n";

/** spawn 类工具：在摘要里算"子代理"，不算普通函数调用。 */
export const SPAWNING_TOOL_NAMES = new Set(["task", "message_branch"]);

/** 汇总标签只分三类，不报具体工具名：
 *  `思考 ×2 · 函数 ×1 · 子代理: 查询北京天气`。 */
export function execStripLabel(
  blocks: AssistantBlock[],
  spawnNames: string[],
  text: (en: string, zh: string) => string,
): string {
  let thinking = 0;
  let functions = 0;
  let spawnBlocks = 0;
  for (const b of blocks) {
    if (b.type === "thinking") thinking++;
    else if (b.type === "tool") {
      if (SPAWNING_TOOL_NAMES.has(b.tool || "")) spawnBlocks++;
      else functions++;
    }
  }
  const parts: string[] = [];
  if (thinking > 0) {
    parts.push(`${text("thinking", "思考")} ×${thinking}`);
  }
  if (functions > 0) {
    parts.push(`${text("functions", "函数")} ×${functions}`);
  }
  const subAgents = Math.max(spawnBlocks, spawnNames.length);
  if (subAgents > 0) {
    parts.push(
      spawnNames.length > 0
        ? `${text("sub-agent", "子代理")}: ${spawnNames.join("、")}`
        : `${text("sub-agent", "子代理")} ×${subAgents}`,
    );
  }
  return parts.join(" · ") || text("execution", "执行过程");
}

export function ExecutionStrip({
  label,
  children,
}: {
  label: string;
  children: React.ReactNode;
}) {
  const [open, setOpen] = useState(false);
  const { text } = useTranslation();
  return (
    <div className="exec-strip" data-open={open ? "1" : "0"}>
      <button
        type="button"
        className="exec-strip-bar"
        onClick={() => setOpen((o) => !o)}
        title={open
          ? text("Collapse execution trace", "收起执行过程")
          : text("Expand execution trace", "展开执行过程")}
      >
        <span className="exec-strip-gear" aria-hidden="true">⚙</span>
        <span className="exec-strip-label">{label}</span>
        <span className={`exec-strip-chevron${open ? " open" : ""}`} aria-hidden="true">
          ▸
        </span>
      </button>
      {open ? <div className="exec-strip-body">{children}</div> : null}
    </div>
  );
}
