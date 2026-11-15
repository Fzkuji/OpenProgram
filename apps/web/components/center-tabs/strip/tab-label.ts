import type { CenterTab } from "@/lib/tabs/center-tabs-store";
import type { useTranslation } from "@/lib/i18n";
import { builtinPageLabel } from "../pages/builtin-page-label";

const pageLabels: Record<string, [string, string]> = {
  agents: ["Agents", "Agents"], programs: ["Abilities", "能力"],
  functions: ["Functions", "函数"], skills: ["Skills", "技能"], mcp: ["MCP", "MCP"],
  plugins: ["Plugins", "插件"], plugin: ["Plugins", "插件"],
  applications: ["Applications", "应用"], chats: ["History", "历史"],
  projects: ["Projects", "项目"], memory: ["Memory", "记忆"],
  history: ["History", "历史"], scheduler: ["Scheduler", "定时任务"],
  settings: ["Settings", "设置"],
};
const settingsLabels: Record<string, [string, string]> = {
  general: ["General", "通用"], appearance: ["Appearance", "外观"], providers: ["LLM Providers", "大模型 Provider"],
  channels: ["Channels", "消息渠道"], browser: ["Browser", "浏览器"],
  system: ["System", "系统"], memory: ["Memory", "记忆"],
  search: ["Web Search", "网页搜索"], usage: ["Token Usage", "Token 用量"],
  auth: ["Accounts", "账户"],
};

/** Presentation follows the current route without overwriting content metadata. */
function routeLabel(route: string, text: ReturnType<typeof useTranslation>["text"]): string {
  const pathname = route.split(/[?#]/, 1)[0];
  const [root, section] = pathname.split("/").filter(Boolean);
  const label = (root === "settings" && settingsLabels[section]) || pageLabels[root];
  return label ? text(...label) : pathname;
}

export function labelOf(
  tab: CenterTab,
  t: ReturnType<typeof useTranslation>["t"],
  text: ReturnType<typeof useTranslation>["text"],
): string {
  if (tab.navigationRoute) return routeLabel(tab.navigationRoute, text);
  if (tab.kind === "ntp") return text("New tab", "新标签页");
  if (tab.kind === "builtin") return builtinPageLabel(tab.page, text);
  if (tab.kind === "file") return tab.title;
  if (tab.kind === "web") return tab.title || tab.url || "";
  // "New conversation" is the backend's placeholder until the first reply
  // names the session; keep showing "New chat" instead of flipping label.
  if (tab.draft || tab.title === "New conversation") return text("New chat", "新会话");
  return tab.title || t("sidebar.untitled");
}

