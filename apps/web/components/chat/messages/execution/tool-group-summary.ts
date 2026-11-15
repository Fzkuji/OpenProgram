/**
 * The one-line header of a folded run of tool calls ("Ran a command,
 * created pill-shadow.html" +16 −0). Built purely from the structured tool
 * blocks already in the transcript; no model call. Design:
 * docs/reference/design/ui/chat/tool-group-summary.html.
 */
import type { AssistantBlock, TurnFileSummary } from "../../../../lib/session-store/types.ts";
import { SPAWNING_TOOL_NAMES } from "../../../../lib/chat/transcript/owned-children.ts";
import { isAgentLive } from "../../../../lib/chat/transcript/row-status.ts";
import { parseToolArgs, presentTool } from "./tool-presentation.ts";
import { GUI_TOOL_NAMES, isGuiToolCall } from "./gui-tool-view.ts";
import { appStepArgs, computerUseAgentTarget, isFreshCall, userBrowser } from "../../../../lib/computer-use/computer-use-agent-call.ts";

type Text = (en: string, zh: string) => string;
type Args = Record<string, unknown>;

/** spawn 类工具：在摘要里算"子代理"，不算普通函数调用。
 * `agent` 生新分支；`send_message` 虽不再 spawn，但它仍触发目标分支跑
 * 一轮、仍在 caller 轮上落 attach 指针卡（runner 的 attach 路径），所以
 * 摘要里同样按"子代理活动"计。 */
export { SPAWNING_TOOL_NAMES };

export interface ToolGroupSummary {
  /** Full sentence; the row truncates it and the hover tip shows it whole. */
  label: string;
  /** Calls in the group that failed (cancellations excluded). */
  failed: number;
  /** Badge counts; both null when the group changed no file or a count is unknown. */
  added: number | null;
  removed: number | null;
}

export interface ToolGroupOptions {
  text: Text;
  /** The group is the live tail of a streaming turn. */
  active?: boolean;
  /** tool_call_ids still running. */
  runningIds?: ReadonlySet<string>;
  /** Labels of sub-agents this group spawned. */
  spawnNames?: string[];
  /** Their statuses, in the same order (running / completed / errored / …). */
  spawnStates?: string[];
  /** Their branch heads, in the same order: a message to one is the same agent. */
  spawnHeads?: (string | undefined)[];
  /** The conversation the group belongs to: a message to another one goes
   *  to a peer conversation, not a sub-agent. */
  sessionId?: string;
  /** The turn's authoritative file summary, once the turn settled. */
  turnFiles?: TurnFileSummary;
  /** Every block of the turn, in order; lets a group see earlier groups. */
  turnBlocks?: AssistantBlock[];
}

/** Above this many lines on either side the edit diff falls back to plain line counts. */
export const LINE_DIFF_CAP = 2000;

type Kind =
  | "command" | "create" | "edit" | "delete" | "read" | "list" | "search"
  | "websearch" | "fetch" | "browser" | "agent" | "peer" | "imagegen" | "imageanalyze"
  | "todo" | "ask" | "memory" | "program" | "skill" | "gui" | "computer" | "other";

interface Bucket {
  kind: Kind;
  first: number;
  calls: number;
  /** Distinct named targets (paths, URLs, program names, tool names). */
  targets: Set<string>;
  /** Calls with no usable target. */
  anonymous: number;
  /** Target of the call in progress, "" when it has none. */
  running?: string;
}

interface FileOp { kind: "create" | "edit" | "delete"; path: string }

const COMMAND = new Set(["bash", "terminal_use", "write_stdin", "execute_code"]);
const SEARCH = new Set(["grep", "glob", "semble_search", "semble_find_related",
  "lsp_definition", "lsp_references", "lsp_diagnostics"]);
const BROWSER = new Set(["web_use", "playwright_browser", "browser_use"]);
const AGENT = new Set([...SPAWNING_TOOL_NAMES, "mixture_of_agents"]);
const FILE_WRITERS = new Set(["write", "edit", "apply_patch"]);

function str(v: unknown): string | undefined {
  return typeof v === "string" && v.trim() ? v.trim() : undefined;
}

function pick(args: Args, ...keys: string[]): string | undefined {
  for (const key of keys) {
    const v = str(args[key]);
    if (v) return v;
  }
  return undefined;
}

function filePath(args: Args): string | undefined {
  return pick(args, "file_path", "path", "filename", "file");
}

export function basename(path: string): string {
  const trimmed = path.replace(/[\\/]+$/, "");
  const position = Math.max(trimmed.lastIndexOf("/"), trimmed.lastIndexOf("\\"));
  return position >= 0 ? trimmed.slice(position + 1) : trimmed;
}

function host(url: string): string {
  try {
    return new URL(url).host || url;
  } catch {
    return url.length > 40 ? url.slice(0, 40) + "…" : url;
  }
}

function quote(pattern: string): string {
  const flat = pattern.replace(/\s+/g, " ");
  return `"${flat.length > 24 ? flat.slice(0, 24) + "…" : flat}"`;
}

/** Number of lines in a text, ignoring one trailing newline. */
export function countLines(s: string): number {
  if (!s) return 0;
  const n = s.split("\n").length;
  return s.endsWith("\n") ? n - 1 : n;
}

function splitLines(s: string): string[] {
  if (!s) return [];
  const lines = s.split("\n");
  if (s.endsWith("\n")) lines.pop();
  return lines;
}

/** Lines added and removed going from `before` to `after` (LCS line diff).
 *  Beyond LINE_DIFF_CAP lines on either side it returns the plain counts. */
export function lineDiff(before: string, after: string): { added: number; removed: number } {
  let a = splitLines(before);
  let b = splitLines(after);
  let start = 0;
  while (start < a.length && start < b.length && a[start] === b[start]) start++;
  let endA = a.length;
  let endB = b.length;
  while (endA > start && endB > start && a[endA - 1] === b[endB - 1]) { endA--; endB--; }
  a = a.slice(start, endA);
  b = b.slice(start, endB);
  if (a.length === 0 || b.length === 0) return { added: b.length, removed: a.length };
  if (a.length > LINE_DIFF_CAP || b.length > LINE_DIFF_CAP) {
    return { added: b.length, removed: a.length };
  }
  let prev = new Uint16Array(b.length + 1);
  let row = new Uint16Array(b.length + 1);
  for (let i = 1; i <= a.length; i++) {
    for (let j = 1; j <= b.length; j++) {
      row[j] = a[i - 1] === b[j - 1] ? prev[j - 1] + 1 : Math.max(prev[j], row[j - 1]);
    }
    [prev, row] = [row, prev];
  }
  const common = prev[b.length];
  return { added: b.length - common, removed: a.length - common };
}

interface PatchSection { op: "add" | "update" | "delete"; path: string; added: number; removed: number }

/** Sections of an apply_patch envelope, with their +/- line counts. */
export function parsePatch(patch: string): PatchSection[] {
  const sections: PatchSection[] = [];
  let cur: PatchSection | null = null;
  for (const line of patch.split("\n")) {
    const header = /^\*\*\* (Add|Update|Delete) File: (.+)$/.exec(line);
    if (header) {
      cur = { op: header[1].toLowerCase() as PatchSection["op"], path: header[2].trim(), added: 0, removed: 0 };
      sections.push(cur);
      continue;
    }
    if (!cur || line.startsWith("***")) continue;
    if (cur.op === "add") cur.added++;
    else if (cur.op === "update") {
      if (line.startsWith("+")) cur.added++;
      else if (line.startsWith("-")) cur.removed++;
    }
  }
  return sections;
}

function failedBlock(b: AssistantBlock): boolean {
  return b.is_error === true && b.outcome !== "cancelled";
}

function succeeded(b: AssistantBlock): boolean {
  return b.is_error !== true && b.outcome !== "failed" && b.outcome !== "cancelled"
    && b.outcome !== "not_started" && b.outcome !== "unknown";
}

/** Paths a block touches, with the op it performs. */
function blockPaths(b: AssistantBlock): string[] {
  const tool = b.tool || "";
  const args = parseToolArgs(b.input);
  if (tool === "apply_patch") return parsePatch(pick(args, "patch", "input") ?? "").map((s) => s.path);
  const path = filePath(args);
  return path ? [path] : [];
}

function kindOf(tool: string, text: Text, args: Args = {}): Kind {
  if (COMMAND.has(tool)) return "command";
  if (tool === "read" || tool === "pdf") return "read";
  if (tool === "list") return "list";
  if (SEARCH.has(tool)) return "search";
  if (tool === "web_search") return "websearch";
  if (tool === "web_fetch") return "fetch";
  if (BROWSER.has(tool)) return "browser";
  if (tool === "computer_use_agent") {
    return isFreshCall(args) ? "agent" : computerUseAgentTarget(args) === "browser" ? "browser" : "computer";
  }
  // browser_use is checked above: "operated the browser" says more than "GUI call".
  // The public computer_use tool shares the GUI Agent capability's name.
  if (GUI_TOOL_NAMES.has(tool)) return isGuiToolCall(tool, args) ? "gui" : "computer";
  if (AGENT.has(tool)) return "agent";
  if (tool === "image_generate") return "imagegen";
  if (tool === "image_analyze") return "imageanalyze";
  if (tool.startsWith("todo_")) return "todo";
  if (tool === "ask_user_question") return "ask";
  if (tool.startsWith("memory_")) return "memory";
  if (tool === "program") return "program";
  if (tool === "skill") return "skill";
  // Keep in step with the timeline rows for anything else.
  const tone = presentTool(tool, undefined, text).tone;
  if (tone === "shell") return "command";
  if (tone === "search") return "search";
  return "other";
}

function turnFileOp(turnFiles: TurnFileSummary | undefined, path: string): string | undefined {
  return turnFiles?.files.find((f) => f.path === path)?.op;
}

/** Whether a write creates its file: nothing earlier in the turn touched the
 *  path, and the settled turn summary (when present) agrees. */
function writeCreates(path: string, before: AssistantBlock[], turnFiles?: TurnFileSummary): boolean {
  const op = turnFileOp(turnFiles, path);
  if (op && op !== "create") return false;
  return !before.some((b) => b.type === "tool" && blockPaths(b).includes(path));
}

function fileOps(b: AssistantBlock, before: AssistantBlock[], turnFiles?: TurnFileSummary): FileOp[] {
  const tool = b.tool || "";
  const args = parseToolArgs(b.input);
  if (tool === "apply_patch") {
    return parsePatch(pick(args, "patch", "input") ?? "").map((s) => ({
      kind: s.op === "add" ? "create" : s.op === "delete" ? "delete" : "edit",
      path: s.path,
    }));
  }
  const path = filePath(args) ?? "";
  if (tool === "write" && path && writeCreates(path, before, turnFiles)) return [{ kind: "create", path }];
  return [{ kind: "edit", path }];
}

/** +/- for one successful writer call; null when it cannot be known from the call. */
function callStats(b: AssistantBlock, before: AssistantBlock[], turnFiles?: TurnFileSummary):
  Map<string, { added: number; removed: number } | null> {
  const out = new Map<string, { added: number; removed: number } | null>();
  const tool = b.tool || "";
  const args = parseToolArgs(b.input);
  if (tool === "apply_patch") {
    for (const s of parsePatch(pick(args, "patch", "input") ?? "")) {
      out.set(s.path, s.op === "delete" ? null : { added: s.added, removed: s.removed });
    }
    return out;
  }
  const path = filePath(args) ?? "";
  if (tool === "edit") {
    const oldText = typeof args.old_string === "string" ? args.old_string : null;
    const newText = typeof args.new_string === "string" ? args.new_string : null;
    if (oldText === null || newText === null) {
      out.set(path, null);
      return out;
    }
    const times = Number(/\((\d+) replacements?\)/.exec(b.result || "")?.[1] ?? 1) || 1;
    const d = lineDiff(oldText, newText);
    out.set(path, { added: d.added * times, removed: d.removed * times });
    return out;
  }
  // write
  const content = typeof args.content === "string" ? args.content : null;
  out.set(path, content !== null && path && writeCreates(path, before, turnFiles)
    ? { added: countLines(content), removed: 0 }
    : null);
  return out;
}

function badge(
  group: AssistantBlock[],
  turn: AssistantBlock[],
  turnFiles?: TurnFileSummary,
): { added: number | null; removed: number | null } {
  const none = { added: null, removed: null };
  const groupSet = new Set(group);
  const writers = group.filter((b) => b.type === "tool" && FILE_WRITERS.has(b.tool || "") && succeeded(b));
  if (writers.length === 0) return none;
  // Per path: the settled turn summary when every write to it is in this
  // group, otherwise the sum of what each call says.
  const perPath = new Map<string, { added: number; removed: number } | null>();
  const authoritative = new Set<string>();
  for (const b of writers) {
    const before = turn.slice(0, Math.max(0, turn.indexOf(b)));
    for (const [path, stats] of callStats(b, before, turnFiles)) {
      if (authoritative.has(path)) continue;
      const row = path ? turnFiles?.files.find((f) => f.path === path) : undefined;
      const onlyHere = row && turn.every((t) => groupSet.has(t) || t.type !== "tool"
        || !FILE_WRITERS.has(t.tool || "") || !blockPaths(t).includes(path));
      if (row && onlyHere && row.added !== null && row.removed !== null) {
        perPath.set(path, { added: row.added, removed: row.removed });
        authoritative.add(path);
        continue;
      }
      const prev = perPath.get(path);
      if (prev === null) continue;
      perPath.set(path, stats && prev !== undefined
        ? { added: prev.added + stats.added, removed: prev.removed + stats.removed }
        : stats);
    }
  }
  let added = 0;
  let removed = 0;
  for (const stats of perPath.values()) {
    if (!stats) return none;
    added += stats.added;
    removed += stats.removed;
  }
  return { added, removed };
}

function bucketCount(bucket: Bucket): number {
  return bucket.targets.size + bucket.anonymous;
}

function single(bucket: Bucket): string | undefined {
  return bucket.anonymous === 0 && bucket.targets.size === 1 ? [...bucket.targets][0] : undefined;
}

function display(kind: Kind, target: string): string {
  if (kind === "fetch") return host(target);
  if (kind === "search") return quote(target);
  if (kind === "create" || kind === "edit" || kind === "delete" || kind === "read") return basename(target);
  return target;
}

function pastPhrase(bucket: Bucket, text: Text): string {
  const n = bucketCount(bucket);
  const calls = bucket.calls;
  const one = single(bucket);
  const named = one !== undefined ? display(bucket.kind, one) : undefined;
  switch (bucket.kind) {
    case "command":
      return calls === 1 ? text("ran a command", "运行了 1 条命令") : text(`ran ${calls} commands`, `运行了 ${calls} 条命令`);
    case "create":
      return named ? text(`created ${named}`, `创建了 ${named}`) : text(`created ${n} files`, `创建了 ${n} 个文件`);
    case "edit":
      return named ? text(`edited ${named}`, `修改了 ${named}`)
        : n === 1 ? text("edited a file", "修改了 1 个文件") : text(`edited ${n} files`, `修改了 ${n} 个文件`);
    case "delete":
      return named ? text(`deleted ${named}`, `删除了 ${named}`) : text(`deleted ${n} files`, `删除了 ${n} 个文件`);
    case "read":
      return named ? text(`read ${named}`, `读取了 ${named}`)
        : n === 1 ? text("read a file", "读取了 1 个文件") : text(`read ${n} files`, `读取了 ${n} 个文件`);
    case "list":
      return calls === 1 ? text("listed a folder", "列出了 1 个目录") : text(`listed ${calls} folders`, `列出了 ${calls} 个目录`);
    case "search":
      return calls === 1 && named ? text(`searched for ${named}`, `搜索了${named}`)
        : calls === 1 ? text("ran a search", "搜索了 1 次") : text(`ran ${calls} searches`, `搜索了 ${calls} 次`);
    case "websearch":
      return calls === 1 ? text("searched the web", "搜索了网页") : text(`searched the web ${calls} times`, `搜索了 ${calls} 次网页`);
    case "fetch":
      return named ? text(`fetched ${named}`, `抓取了 ${named}`)
        : n === 1 ? text("fetched a page", "抓取了 1 个网页") : text(`fetched ${n} pages`, `抓取了 ${n} 个网页`);
    case "browser":
      return text("operated the browser", "操作了浏览器");
    case "gui":
      return calls === 1 ? text("ran a GUI call", "执行了 1 次 GUI 调用") : text(`ran ${calls} GUI calls`, `执行了 ${calls} 次 GUI 调用`);
    case "computer":
      return named ? text(`operated ${named}`, `操作了 ${named}`)
        : bucket.targets.size > 1 ? text(`operated ${bucket.targets.size} apps`, `操作了 ${bucket.targets.size} 个应用`)
        : text("operated an app", "操作了应用");
    case "agent":
      return named ? text(`ran sub-agent ${named}`, `运行了子代理 ${named}`)
        : n === 1 ? text("ran a sub-agent", "运行了 1 个子代理") : text(`ran ${n} sub-agents`, `运行了 ${n} 个子代理`);
    case "peer":
      return n === 1 ? text("messaged another conversation", "给另一个会话发了消息")
        : text(`messaged ${n} conversations`, `给 ${n} 个会话发了消息`);
    case "imagegen":
      return calls === 1 ? text("generated an image", "生成了 1 张图片") : text(`generated ${calls} images`, `生成了 ${calls} 张图片`);
    case "imageanalyze":
      return calls === 1 ? text("analyzed an image", "分析了 1 张图片") : text(`analyzed ${calls} images`, `分析了 ${calls} 张图片`);
    case "todo":
      return text("updated todos", "更新了待办");
    case "ask":
      return calls === 1 ? text("asked a question", "提了 1 个问题") : text(`asked ${calls} questions`, `提了 ${calls} 个问题`);
    case "memory":
      return text("accessed memory", "读写了记忆");
    case "program":
      return named ? text(`ran program ${named}`, `运行了程序 ${named}`)
        : n === 1 ? text("ran a program", "运行了 1 个程序") : text(`ran ${n} programs`, `运行了 ${n} 个程序`);
    case "skill":
      return named ? text(`used skill ${named}`, `使用了技能 ${named}`)
        : n === 1 ? text("used a skill", "使用了 1 个技能") : text(`used ${n} skills`, `使用了 ${n} 个技能`);
    case "other":
      if (named) return calls === 1 ? text(`used ${named}`, `使用了 ${named}`) : text(`used ${named} ${calls} times`, `使用了 ${named} ${calls} 次`);
      if (bucket.anonymous === 0 && bucket.targets.size === 2) {
        const [a, b] = [...bucket.targets];
        return text(`used ${a} and ${b}`, `使用了 ${a} 和 ${b}`);
      }
      return n === 1 ? text("used a tool", "使用了 1 个工具") : text(`used ${n} tools`, `使用了 ${n} 个工具`);
  }
}

function presentPhrase(bucket: Bucket, text: Text): string {
  const t = bucket.running ? display(bucket.kind, bucket.running) : "";
  switch (bucket.kind) {
    case "command": return text("running a command", "正在运行命令");
    case "create": return t ? text(`creating ${t}`, `正在创建 ${t}`) : text("creating a file", "正在创建文件");
    case "edit": return t ? text(`editing ${t}`, `正在修改 ${t}`) : text("editing a file", "正在修改文件");
    case "delete": return t ? text(`deleting ${t}`, `正在删除 ${t}`) : text("deleting a file", "正在删除文件");
    case "read": return t ? text(`reading ${t}`, `正在读取 ${t}`) : text("reading a file", "正在读取文件");
    case "list": return text("listing a folder", "正在列出目录");
    case "search": return t ? text(`searching for ${t}`, `正在搜索${t}`) : text("searching", "正在搜索");
    case "websearch": return text("searching the web", "正在搜索网页");
    case "fetch": return t ? text(`fetching ${t}`, `正在抓取 ${t}`) : text("fetching a page", "正在抓取网页");
    case "browser": return text("operating the browser", "正在操作浏览器");
    case "gui": return text("running a GUI call", "正在执行 GUI 调用");
    case "computer": return t ? text(`operating ${t}`, `正在操作 ${t}`) : text("operating an app", "正在操作应用");
    case "agent": return t ? text(`running sub-agent ${t}`, `正在运行子代理 ${t}`) : text("running a sub-agent", "正在运行子代理");
    case "peer": return text("messaging another conversation", "正在给另一个会话发消息");
    case "imagegen": return text("generating an image", "正在生成图片");
    case "imageanalyze": return text("analyzing an image", "正在分析图片");
    case "todo": return text("updating todos", "正在更新待办");
    case "ask": return text("asking a question", "正在提问");
    case "memory": return text("accessing memory", "正在读写记忆");
    case "program": return t ? text(`running program ${t}`, `正在运行程序 ${t}`) : text("running a program", "正在运行程序");
    case "skill": return t ? text(`using skill ${t}`, `正在使用技能 ${t}`) : text("using a skill", "正在使用技能");
    case "other": return t ? text(`using ${t}`, `正在使用 ${t}`) : text("using a tool", "正在使用工具");
  }
}

/** Identifying target of a call for its bucket, "" when it has none. */
function targetOf(kind: Kind, tool: string, args: Args): string {
  switch (kind) {
    case "read": return filePath(args) ?? "";
    case "search": return pick(args, "pattern", "query", "regex", "glob", "symbol") ?? "";
    case "fetch": return pick(args, "url") ?? "";
    case "agent": return "";
    case "peer": return "";
    case "program": return pick(args, "name", "program", "path") ?? "";
    case "skill": return pick(args, "name", "skill") ?? "";
    case "computer":
      if (tool === "computer_use_agent") {
        const target = computerUseAgentTarget(args);
        if (target === "vm") return "VM";
        if (target === "browser") return userBrowser(args) ?? "";
        return pick(appStepArgs(args) ?? {}, "app") ?? "";
      }
      return pick(args, "app") ?? "";
    case "other": return tool;
    default: return "";
  }
}


/** Where the spawned agents stand: "done" for one, counts for several.
 *  A lone running agent says so in the phrase itself. */
function spawnStatusNote(states: string[], text: Text): string {
  if (states.length === 0) return "";
  const live = states.filter((s) => isAgentLive(s)).length;
  const failed = states.filter((s) => s === "errored" || s === "failed").length;
  const cancelled = states.filter((s) => s === "cancelled").length;
  const done = states.length - live - failed - cancelled;
  if (states.length === 1) {
    return live ? "" : failed ? text("failed", "失败")
      : cancelled ? text("cancelled", "已取消") : text("done", "已完成");
  }
  return [
    done ? text(`${done} done`, `${done} 个已完成`) : "",
    live ? text(`${live} running`, `${live} 个运行中`) : "",
    failed ? text(`${failed} failed`, `${failed} 个失败`) : "",
    cancelled ? text(`${cancelled} cancelled`, `${cancelled} 个已取消`) : "",
  ].filter(Boolean).join(text(", ", "，"));
}

function capitalise(s: string): string {
  return s.charAt(0).toUpperCase() + s.slice(1);
}

/** Summarise one folded group of thinking and tool blocks. */
export function summarizeToolGroup(blocks: AssistantBlock[], options: ToolGroupOptions): ToolGroupSummary {
  const { text, active = false, runningIds, spawnNames = [], spawnStates = [], spawnHeads = [], sessionId, turnFiles } = options;
  const turn = options.turnBlocks ?? blocks;
  const buckets = new Map<Kind, Bucket>();
  let thinking = 0;
  let failed = 0;
  let order = 0;
  let spawnBlocks = 0;
  // Agents a send_message reached, by branch head or name. A message, or a
  // watch, talks to an agent that already exists: several to one agent, or
  // to one this group spawned, are still that one agent.
  const messaged = new Set<string>();
  const bucket = (kind: Kind): Bucket => {
    let b = buckets.get(kind);
    if (!b) {
      b = { kind, first: order, calls: 0, targets: new Set(), anonymous: 0 };
      buckets.set(kind, b);
    }
    return b;
  };
  blocks.forEach((b) => {
    order++;
    if (b.type === "thinking") { thinking++; return; }
    if (b.type !== "tool") return;
    if (failedBlock(b)) failed++;
    const tool = b.tool || "";
    const running = active && !!b.tool_call_id && !!runningIds?.has(b.tool_call_id);
    if (FILE_WRITERS.has(tool)) {
      const before = turn.slice(0, Math.max(0, turn.indexOf(b)));
      for (const op of fileOps(b, before, turnFiles)) {
        const target = bucket(op.kind);
        target.calls++;
        if (op.path) target.targets.add(op.path);
        else target.anonymous++;
        if (running) target.running = op.path;
      }
      return;
    }
    const args = parseToolArgs(b.input);
    // A send the tool turned down reached no one.
    if (tool === "send_message" && (b.result ?? "").startsWith("[send_message ")) return;
    const to = tool === "send_message" ? pick(args, "to") : undefined;
    const conversation = to?.includes(":") ? to.slice(0, to.indexOf(":")) : undefined;
    if (sessionId && conversation && conversation !== sessionId) {
      // Another conversation is a peer, not a sub-agent: count it once
      // however many messages and watches went to it.
      const peer = bucket("peer");
      peer.calls++;
      peer.targets.add(conversation);
      if (running) peer.running = "";
      return;
    }
    const kind = kindOf(tool, text, args);
    const target = bucket(kind);
    target.calls++;
    if (tool === "send_message") {
      const to = pick(args, "to");
      if (to) messaged.add(to.slice(to.lastIndexOf(":") + 1));
      else spawnBlocks++;
    } else if (kind === "agent") spawnBlocks++;
    const value = targetOf(kind, tool, args);
    if (value) target.targets.add(value);
    else if (kind !== "agent") target.anonymous++;
    if (running) target.running = value;
  });
  const known = new Set([...spawnNames, ...spawnHeads.filter((h): h is string => !!h)]);
  const others = [...messaged].filter((key) => !known.has(key)).length;
  if (spawnNames.length > 0 || spawnBlocks > 0 || others > 0) {
    const agents = bucket("agent");
    spawnNames.forEach((name) => agents.targets.add(name));
    agents.anonymous = Math.max(0, Math.max(spawnBlocks, spawnNames.length) - agents.targets.size) + others;
    // A lone agent still at work reads as the group's live phrase.
    if (spawnStates.length === 1 && isAgentLive(spawnStates[0]) && agents.running === undefined) {
      agents.running = spawnNames[0] ?? "";
    }
  }
  const ordered = [...buckets.values()].sort((a, b) => a.first - b.first);
  const current = ordered.filter((b) => b.running !== undefined);
  const live = current[current.length - 1];
  const phrases = ordered.filter((b) => b !== live).map((b) => pastPhrase(b, text));
  if (live) phrases.push(presentPhrase(live, text) + "…");
  let label: string;
  if (phrases.length > 0) label = capitalise(phrases.join(text(", ", "，")));
  else if (thinking > 0) label = active ? text("Thinking…", "思考中…") : text("Thought", "思考");
  else label = text("Execution", "执行过程");
  const states = spawnStatusNote(spawnStates, text);
  if (states) label += " · " + states;
  return { label, failed, ...badge(blocks, turn, turnFiles) };
}
