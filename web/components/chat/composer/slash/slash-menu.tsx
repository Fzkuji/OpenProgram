"use client";

/**
 * Slash command popover rendered above the composer textarea.
 *
 * Pure presentation — every piece of menu state comes from the
 * caller's ``useSlashMenu`` hook. Extracted from composer/index.tsx
 * so the main file isn't carrying the matches list + scroll-into-view
 * + open/close animation classes inline.
 */
import type { SlashCommand } from "./slash-commands";
import styles from "../composer.module.css";
import { useTranslation } from "@/lib/i18n";

interface SlashMenuProps {
  visible: boolean;
  closing: boolean;
  matches: SlashCommand[];
  activeIndex: number;
  onPick: (cmd: SlashCommand) => void;
}

export function SlashMenu({
  visible,
  closing,
  matches,
  activeIndex,
  onPick,
}: SlashMenuProps) {
  const { text } = useTranslation();
  if (!visible) return null;
  return (
    <div
      className={`${styles.slashMenu} ${closing ? styles.closing : styles.opening}`}
    >
      {matches.map((c, i) => (
        <div
          key={c.name}
          ref={
            // Scroll the keyboard-highlighted item into view when
            // arrow nav drives it off-screen. Mouse hover no
            // longer touches activeIndex (the CSS :hover state
            // alone provides hover feedback), so this fires
            // only on keyboard moves — no more jiggle when the
            // cursor drifts onto a bottom item.
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
          <span className={styles.slashMenuName}>{c.name}</span>
          {c.args ? (
            <>
              {" "}
              <span className={styles.slashMenuArgs}>{c.args}</span>
            </>
          ) : null}
          <div className={styles.slashMenuDesc}>{slashDescription(c, text)}</div>
        </div>
      ))}
    </div>
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
        "Run another agent in this session as a new branch. Default inherits this conversation; --clean starts at a new root. Add --async to spawn the task to the background runner.",
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
