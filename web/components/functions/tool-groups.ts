export type ToolInfo = {
  name: string;
  description?: string;
  disabled?: boolean;
  group?: string;
  source?: "builtin" | "mcp";
  server?: string | null;
};

export const TOOL_GROUPS = [
  ["file", "Files & Shell", "文件与命令"],
  ["web", "Web & Media", "网页与媒体"],
  ["memory", "Memory", "记忆"],
  ["planning", "Planning", "规划"],
  ["agents", "Agent Collaboration", "Agent 协作"],
  ["jobs", "Jobs & Schedules", "任务与调度"],
  ["code", "Code Intelligence", "代码分析"],
  ["mcp", "MCP Discovery", "MCP 发现"],
  ["worktree", "Worktrees", "工作树"],
  ["interaction", "User Interaction", "用户交互"],
  ["runtime", "Runtime", "运行时"],
  ["other", "Other", "其他"],
] as const;

export function groupTools(tools: ToolInfo[], by: "group" | "server" = "group") {
  const rows = new Map<string, ToolInfo[]>();
  for (const tool of tools) {
    const key = by === "server" ? tool.server || "MCP" : tool.group || "other";
    const items = rows.get(key);
    if (items) items.push(tool);
    else rows.set(key, [tool]);
  }
  const order = new Map<string, number>(TOOL_GROUPS.map(([name], index) => [name, index]));
  return [...rows]
    .map(([name, items]) => ({ name, items }))
    .sort((a, b) => by === "group"
      ? (order.get(a.name) ?? 999) - (order.get(b.name) ?? 999)
      : a.name.localeCompare(b.name));
}
