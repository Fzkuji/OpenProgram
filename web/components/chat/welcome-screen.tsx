/**
 * WelcomeScreen — empty-chat onboarding panel.
 *
 * Renders the `{LLM}` logo, the "Agentic Programming" title, and the
 * help text. Visible whenever the session store says so; mounted as a
 * portal inside #welcome-mount placeholder that PageShell leaves in
 * the chat area.
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
        <div className={`${styles.title} display-serif`}>Agentic Programming</div>
        <div className={styles.text}>
          {text(
            "Run agentic functions, create new ones, or ask questions. Type a command or natural language below.",
            "运行 Agentic 函数、创建新函数，或直接提问。可以在下方输入命令或自然语言。",
          )}
        </div>
      </div>
    </div>
  );
}
