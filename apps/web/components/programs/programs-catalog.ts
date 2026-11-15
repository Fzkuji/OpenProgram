import type { ProgramKind } from "./programs-logic";

export type ProgramExplorerEntry = {
  name: string;
  path: string;
  kind: "folder" | "file";
  source_path?: string;
  program_kind: ProgramKind | null;
  has_children: boolean;
  logic_path?: string | null;
  description?: string;
  callable_name?: string;
  /** Applications: built in (openprogram/programs/applications) or installed. */
  origin?: "builtin" | "installed";
  /** Applications whose setting the host tracks. */
  enabled?: boolean;
  title?: string;
  application_id?: string;
  /** Installed packages: load state, error, and what to do next. */
  load_state?: "loaded" | "pending" | "error";
  load_error?: string;
  load_note?: string;
  /** Applications without an Agent entry: why there is nothing to Use. */
  note?: ApplicationNote;
  /** Applications: the glyph and version they declare, and what holds them. */
  icon?: string;
  version?: string;
  form?: "package" | "application" | "harness";
  /** Applications: every entry the Agent can call, with its one-line description. */
  agent_entries?: { name: string; description: string; tool?: string }[];
  /** The source directory, shortened: checkout-relative or ``~/…``. */
  location?: string;
  /** Tools: their arguments. */
  parameters?: { name: string; description: string; required: boolean }[];
};

/** ``ui_only``: an application opened from Applications. ``library``: a
 *  package of Python functions for other Programs. */
export type ApplicationNote =
  | { kind: "ui_only" }
  | { kind: "library"; exports?: string[] };

type Translate = (en: string, zh: string) => string;

const KEPT_VERSION_NOTE = /^Kept version (\S+); version (\S+) failed\.$/;

/** A package whose new version failed while the previous one keeps serving.
 *  `message` is the reason without its exception type or file location. */
export function keptVersionFailure(entry: ProgramExplorerEntry, text: Translate) {
  const matched = entry.load_state === "error" ? KEPT_VERSION_NOTE.exec(entry.load_note ?? "") : null;
  if (!matched) return null;
  const [, kept, failed] = matched;
  const fullError = entry.load_error ?? "";
  const reason = fullError.split("\n", 1)[0]
    .replace(/\s+\([^()]*:\d+\)$/, "")
    .replace(/^[A-Za-z_][\w.]*(?:Error|Exception|Warning|Exit|Interrupt):\s*/, "")
    .replace(/[.。]$/, "");
  return {
    title: text(`Kept version ${kept}; version ${failed} failed.`, `保留了版本 ${kept}；版本 ${failed} 加载失败。`),
    message: text(
      `It could not load: ${reason}. This Program keeps working on version ${kept} until the error is fixed.`,
      `它加载失败：${reason}。修好之前，这个 Program 继续用版本 ${kept} 工作。`,
    ),
    fullError,
  };
}

/** Short label for the detail header, after the application's origin. */
export function applicationNoteLabel(note: ApplicationNote | null | undefined, text: Translate): string {
  if (note?.kind === "ui_only") return text("UI only", "仅界面");
  if (note?.kind === "library") return text("Python library", "Python 库");
  return "";
}

/** The neutral note shown in place of a Use button. */
export function applicationNoteText(
  note: ApplicationNote | null | undefined,
  text: Translate,
): { title: string; lines: string[] } | null {
  if (note?.kind === "ui_only") {
    return {
      title: text("No Agent entry; UI-only application", "没有 Agent 入口；仅界面应用"),
      lines: [text("Open it from Applications.", "从“应用”中打开它。")],
    };
  }
  if (note?.kind !== "library") return null;
  const lines: string[] = [];
  if (note.exports?.length) {
    lines.push(text(
      `A Python library for other Programs: ${note.exports.join(", ")}.`,
      `供其他 Program 调用的 Python 库：${note.exports.join("、")}。`,
    ));
  }
  return { title: text("No Agent entry; registers no tools", "没有 Agent 入口；不注册工具"), lines };
}

/** "Built-in application", "Installed package", "Built-in tool", … for the detail meta line. */
export function programKindLabel(
  entry: Pick<ProgramExplorerEntry, "program_kind" | "origin" | "form" | "application_id"> & { hasWindow?: boolean },
  text: Translate,
): string {
  if (entry.program_kind === "tool") return text("Built-in tool", "内置工具");
  if (entry.program_kind !== "application") return text("Source module", "源码模块");
  const application = entry.form !== "package" || Boolean(entry.application_id) || entry.origin === "builtin" || entry.hasWindow;
  if (entry.origin === "builtin") return text("Built-in application", "内置应用");
  if (entry.origin === "installed") return application ? text("Installed application", "已安装应用") : text("Installed package", "已安装包");
  return application ? text("Application", "应用") : text("Package", "包");
}

/** An application's capabilities as one plain phrase, or "" when it declares none. */
export function applicationPermissions(capabilities: readonly string[] | undefined, text: Translate): string {
  const has = (name: string) => Boolean(capabilities?.includes(name));
  const parts: string[] = [];
  if (has("files.project.write")) parts.push(text("Read and write project files", "读写项目文件"));
  else if (has("files.project.read")) parts.push(text("Read project files", "读取项目文件"));
  if (has("model.invoke")) parts.push(text("call the model", "调用模型"));
  if (has("storage.app")) parts.push(text("keep its own data", "保存自己的数据"));
  if (!parts.length) return "";
  const phrase = parts.join(text(", ", "，"));
  return phrase.charAt(0).toUpperCase() + phrase.slice(1);
}

/** The short count on the collapsed Call logic disclosure. */
export function callLogicSummary(
  logic: { root: string; nodes: unknown[]; edges: { source: string }[]; entries?: string[] } | null | undefined,
  text: Translate,
): string {
  if (!logic) return "";
  const entries = logic.entries?.length ?? 0;
  if (entries > 1) return text(`${entries} entries`, `${entries} 个入口`);
  const direct = logic.edges.filter(edge => edge.source === logic.root).length;
  if (!direct) return text("no calls to other Programs", "不调用其他 Program");
  return direct === 1 ? text("1 direct call", "1 个直接调用") : text(`${direct} direct calls`, `${direct} 个直接调用`);
}

export function programInvocationName(
  entry: { name: string; callable_name?: string } | null | undefined,
): string {
  return entry?.callable_name || entry?.name || "";
}
