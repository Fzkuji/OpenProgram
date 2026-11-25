/**
 * How one tool call reads in the execution timeline: an icon, a tone
 * (colour family), a verb and the single argument that identifies the
 * call ("Ran `npm test`", "Edited `src/app.ts`"). Raw JSON never reaches
 * the row; the side panel still shows full params and output.
 */
// Type-only (erased at runtime), so the node unit tests can load this
// module without the `@/` alias.
import type { SolarIconName } from "../../../solar-icons/bodies";
import { guiCapabilityLabel } from "./gui-tool-view.ts";
import { USER_BROWSERS } from "../../../../lib/computer-use/browsers.ts";
import { callStep, callTarget, machineLabel, presentStep, resultBody, screenStep, type CallTarget } from "../../../../lib/computer-use/computer-use-call.ts";
import { searchQuery } from "../../../../lib/chat/transcript/web-sources.ts";

export type ToolTone =
  | "shell" | "edit" | "read" | "search" | "web" | "agent"
  | "memory" | "plan" | "media" | "system" | "function";

export interface ToolPresentation {
  /** Solar Bold Duotone glyph drawn in the row's tinted tile. */
  icon: SolarIconName;
  tone: ToolTone;
  /** Verb phrase, already localized. */
  title: string;
  /** The identifying argument (command, path, query, url…), if any. */
  target?: string;
}

type Text = (en: string, zh: string) => string;
type Args = Record<string, unknown>;

function str(v: unknown): string | undefined {
  if (typeof v === "string" && v.trim()) return v.trim();
  if (typeof v === "number" || typeof v === "boolean") return String(v);
  return undefined;
}

function firstQuestion(args: Args): string | undefined {
  const questions = (args as Record<string, unknown>)?.questions;
  const first = Array.isArray(questions) ? questions[0] : undefined;
  const question = first && typeof first === "object" ? (first as Record<string, unknown>).question : undefined;
  return typeof question === "string" && question.trim() ? question : undefined;
}

function pick(args: Args, ...keys: string[]): string | undefined {
  for (const key of keys) {
    const v = str(args[key]);
    if (v) return v;
  }
  return undefined;
}

function firstLine(s: string | undefined): string | undefined {
  if (!s) return s;
  const line = s.split("\n").find((l) => l.trim());
  return line?.trim();
}

/** Shorten an absolute path to its last segments so the chip stays readable. */
function shortPath(p: string | undefined): string | undefined {
  if (!p) return p;
  const parts = p.split("/").filter(Boolean);
  return parts.length > 3 ? "…/" + parts.slice(-3).join("/") : p;
}

/** Where a step lands, as the phrase after its verb: "in Calculator", "on the screen". */
type Place = { en: string; zh: string } | null;

function placeIn(app: string | undefined): Place {
  return app ? { en: `in ${app}`, zh: `在 ${app} 中` } : null;
}

/** "Clicked in Calculator", "Typed on the screen": the action and where, never the raw args. */
function computerUsePresentation(args: Args, text: Text, place: Place = placeIn(pick(args, "app")),
  listPlace: Place = null): ToolPresentation {
  const action = pick(args, "action") ?? "";
  const titles: Record<string, [string, string]> = {
    list_apps: ["Listed apps", "列出应用"],
    list_displays: ["Listed displays", "列出显示器"],
    get_state: ["Read", "读取"],
    click: ["Clicked", "点击"],
    type_text: ["Typed", "输入"],
    press_key: ["Pressed key", "按键"],
    set_value: ["Set value", "设值"],
    scroll: ["Scrolled", "滚动"],
    drag: ["Dragged", "拖拽"],
    perform_secondary_action: ["Performed action", "执行动作"],
    select_text: ["Selected text", "选中文字"],
    paste: ["Pasted", "粘贴"],
    list_windows: ["Listed windows", "列出窗口"],
    zoom: ["Zoomed", "放大查看"],
    find: ["Searched", "查找"],
    copy: ["Copied", "复制"],
    focus: ["Focused", "聚焦"],
    menu: ["Used the menu", "使用菜单"],
    show_window: ["Showed the window", "显示窗口"],
    open_file: ["Opened a file", "打开文件"],
    batch: ["Ran steps", "执行多步"],
    hover: ["Hovered", "悬停"],
    wait: ["Waited", "等待"],
    start_audio_recording: ["Started recording", "开始录音"],
    stop_audio_recording: ["Stopped recording", "停止录音"],
  };
  const [en, zh] = titles[action] ?? ["Computer use", "电脑操作"];
  // A list names no application: it says which computer it looked at, if another one.
  const where = action === "list_apps" || action === "list_displays" ? listPlace : place;
  const title = where ? text(`${en} ${where.en}`, `${where.zh}${zh}`) : text(en, zh);
  const menuPath = Array.isArray(args.menu_path) ? args.menu_path.map(str).filter(Boolean).join(" › ")
    : pick(args, "menu_path");
  const steps = Array.isArray(args.actions) ? args.actions.length : 0;
  const target = action === "press_key" ? pick(args, "key")
    : action === "type_text" || action === "paste" || action === "select_text" || action === "find"
      ? firstLine(pick(args, "text")) ?? pick(args, "role")
    : action === "perform_secondary_action" ? pick(args, "action_name")
    : action === "set_value" ? pick(args, "value")
    : action === "get_state" || action === "show_window" ? pick(args, "window", "display")
    : action === "open_file" ? shortPath(pick(args, "path"))
    : action === "menu" ? menuPath
    : action === "batch" && steps ? text(`${steps} steps`, `${steps} 步`)
    : undefined;
  return { icon: "monitor", tone: "system", title, target };
}

export function parseToolArgs(input?: string): Args {
  if (!input) return {};
  try {
    const v = JSON.parse(input);
    if (!v || typeof v !== "object" || Array.isArray(v)) return { input: v };
    // The runtime's task progress, not the tool's own argument.
    const { task_progress: _progress, ...args } = v as Args;
    return args;
  } catch {
    return { input };
  }
}

/** Where a computer_use step lands on its target, for the verb's phrase. */
function placeOf(call: CallTarget, app: string | undefined): { place: Place; list: Place } {
  const machine = call.machine ? machineLabel(call.machine) : "";
  const on: Place = machine ? { en: `on ${machine}`, zh: `在 ${machine} 上` } : null;
  if (call.family === "window") {
    if (!app) return { place: on, list: on };
    return { place: machine ? { en: `in ${app} on ${machine}`, zh: `在 ${machine} 的 ${app} 中` } : placeIn(app), list: on };
  }
  if (call.family === "screen") {
    const screen: Place = machine ? { en: `on ${machine}'s screen`, zh: `在 ${machine} 的屏幕上` }
      : { en: "on the screen", zh: "在屏幕上" };
    return { place: screen, list: on };
  }
  return { place: on, list: on };
}

/** The name a browser target goes by: "Browser", "Chrome", "Browser on Lab VM". */
export function browserTitle(call: CallTarget, text: Text): string {
  const own = USER_BROWSERS[call.browser];
  if (own) return own.short;
  return call.machine ? text(`Browser on ${machineLabel(call.machine)}`, `${machineLabel(call.machine)} 上的浏览器`)
    : text("Browser", "浏览器");
}

/** computer_use: application windows and screens read like the app tool's rows, browsers like their own. */
function computerUseToolPresentation(args: Args, text: Text): ToolPresentation {
  const call = callTarget(args);
  const step = callStep(args);
  const { place, list } = placeOf(call, pick(step, "app"));
  if (step.action === "code") {
    return { icon: "code-square", tone: "shell",
      title: place ? text(`Ran code ${place.en}`, `${place.zh}运行代码`) : text("Ran code", "运行代码"),
      target: firstLine(pick(step, "code")) };
  }
  if (call.family === "window" || call.family === "screen") return computerUsePresentation(step, text, place, list);
  const { action, steps, url } = screenStep(args);
  const what = action || (steps ? text(`${steps} steps`, `${steps} 步`) : "");
  if (call.family === "browser") {
    return { icon: "cursor", tone: "web", title: browserTitle(call, text),
      target: [what, url].filter(Boolean).join(" ") || undefined };
  }
  return { icon: "monitor", tone: "system", title: text("Computer use", "电脑操作"),
    target: [call.target, what].filter(Boolean).join(" ") || undefined };
}

/** Any string-ish argument, for tools without a dedicated rule. */
function genericTarget(args: Args): string | undefined {
  return pick(args, "command", "action", "path", "file_path", "url", "query",
    "pattern", "name", "prompt", "description", "title", "input")
    ?? Object.values(args).map(str).find(Boolean);
}

export interface PresentToolOptions {
  /** The model's provider ran the call itself (block.provider_executed). */
  providerExecuted?: boolean;
}

/** A provider-executed web search: one "Searched the web: <query>" line,
 *  the query read from the JSON input or, failing that, the raw input. */
export function presentProviderSearch(input: string | undefined, text: Text): ToolPresentation {
  const query = searchQuery(input);
  return {
    icon: "global",
    tone: "web",
    title: query ? text(`Searched the web: ${query}`, `网页搜索：${query}`) : text("Searched the web", "网页搜索"),
  };
}

export function presentTool(
  name: string,
  input: string | undefined,
  text: Text,
  options: PresentToolOptions = {},
): ToolPresentation {
  if (options.providerExecuted && name === "web_search") return presentProviderSearch(input, text);
  const args = parseToolArgs(input);
  const path = shortPath(pick(args, "path", "file_path", "filename", "file", "notebook_path"));
  switch (name) {
    case "computer_use":
      return computerUseToolPresentation(args, text);
    case "gui_agent":
      return { icon: "monitor", tone: "system", title: guiCapabilityLabel(name, text), target: firstLine(pick(args, "task")) };
    case "vm_use":
      return { icon: "monitor", tone: "system", title: guiCapabilityLabel(name, text),
        target: pick(args, "app_name") ?? firstLine(pick(args, "task")) };
    case "browser_use":
      return { icon: "cursor", tone: "web", title: guiCapabilityLabel(name, text), target: pick(args, "url") ?? firstLine(pick(args, "task")) };
    case "bash":
      return { icon: "programming", tone: "shell", title: text("Ran", "运行"),
        target: firstLine(pick(args, "command", "cmd", "input")) };
    case "write_stdin": {
      const chars = typeof args.chars === "string" ? args.chars : "";
      if (!chars) return { icon: "programming", tone: "shell", title: text("Waited for output", "等待输出") };
      if (chars === "\u0003") return { icon: "programming", tone: "shell", title: text("Interrupted", "中断命令") };
      return { icon: "programming", tone: "shell", title: text("Typed", "输入"), target: firstLine(chars) };
    }
    case "terminal_use":
      return { icon: "programming", tone: "shell",
        title: args.action === "input" ? text("Typed in terminal", "在终端输入") : text("Terminal", "终端"),
        target: firstLine(pick(args, "data", "action")) };
    case "execute_code":
      return { icon: "code-square", tone: "shell", title: text("Executed code", "执行代码"),
        target: pick(args, "language") ?? firstLine(pick(args, "code")) };
    case "edit":
      return { icon: "pen-2", tone: "edit", title: text("Edited", "修改"), target: path };
    case "write":
      return { icon: "pen-2", tone: "edit", title: text("Wrote", "写入"), target: path };
    case "apply_patch":
      return { icon: "pen-2", tone: "edit", title: text("Patched", "应用补丁"), target: path };
    case "read":
    case "pdf":
      return { icon: "book-2", tone: "read", title: text("Read", "读取"), target: path };
    case "list":
      return { icon: "folder-open", tone: "read", title: text("Listed", "列出"), target: path ?? "." };
    case "glob":
      return { icon: "file-search", tone: "search", title: text("Found files", "查找文件"),
        target: pick(args, "pattern", "glob") };
    case "grep":
      return { icon: "magnifier", tone: "search", title: text("Searched", "搜索"),
        target: pick(args, "pattern", "query", "regex") };
    case "semble_search":
    case "semble_find_related":
      return { icon: "magnifier", tone: "search", title: text("Searched code", "搜索代码"),
        target: pick(args, "query", "path", "symbol") };
    case "lsp_definition":
    case "lsp_references":
    case "lsp_diagnostics":
      return { icon: "structure", tone: "search", title: text("Inspected code", "代码分析"),
        target: pick(args, "symbol") ?? path };
    case "web_search":
      return { icon: "global", tone: "web", title: text("Searched the web", "网页搜索"),
        target: pick(args, "query", "q") };
    case "web_fetch":
      return { icon: "global", tone: "web", title: text("Fetched", "抓取网页"), target: pick(args, "url") };
    case "playwright_browser":
    case "web_use": {
      // An act names its action inside arguments; show that instead of "act".
      const nested = args.arguments && typeof args.arguments === "object" && !Array.isArray(args.arguments)
        ? args.arguments as Args : {};
      const action = pick(args, "action") ?? pick(nested, "action");
      const url = pick(args, "url") ?? pick(nested, "url");
      return { icon: "cursor", tone: "web", title: text("Browser", "浏览器"),
        target: [action ?? pick(args, "command"), url].filter(Boolean).join(" ") || undefined };
    }
    case "image_generate":
      return { icon: "gallery-add", tone: "media", title: text("Generated image", "生成图片"),
        target: firstLine(pick(args, "prompt")) };
    case "image_analyze":
      return { icon: "gallery", tone: "media", title: text("Analyzed image", "分析图片"),
        target: path ?? pick(args, "url") };
    case "canvas":
    case "send_file":
      return { icon: "plain-2", tone: "media", title: name === "canvas" ? text("Canvas", "画布") : text("Sent file", "发送文件"),
        target: path ?? genericTarget(args) };
    case "agent":
    case "task":
    case "mixture_of_agents":
    case "list_agents":
    case "archive_agent":
      return { icon: "ghost", tone: "agent", title: text("Sub-agent", "子代理"),
        target: firstLine(pick(args, "description", "name", "prompt", "task")) };
    case "send_message":
      return { icon: "plain-2", tone: "agent", title: text("Sent to", "已发给"),
        target: pick(args, "to", "agent", "name") };
    case "read_conversation":
      return { icon: "book", tone: "read", title: text("Read conversation", "读取对话"),
        target: genericTarget(args) };
    case "ask_user_question":
      return { icon: "chat-round-question-mark", tone: "plan", title: text("Asked", "提问"),
        // Claude Code's question tool asks a list: its first question names the card.
        target: firstLine(pick(args, "question", "prompt") ?? firstQuestion(args)) };
    case "enter_plan_mode":
    case "exit_plan_mode":
      return { icon: "map", tone: "plan", title: name === "enter_plan_mode" ? text("Planning", "进入规划") : text("Plan ready", "规划完成") };
    case "remind":
      return { icon: "calendar", tone: "plan", title: text("Reminder", "提醒"),
        target: firstLine(pick(args, "message")) };
    case "cron":
    case "scheduler":
    case "list_jobs":
    case "job_output":
      return { icon: "calendar", tone: "plan", title: text("Scheduled", "定时任务"),
        target: genericTarget(args) };
    case "program":
      return { icon: "box", tone: "function", title: text("Ran program", "运行程序"),
        target: pick(args, "name", "program", "path") };
    case "skill":
      return { icon: "stars", tone: "function", title: text("Used skill", "使用技能"),
        target: pick(args, "name", "skill") };
    case "resource":
      return { icon: "database", tone: "read", title: text("Resource", "资源"), target: genericTarget(args) };
  }
  if (name === "task_start" || name === "task_step" || name === "task_list") {
    // Drawn only where a call is shown outside a reply's task block.
    return { icon: "checklist", tone: "plan", title: text("Updated task", "更新任务"),
      target: firstLine(pick(args, "title", "outcome", "result")) };
  }
  if (name.startsWith("todo_")) {
    return { icon: "checklist", tone: "plan", title: text("Updated todos", "更新待办"),
      target: firstLine(pick(args, "title", "content", "text")) };
  }
  if (name.startsWith("memory_")) {
    return { icon: "database", tone: "memory", title: text("Memory", "记忆"),
      target: pick(args, "query", "key", "path", "content") };
  }
  if (name.startsWith("worktree_")) {
    return { icon: "git-branch", tone: "system", title: text("Worktree", "工作树"),
      target: name.slice("worktree_".length) };
  }
  if (name.startsWith("self_update")) {
    return { icon: "refresh", tone: "system", title: text("Self-update", "自我更新"),
      target: pick(args, "action") || name.replace(/^self_update_?/, "") || undefined };
  }
  if (name.includes("mcp")) {
    return { icon: "plug-circle", tone: "system", title: "MCP", target: genericTarget(args) };
  }
  return { icon: "sledgehammer", tone: "function", title: name || text("Function", "函数"),
    target: genericTarget(args) };
}

/** One readable line for a tool result. JSON objects and arrays are
 *  summarised by their telling fields instead of printed raw. */
export function summarizeResult(raw: string, text: Text): string {
  // The guides and the target header in front of a result are not the result.
  const trimmed = resultBody(raw);
  if (!trimmed.startsWith("{") && !trimmed.startsWith("[")) return firstLine(trimmed) ?? "";
  let value: unknown;
  try {
    value = JSON.parse(trimmed);
  } catch {
    // Stored results are often truncated mid-object; still pull out the
    // telling string fields rather than echoing the braces.
    const found: string[] = [];
    for (const key of ["error", "progress", "title", "message", "summary", "status", "url"]) {
      const m = new RegExp(`"${key}"\\s*:\\s*"((?:[^"\\\\]|\\\\.)*)"`).exec(trimmed);
      if (m?.[1]) found.push(m[1]);
      if (found.length >= 2) break;
    }
    return found.length ? found.join(" · ") : text("structured output", "结构化输出");
  }
  if (Array.isArray(value)) {
    return value.length === 1 ? text("1 item", "1 项") : text(`${value.length} items`, `${value.length} 项`);
  }
  if (!value || typeof value !== "object") return String(value);
  const obj = value as Args;
  const pieces: string[] = [];
  const failed = obj.ok === false || obj.success === false || typeof obj.error === "string";
  if (failed) pieces.push(str(obj.error) ?? text("failed", "失败"));
  for (const key of ["progress", "title", "message", "summary", "status", "url", "path", "result", "output"]) {
    const v = str(obj[key]);
    if (v && !pieces.includes(v)) pieces.push(firstLine(v) ?? v);
    if (pieces.length >= 2) break;
  }
  if (pieces.length === 0) {
    const nested = Object.values(obj).find((v) => Array.isArray(v)) as unknown[] | undefined;
    if (nested) pieces.push(text(`${nested.length} items`, `${nested.length} 项`));
    else pieces.push(obj.ok === true || obj.success === true ? text("done", "完成") : text(`${Object.keys(obj).length} fields`, `${Object.keys(obj).length} 个字段`));
  }
  return pieces.join(" · ");
}
