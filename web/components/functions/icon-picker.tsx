"use client";

import { useEffect, useRef } from "react";
import styles from "./icon-picker.module.css";
import { useTranslation } from "@/lib/i18n";
import {
  type AnimatedNavIconHandle,
  ActivityIcon,
  BookTextIcon,
  BotIcon,
  BoxIcon,
  BoxesIcon,
  BrainIcon,
  ChartColumnIncreasingIcon,
  ChromeIcon,
  FileTextIcon,
  FlameIcon,
  FolderCodeIcon,
  GitBranchIcon,
  HeartIcon,
  LayersIcon,
  MessageCircleIcon,
  MonitorCheckIcon,
  RocketIcon,
  SearchIcon,
  SparklesIcon,
  SquarePenIcon,
  TerminalIcon,
  WorkflowIcon,
  WrenchIcon,
  ZapIcon,
} from "@/components/animated-icons";

/** The flat animated line icons (app-wide pqoqubbw set) a function
 *  card can use. programs-meta stores the slug key. */
export const FUNCTION_ICONS = {
  box: BoxIcon,
  bot: BotIcon,
  chrome: ChromeIcon,
  search: SearchIcon,
  "book-text": BookTextIcon,
  "monitor-check": MonitorCheckIcon,
  "file-text": FileTextIcon,
  "chart-column": ChartColumnIncreasingIcon,
  "square-pen": SquarePenIcon,
  wrench: WrenchIcon,
  zap: ZapIcon,
  flame: FlameIcon,
  rocket: RocketIcon,
  sparkles: SparklesIcon,
  brain: BrainIcon,
  "message-circle": MessageCircleIcon,
  terminal: TerminalIcon,
  heart: HeartIcon,
  "folder-code": FolderCodeIcon,
  boxes: BoxesIcon,
  workflow: WorkflowIcon,
  "git-branch": GitBranchIcon,
  layers: LayersIcon,
  activity: ActivityIcon,
} as const;

export type FunctionIconSlug = keyof typeof FUNCTION_ICONS;

export const DEFAULT_ICON: FunctionIconSlug = "box";

/** programs-meta values stored before the emoji → flat-icon switch.
 *  Map each legacy emoji onto the closest slug so an old pick still
 *  resolves to an icon instead of silently falling back to default. */
const LEGACY_EMOJI_TO_SLUG: Record<string, FunctionIconSlug> = {
  "📦": "box",
  "🤖": "bot",
  "🌐": "chrome",
  "🔍": "search",
  "📚": "book-text",
  "🖥": "monitor-check",
  "📄": "file-text",
  "📊": "chart-column",
  "🎨": "sparkles",
  "✏️": "square-pen",
  "🛠": "wrench",
  "⚡": "zap",
  "💡": "zap",
  "🔥": "flame",
  "⭐": "heart",
  "🎯": "activity",
  "📷": "monitor-check",
  "🎵": "activity",
  "🧠": "brain",
  "💬": "message-circle",
  "🎮": "rocket",
  "🚀": "rocket",
  "🧪": "sparkles",
  "✨": "sparkles",
};

/** Resolve whatever programs-meta holds (slug, legacy emoji, or
 *  nothing) to a valid slug. */
export function normalizeIcon(value: string | undefined | null): FunctionIconSlug {
  if (!value) return DEFAULT_ICON;
  if (value in FUNCTION_ICONS) return value as FunctionIconSlug;
  return LEGACY_EMOJI_TO_SLUG[value] ?? DEFAULT_ICON;
}

export function IconPicker({
  name,
  current,
  onPick,
  onClose,
}: {
  name: string;
  current: string;
  onPick: (icon: string | null) => void;
  onClose: () => void;
}) {
  const { text } = useTranslation();
  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") onClose();
    }
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [onClose]);

  return (
    <div className={styles.overlay} onClick={onClose}>
      <div className={styles.picker} onClick={(e) => e.stopPropagation()}>
        <div className={styles.head}>
          <span className={styles.title}>
            {text("Pick an icon for", "为以下函数选择图标")} <code>{name}</code>
          </span>
          <div className={styles.actions}>
            <button
              type="button"
              className={styles.btnGhost}
              onClick={() => onPick(null)}
              title={text("Reset to default", "恢复默认")}
            >
              {text("Reset", "重置")}
            </button>
            <button
              type="button"
              className={styles.btnGhost}
              onClick={onClose}
              title={text("Close", "关闭")}
            >
              {text("Close", "关闭")}
            </button>
          </div>
        </div>
        <div className={styles.grid}>
          {(Object.keys(FUNCTION_ICONS) as FunctionIconSlug[]).map((slug) => (
            <IconCell
              key={slug}
              slug={slug}
              active={slug === current}
              onPick={() => onPick(slug)}
            />
          ))}
        </div>
      </div>
    </div>
  );
}

/** One picker cell — the button's hover drives the icon animation
 *  (controlled-ref pattern, same as everywhere else in the app). */
function IconCell({
  slug,
  active,
  onPick,
}: {
  slug: FunctionIconSlug;
  active: boolean;
  onPick: () => void;
}) {
  const iconRef = useRef<AnimatedNavIconHandle>(null);
  const Icon = FUNCTION_ICONS[slug];
  return (
    <button
      type="button"
      className={active ? `${styles.btn} ${styles.btnActive}` : styles.btn}
      onClick={onPick}
      onMouseEnter={() => iconRef.current?.startAnimation?.()}
      onMouseLeave={() => iconRef.current?.stopAnimation?.()}
    >
      <Icon ref={iconRef} size={20} />
    </button>
  );
}
