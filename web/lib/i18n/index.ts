/**
 * Minimal i18n helper. Stores the locale in localStorage and exposes
 * a `useTranslation()` hook returning a `t(key)` function + the
 * current locale + a setter that broadcasts to subscribers.
 *
 * Scope: shared UI labels that have been migrated to this helper.
 * Components opt in by adding stable keys to the dictionary below.
 */
"use client";

import { useCallback, useEffect, useState } from "react";

export type Locale = "en" | "zh";

const STORAGE_KEY = "agentic_locale";

// Module-level subscribers — Zustand-lite. Each component using
// `useTranslation()` re-renders when the locale changes.
const subscribers = new Set<(loc: Locale) => void>();

function readStored(): Locale {
  if (typeof window === "undefined") return "en";
  const v = localStorage.getItem(STORAGE_KEY);
  return v === "zh" ? "zh" : "en";
}

let current: Locale = "en";

export function setLocale(next: Locale): void {
  if (next === current) return;
  current = next;
  if (typeof window !== "undefined") {
    localStorage.setItem(STORAGE_KEY, next);
    document.documentElement.setAttribute("lang", next === "zh" ? "zh-CN" : "en");
  }
  subscribers.forEach((s) => s(next));
}

export function getLocale(): Locale {
  return current;
}

export function translateText(en: string, zh: string): string {
  return current === "zh" ? zh : en;
}

// Per-key dictionary. Top-level keys are stable identifiers; the
// inner record holds the per-locale string. Falls back to English
// when a translation is missing.
const DICT = {
  // /settings/general page
  "general.title": { en: "General", zh: "通用" },
  "general.meta": {
    en: "App-wide preferences. Theme, font and language are per-browser; version info is read-only.",
    zh: "应用级偏好。主题、字体和语言按浏览器保存；版本信息只读。",
  },
  "general.section.preferences": { en: "Preferences", zh: "偏好设置" },
  "general.appearance": { en: "Appearance", zh: "外观" },
  "general.theme.light": { en: "Light", zh: "浅色" },
  "general.theme.auto": { en: "Auto", zh: "跟随系统" },
  "general.theme.dark": { en: "Dark", zh: "深色" },
  "general.font": { en: "Font", zh: "字体" },
  "general.language": { en: "Language", zh: "语言" },
  "general.section.application": { en: "Application", zh: "应用信息" },
  "general.version": { en: "Version", zh: "版本" },
  "general.framework": { en: "Framework", zh: "框架" },
  "general.section.agent": { en: "Agent", zh: "Agent" },
  "general.section.you": { en: "User", zh: "用户" },
  "general.you.name.placeholder": { en: "User", zh: "用户" },
  "general.agent.name": { en: "Display name", zh: "显示名称" },
  "general.agent.name.placeholder": { en: "Agent", zh: "Agent" },
  "general.agent.initial": { en: "Avatar initial", zh: "头像字符" },
  "general.agent.initial.hint": {
    en: "One character shown in the round avatar.",
    zh: "圆形头像里显示的一个字符。",
  },
  "general.agent.color": { en: "Avatar color", zh: "头像颜色" },
  "general.agent.preview": { en: "Preview", zh: "预览" },

  // Settings shell + tab labels
  "settings.title": { en: "Settings", zh: "设置" },
  "settings.tab.providers": { en: "LLM Providers", zh: "大模型 Provider" },
  "settings.tab.usage": { en: "Token Usage", zh: "Token 用量" },
  "settings.tab.search": { en: "Web Search", zh: "网页搜索" },
  "settings.tab.channels": { en: "Channels", zh: "消息渠道" },
  "settings.tab.general": { en: "General", zh: "通用" },
  "settings.tab.system": { en: "System", zh: "系统" },

  // Token usage page
  "usage.desc": {
    en: "Token consumption aggregated across all sessions, from already-recorded provider usage. Cost is estimated from the model catalog's pricing where the model is known.",
    zh: "汇总所有会话已记录的 Token 用量。成本按模型目录中的定价估算（仅对已知定价的模型）。",
  },
  "usage.loading": { en: "Loading…", zh: "加载中…" },
  "usage.error": { en: "Failed to load usage data.", zh: "加载用量数据失败。" },
  "usage.empty": {
    en: "No per-model usage recorded yet. Models that don't report usage (some OpenAI-compatible backends) won't appear here.",
    zh: "暂无按模型记录的用量。不回报 usage 的模型（部分 OpenAI 兼容后端）不会出现在这里。",
  },
  "usage.byModel": { en: "By model", zh: "按模型" },
  "usage.card.input": { en: "Input tokens", zh: "输入 Token" },
  "usage.card.output": { en: "Output tokens", zh: "输出 Token" },
  "usage.card.total": { en: "Total tokens", zh: "总 Token" },
  "usage.card.cost": { en: "Est. cost", zh: "预估成本" },
  "usage.card.sessions": { en: "Sessions", zh: "会话数" },
  "usage.card.turns": { en: "Turns", zh: "对话轮数" },
  "usage.col.model": { en: "Model", zh: "模型" },
  "usage.col.provider": { en: "Provider", zh: "Provider" },
  "usage.col.input": { en: "Input", zh: "输入" },
  "usage.col.output": { en: "Output", zh: "输出" },
  "usage.col.cacheRead": { en: "Cache read", zh: "缓存读取" },
  "usage.col.messages": { en: "Messages", zh: "消息数" },
  "usage.col.cost": { en: "Est. cost", zh: "预估成本" },

  // Left sidebar nav (AppShell)
  "nav.new_chat": { en: "New chat", zh: "新会话" },
  "nav.functions": { en: "Functions", zh: "函数" },
  "nav.programs": { en: "Programs", zh: "程序" },
  "nav.skills": { en: "Skills", zh: "技能" },
  "nav.plugins": { en: "Plugins", zh: "插件" },
  "nav.mcp": { en: "MCP Servers", zh: "MCP 服务器" },
  "nav.memory": { en: "Memory", zh: "记忆" },
  "nav.chats": { en: "Chats", zh: "会话历史" },

  // Sidebar secondary labels and actions
  "sidebar.toggle": { en: "Toggle sidebar", zh: "切换侧边栏" },
  "sidebar.refresh": { en: "Refresh", zh: "刷新" },
  "sidebar.favorite_functions": { en: "Favorite Functions", zh: "收藏函数" },
  "sidebar.show": { en: "Show", zh: "显示" },
  "sidebar.hide": { en: "Hide", zh: "隐藏" },
  "sidebar.clear_all": { en: "Clear all", zh: "清空全部" },
  "sidebar.no_conversations": { en: "No conversations yet", zh: "暂无会话" },
  "sidebar.delete_chat": { en: "Delete chat", zh: "删除会话" },
  "sidebar.delete_all_chats": { en: "Delete all chats", zh: "删除全部会话" },
  "sidebar.cancel": { en: "Cancel", zh: "取消" },
  "sidebar.delete": { en: "Delete", zh: "删除" },
  "sidebar.untitled": { en: "Untitled", zh: "未命名" },
  "sidebar.running": { en: "running", zh: "运行中" },
  "sidebar.delete_all_irreversible": {
    en: "This cannot be undone.",
    zh: "此操作无法撤销。",
  },
  // Conversation context menu + Recents filter
  "sidebar.rename": { en: "Rename", zh: "重命名" },
  "sidebar.pin": { en: "Pin", zh: "置顶" },
  "sidebar.unpin": { en: "Unpin", zh: "取消置顶" },
  "sidebar.move_to_group": { en: "Move to group", zh: "移动到分组" },
  "sidebar.new_group": { en: "New group…", zh: "新建分组…" },
  "sidebar.new_group_prompt": { en: "Group name", zh: "分组名称" },
  "sidebar.ungrouped": { en: "Ungrouped", zh: "未分组" },
  "sidebar.remove_from_group": { en: "Remove from group", zh: "移出分组" },
  "sidebar.copy_link": { en: "Copy link", zh: "复制链接" },
  "sidebar.link_copied": { en: "Link copied", zh: "已复制链接" },
  "sidebar.archive": { en: "Archive", zh: "归档" },
  "sidebar.unarchive": { en: "Unarchive", zh: "取消归档" },
  "sidebar.pinned": { en: "Pinned", zh: "已置顶" },
  "sidebar.recents": { en: "Recents", zh: "最近" },
  "sidebar.needs_input": { en: "Needs input", zh: "等待输入" },
  "sidebar.unread": { en: "New result", zh: "有新结果" },
  "sidebar.today": { en: "Today", zh: "今天" },
  "sidebar.yesterday": { en: "Yesterday", zh: "昨天" },
  "sidebar.older": { en: "Older", zh: "更早" },
  "sidebar.no_projects": { en: "No projects yet", zh: "暂无项目" },
  "sidebar.no_environments": { en: "No environments yet", zh: "暂无环境" },
  "sidebar.filter": { en: "Filter & sort", zh: "筛选与排序" },
  "sidebar.status": { en: "Status", zh: "状态" },
  "sidebar.status_active": { en: "Active", zh: "活跃" },
  "sidebar.status_archived": { en: "Archived", zh: "已归档" },
  "sidebar.status_all": { en: "All", zh: "全部" },
  "sidebar.project": { en: "Project", zh: "项目" },
  "sidebar.all_projects": { en: "All projects", zh: "全部项目" },
  "sidebar.environment": { en: "Environment", zh: "环境" },
  "sidebar.last_activity": { en: "Last activity", zh: "最近活动" },
  "sidebar.activity_all": { en: "All", zh: "全部" },
  "sidebar.activity_1d": { en: "Today", zh: "今天" },
  "sidebar.activity_7d": { en: "Past 7 days", zh: "近 7 天" },
  "sidebar.activity_30d": { en: "Past 30 days", zh: "近 30 天" },
  "sidebar.filter_all": { en: "All", zh: "全部" },
  "sidebar.group_by": { en: "Group by", zh: "分组方式" },
  "sidebar.group_date": { en: "Date", zh: "日期" },
  "sidebar.group_none": { en: "None", zh: "无" },
  "sidebar.group_state": { en: "State", zh: "状态" },
  "sidebar.group_project": { en: "Project", zh: "项目" },
  "sidebar.working": { en: "Working", zh: "进行中" },
  "sidebar.completed": { en: "Completed", zh: "已完成" },
  "sidebar.sort_by": { en: "Sort by", zh: "排序方式" },
  "sidebar.sort_title": { en: "Alphabetically", zh: "按字母顺序" },
  "sidebar.sort_created": { en: "Created time", zh: "创建时间" },
  "sidebar.sort_recency": { en: "Recency", zh: "最近" },

  // User footer menu
  "user.local_instance": { en: "Local instance", zh: "本地实例" },
  "user.settings": { en: "Settings", zh: "设置" },
  "user.about": { en: "About", zh: "关于" },

  // Chat topbar agent selector
  "agent.chat": { en: "Chat", zh: "对话" },
  "agent.exec": { en: "Exec", zh: "执行" },
  "agent.chat_agent": { en: "Chat Agent", zh: "对话 Agent" },
  "agent.execution_agent": { en: "Execution Agent", zh: "执行 Agent" },
  "agent.no_enabled_models": { en: "No enabled models", zh: "没有启用的模型" },
  "agent.enable_models": { en: "enable some in Settings", zh: "去设置中启用模型" },
  "agent.switch_failed": { en: "Agent switch failed: ", zh: "Agent 切换失败：" },

  // Right sidebar panels
  "right.resize_panel": { en: "Drag to resize panel", zh: "拖动调整面板宽度" },
  "right.toggle_panel": { en: "Toggle panel", zh: "切换面板" },
  "right.history": { en: "History", zh: "历史" },
  "right.context": { en: "Context", zh: "上下文" },
  "right.executions": { en: "Executions", zh: "执行" },
  "right.context_tooltip": {
    en: "Compacted context the next LLM turn will see",
    zh: "下一次大模型调用会读取的压缩上下文",
  },
  "right.viewport": { en: "Viewport", zh: "视口" },
  "right.viewport_tooltip": {
    en: "Highlight follows chat scroll position",
    zh: "高亮跟随当前聊天滚动位置",
  },
  "right.context_highlight_tooltip": {
    en: "Highlight the message set the next LLM turn will load",
    zh: "高亮下一次大模型调用会读取的消息集合",
  },
  "right.no_execution": { en: "No execution selected.", zh: "未选择执行记录。" },
  "right.no_execution_hint": {
    en: "Click a node in the conversation tree to inspect its context and output.",
    zh: "点击会话树中的节点查看它的上下文和输出。",
  },
  "right.branches": { en: "Branches", zh: "分支" },
  "right.worktrees": { en: "Worktrees", zh: "Worktree" },
  "right.selected": { en: "selected", zh: "已选择" },
  "right.base": { en: "base", zh: "基准" },
  "right.attach_to": { en: "Attach to", zh: "附加到" },
  "right.attach_title": {
    en: "Attach selected branch(es) to another branch",
    zh: "把选中的分支附加到另一个分支",
  },
  "right.this_session": { en: "This session", zh: "当前会话" },
  "right.other_session": { en: "Other session...", zh: "其他会话..." },
  "right.no_other_branches": {
    en: "No other branches in this session.",
    zh: "当前会话没有其他分支。",
  },
  "right.loading": { en: "Loading...", zh: "加载中..." },
  "right.merge_ellipsis": { en: "Merge...", zh: "合并..." },
  "right.merge": { en: "Merge", zh: "合并" },
  "right.clear_selection": { en: "Clear selection", zh: "清除选择" },
  "right.task_running_merge_wait": {
    en: "Task is running; wait for it to finish before merging",
    zh: "任务正在运行，完成后才能合并",
  },
  "right.deselect_base_hint": {
    en: "Click again to deselect; Cmd-click to mark as base",
    zh: "再次点击取消选择；按住 Cmd 点击可标为基准",
  },
  "right.select_merge_hint": {
    en: "Select for merge (Cmd-click to mark as base)",
    zh: "选择用于合并（按住 Cmd 点击可标为基准）",
  },
  "right.delete_branch_confirm": {
    en: "Delete this branch and its messages? This cannot be undone.",
    zh: "删除这个分支及其消息？此操作无法撤销。",
  },
  "right.rename_branch": { en: "Rename branch", zh: "重命名分支" },
  "right.delete_branch": { en: "Delete branch", zh: "删除分支" },
  "right.head": { en: "HEAD", zh: "当前" },
  "right.running": { en: "running", zh: "运行中" },
  "right.equal_merge": { en: "Equal merge", zh: "平等合并" },
  "right.equal_merge_desc": {
    en: "write a new turn whose parents are all selected branches. Reply lands as a fresh branch tip.",
    zh: "写入一个新回合，父节点为所有已选分支；回复会落在新的分支末端。",
  },
  "right.attach_base": { en: "Attach into base", zh: "附加到基准" },
  "right.attach_base_desc": {
    en: "reply continues the base branch, and the other selections are loaded as context. Cmd-click a row to pick the base.",
    zh: "回复继续写入基准分支，其他已选分支作为上下文读取。按住 Cmd 点击行可选择基准。",
  },
  "right.merge_instruction_placeholder": {
    en: "Optional instruction for the merge agent (how to reconcile)",
    zh: "可选：给合并 Agent 的指令（如何整合）",
  },
  "right.merge_shortcut_hint": { en: "Cmd/Ctrl + Enter to merge", zh: "Cmd/Ctrl + Enter 合并" },
  "right.status": { en: "Status", zh: "状态" },
  "right.created": { en: "Created", zh: "创建时间" },
  "right.file": { en: "file", zh: "个文件" },
  "right.files": { en: "files", zh: "个文件" },
  "right.keep": { en: "Keep", zh: "保留" },
  "right.discard": { en: "Discard", zh: "丢弃" },
  "right.keep_worktree_title": {
    en: "Keep the on-disk worktree, detach from OpenProgram",
    zh: "保留磁盘上的 worktree，并从 OpenProgram 中解除关联",
  },
  "right.discard_worktree_title": {
    en: "Remove worktree dir and delete branch",
    zh: "删除 worktree 目录并删除分支",
  },
  "right.merging": { en: "merging...", zh: "正在合并..." },
} as const;

type Key = keyof typeof DICT;

export function useTranslation() {
  const [loc, setLoc] = useState<Locale>(current);
  useEffect(() => {
    // Hydrate from localStorage once the client mounts (SSR-safe).
    const stored = readStored();
    if (stored !== current) {
      current = stored;
      if (typeof document !== "undefined") {
        document.documentElement.setAttribute("lang", stored === "zh" ? "zh-CN" : "en");
      }
    }
    setLoc(current);
    const sub = (v: Locale) => setLoc(v);
    subscribers.add(sub);
    return () => { subscribers.delete(sub); };
  }, []);

  const t = useCallback((key: Key): string => {
    const row = DICT[key];
    return (row && (row[loc] ?? row.en)) || String(key);
  }, [loc]);
  const text = useCallback((en: string, zh: string): string => (loc === "zh" ? zh : en), [loc]);
  return { t, text, locale: loc, setLocale };
}
