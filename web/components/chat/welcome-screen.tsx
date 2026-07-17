/**
 * WelcomeScreen — empty-chat centered greeting.
 *
 * OpenProgram 自己的问候组合：居中的 {LLM} 打字动画 logo + 一句自家
 * tagline。不用 claude.ai 的"图标 + What's up next?"左上角组合——那是
 * 它的商标性排布，句子和位置都得是我们自己的。Visible whenever the
 * session store says so; mounted as a portal inside the #welcome-mount
 * placeholder that PageShell leaves in the chat area.
 */
"use client";

import { useSessionStore } from "@/lib/session-store";
import { useTranslation } from "@/lib/i18n";

import styles from "./welcome-screen.module.css";

export function WelcomeScreen() {
  const visible = useSessionStore((s) => s.welcomeVisible);
  const { text } = useTranslation();

  if (!visible) return null;

  return (
    <div className={styles.welcome}>
      <div className={styles.top}>
        <div className={styles.logo}>
          {"{"}
          <span className={styles.l1}>L</span>
          <span className={styles.l2}>L</span>
          <span className={styles.m}>M</span>
          <span className={styles.caret} />
          {"}"}
        </div>
        <div className={styles.tagline}>
          {text(
            "Run functions, build agents, or just ask.",
            "运行函数、搭建 agent，或者直接提问。",
          )}
        </div>
      </div>
    </div>
  );
}
