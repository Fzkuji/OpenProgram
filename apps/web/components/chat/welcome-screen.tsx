/**
 * WelcomeScreen — empty-chat centered greeting.
 *
 * Uses the same static three-node mark as the app icon, followed by one
 * short capability sentence. Visible whenever the session store says so;
 * mounted as a portal inside the #welcome-mount placeholder that PageShell
 * leaves in the chat area.
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
        <img
          className={styles.mark}
          src="/icon.svg"
          width={34}
          height={34}
          alt=""
          aria-hidden="true"
          draggable={false}
        />
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
