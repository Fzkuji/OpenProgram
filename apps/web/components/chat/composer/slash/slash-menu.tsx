"use client";

/**
 * Slash command popover, the same popover as the ``@`` menu
 * (attach/mention/file-menu.tsx): glass panel, one 24px row per command
 * with its icon, arguments and description in muted text, opened upward from
 * the field at the "/".
 *
 * Pure presentation — every piece of menu state comes from the caller's
 * ``useSlashMenu`` hook. A zero-size marker sits in the composer
 * (``.slashClip``) so the menu can find its own input; the menu itself
 * is portalled to the page so no ancestor clips it.
 */
import { useLayoutEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import type { SlashCommand } from "./slash-commands";
import styles from "../composer.module.css";
import { useTranslation } from "@/lib/i18n";
import { SolarIcon, type SolarIconName } from "@/components/solar-icons";

/* Each built-in command has its own glyph; any other command takes its
   source's default, so a skill reads as a skill at a glance. */
const COMMAND_ICONS: Record<string, SolarIconName> = {
  "/compact": "layers",
  "/context": "widget-2",
  "/clear": "pen-new-square",
  "/new": "add-square",
  "/branch": "git-branch",
  "/skill": "stars",
  "/memory": "book-2",
  "/task": "users-group-rounded",
  "/merge": "git-pull-request",
  "/help": "question-circle",
  "/sandbox": "shield-check",
  "/rewind": "history",
  "/doctor": "sledgehammer",
  "/goal": "target",
  "/commit-message": "git-graph",
};
const SOURCE_ICONS: Record<string, SolarIconName> = {
  skill: "stars",
  plugin: "plug-circle",
  user: "code-square",
  project: "code-square",
};

function commandIcon(c: SlashCommand): SolarIconName {
  return COMMAND_ICONS[c.name] ?? SOURCE_ICONS[c.source ?? ""] ?? "code-square";
}

interface SlashMenuProps {
  visible: boolean;
  closing: boolean;
  matches: SlashCommand[];
  activeIndex: number;
  onPick: (cmd: SlashCommand) => void;
}

type CaretField = HTMLElement & { coordsAt?: (pos: number) => { left: number; top: number } | null };

export function SlashMenu({
  visible,
  closing,
  matches,
  activeIndex,
  onPick,
}: SlashMenuProps) {
  const { text } = useTranslation();
  const markerRef = useRef<HTMLSpanElement | null>(null);
  const [pos, setPos] = useState<{ left: number; bottom: number } | null>(null);
  const shown = visible && !closing && matches.length > 0;

  // Same anchor as the @ menu: 6px above the field, left edge at the "/".
  useLayoutEffect(() => {
    if (!shown) return;
    const place = () => {
      const stack = markerRef.current?.parentElement?.parentElement;
      const field = stack?.querySelector<HTMLElement>("[data-composer-input]");
      if (!field) return;
      const rect = field.getBoundingClientRect();
      const slash = (field as CaretField).coordsAt?.(0);
      setPos({
        left: Math.max(8, Math.min(slash?.left ?? rect.left + 8, window.innerWidth - 268)),
        bottom: Math.max(8, window.innerHeight - rect.top + 6),
      });
    };
    place();
    window.addEventListener("resize", place);
    return () => window.removeEventListener("resize", place);
  }, [shown, matches.length]);

  const menu = shown && pos && typeof document !== "undefined"
    ? createPortal(
      <div
        role="listbox"
        className={styles.slashMenu}
        style={{ left: pos.left, bottom: pos.bottom }}
        onMouseDown={(e) => e.preventDefault()}
      >
        {matches.map((c, i) => (
          <div
            key={c.name}
            role="option"
            aria-selected={i === activeIndex}
            ref={
              // Keep the keyboard pick in view; hover is CSS only, so this
              // fires on arrow keys and never jiggles under the pointer.
              i === activeIndex
                ? (el) => el?.scrollIntoView({ block: "nearest" })
                : undefined
            }
            className={`${styles.slashMenuItem} ${i === activeIndex ? styles.slashMenuItemActive : ""}`}
            onMouseDown={(e) => {
              e.preventDefault();
              onPick(c);
            }}
          >
            <span aria-hidden className={styles.slashMenuIcon}>
              <SolarIcon name={commandIcon(c)} size={16} fit="fill" />
            </span>
            <span className={styles.slashMenuName}>{c.name}</span>
            {c.args ? <span className={styles.slashMenuArgs}>{c.args}</span> : null}
            <span className={styles.slashMenuDesc}>{slashDescription(c, text)}</span>
          </div>
        ))}
      </div>,
      document.body,
    )
    : null;

  return (
    <>
      <span ref={markerRef} hidden />
      {menu}
    </>
  );
}

function slashDescription(c: SlashCommand, text: (en: string, zh: string) => string): string {
  switch (c.name) {
    case "/compact":
      return text(
        "Summarise older history; keep recent N tokens verbatim (default: window-adaptive)",
        "总结较早历史；最近 N 个 token 原样保留（默认按窗口自适应）",
      );
    case "/clear":
      return text('Start a fresh conversation (equivalent to "New chat")', "开始新会话（等同于“新会话”）");
    case "/new":
      return text("Alias of /clear - open a brand-new conversation", "/clear 的别名，打开一个全新会话");
    case "/branch":
      return text("Branch the current conversation from this point", "从当前位置创建当前会话的分支");
    case "/skill":
      return text("Run a registered skill by name", "按名称运行已注册技能");
    case "/memory":
      return text("Open the memory page in a new tab", "在新标签页打开记忆页面");
    case "/task":
      return text(
        "Run another agent in this session as a new branch. Default inherits this conversation; --clean starts at a new root. Add --async to spawn a background job.",
        "在当前会话中以新分支运行另一个 Agent。默认继承当前会话；--clean 从新根开始；--async 放到后台运行。",
      );
    case "/merge":
      return text(
        "Merge N peer branches into a reply on this session. Same-session: 'sid:head_id'. Mark base with '*' prefix.",
        "把多个同级分支合并为当前会话的一条回复。同会话格式为 sid:head_id；用 * 前缀标记基准。",
      );
    case "/help":
      return text("Open the command list (built-ins, plugin commands, skills)", "打开命令列表（内置、插件命令、技能）");
    case "/doctor":
      return text("Run health checks: python, node, skills, plugins, providers, mcp, cache", "运行健康检查：Python、Node、技能、插件、Provider、MCP、缓存");
    default:
      return c.description;
  }
}
