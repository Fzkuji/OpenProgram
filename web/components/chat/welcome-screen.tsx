/**
 * WelcomeScreen — empty-chat greeting header (Claude Code style).
 *
 * Renders the bubble-ring app icon (app/icon.svg, served at /icon.svg)
 * next to a plain-sans "What's up next?" heading, top-left aligned to
 * the same 768px content column as the composer. Nothing below it —
 * clean empty space until the input box. Visible whenever the session
 * store says so; mounted as a portal inside the #welcome-mount
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
      <div className={styles.header}>
        {/* eslint-disable-next-line @next/next/no-img-element -- static
            24px svg; next/image adds nothing here */}
        <img
          src="/icon.svg"
          alt=""
          width={24}
          height={24}
          className={styles.icon}
        />
        <h1 className={styles.title}>
          {text("What's up next?", "接下来做什么？")}
        </h1>
      </div>
    </div>
  );
}
