"use client";

import { usePathname } from "next/navigation";
import { useTranslation } from "@/lib/i18n";
import styles from "./shell-loading.module.css";

/**
 * Suspense fallback for /functions, /skills, /memory, /mcp, /plugins,
 * /chats, and any other (shell) subroute that doesn't ship its own
 * loading.tsx. Without this file, clicking a sidebar item left the
 * old page on screen for the full route-compile (~200-1500ms in dev
 * mode), which the user reported as "click does nothing, then snap".
 *
 * The /settings/* tree has a more specific
 * ``app/(shell)/settings/loading.tsx`` that wins over this generic
 * one (Next.js picks the deepest segment's loading.tsx). /chat and
 * /s/<id> don't suspense because their UI is mounted inside AppShell
 * itself — their page.tsx is just ``return null;``.
 */
const ROUTE_KEYS: Record<string, "nav.functions"|"nav.skills"|"nav.memory"|"nav.mcp"|"nav.plugins"|"nav.chats"> = {
  functions: "nav.functions",
  skills: "nav.skills",
  memory: "nav.memory",
  mcp: "nav.mcp",
  plugins: "nav.plugins",
  chats: "nav.chats",
};

export default function ShellLoading() {
  const { t, text } = useTranslation();
  const pathname = usePathname() || "";
  const seg = pathname.split("/")[1] || "";
  const key = ROUTE_KEYS[seg];
  const title = key ? t(key) : text("Loading...", "加载中...");

  return (
    <div className={styles.wrap}>
      <div className={styles.header}>
        <h2 className={styles.title}>{title}</h2>
      </div>
      <div className={styles.body}>
        <div className={styles.skel} />
        <div className={styles.skel} />
        <div className={styles.skelShort} />
      </div>
    </div>
  );
}
