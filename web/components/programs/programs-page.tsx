"use client";

/**
 * /programs — Agentic programs catalog (placeholder).
 *
 * Programs are a different shape from functions: a program is software
 * with its own surface — the user launches it and works inside it,
 * rather than the agent invoking it as a tool call mid-conversation.
 * Every program is LLM-driven (that's the point of hosting it here),
 * so there is no with/without-LLM split like on the Functions page.
 *
 * No real programs exist yet — this page reserves the slot with the
 * same page furniture as /functions so the first real program drops
 * into a familiar frame.
 */
import { useRef } from "react";
import styles from "./programs-page.module.css";
import { useTranslation } from "@/lib/i18n";
import {
  MonitorIcon,
  type AnimatedNavIconHandle,
} from "../animated-icons";

export function ProgramsPage() {
  const { text } = useTranslation();
  const iconRef = useRef<AnimatedNavIconHandle>(null);
  return (
    <div className={styles.page}>
      <div className={styles.header}>
        <h1 className={styles.title}>{text("Programs", "程序")}</h1>
        <p className={styles.subtitle}>
          {text(
            "LLM-powered software with its own interface — launched and used directly, not invoked as a tool call. Functions run inside a conversation; programs run as their own thing.",
            "带有独立界面的 LLM 软件——由你直接启动和使用，而不是在对话里被作为工具调用。函数在对话内运行；程序作为独立软件运行。",
          )}
        </p>
      </div>
      <div className={styles.grid}>
        <div
          className={styles.placeholderCard}
          onMouseEnter={() => iconRef.current?.startAnimation?.()}
          onMouseLeave={() => iconRef.current?.stopAnimation?.()}
        >
          <div className={styles.placeholderIcon}>
            <MonitorIcon ref={iconRef} size={22} />
          </div>
          <div className={styles.placeholderName}>example_program</div>
          <div className={styles.placeholderDesc}>
            {text(
              "The first programs are on the way. A program ships as its own repo, installs like the agent harnesses, and appears here with a Launch button.",
              "首批程序正在路上。程序以独立仓库发布，安装方式与 agent harness 相同，安装后出现在这里并带有启动按钮。",
            )}
          </div>
          <div className={styles.placeholderBadge}>
            {text("Coming soon", "即将推出")}
          </div>
        </div>
      </div>
    </div>
  );
}
