"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { applicationRequest } from "@/lib/net/applications";
import { refreshApplications, useApplicationList } from "@/lib/net/application-list";
import { useApplicationLauncher } from "@/components/applications/use-application-launcher";

import { useTranslation } from "@/lib/i18n";
import { newSession } from "@/lib/runtime-bridge/session/conversations";
import { useCenterTabs } from "@/lib/tabs/center-tabs-store";
import styles from "./pages.module.css";
import { Launchpad, type LaunchItem } from "./launchpad/launchpad";
import { artFor, toneFor } from "@/components/applications/application-icon";
import { BUILTIN_TABS } from "@/components/applications/application-rows";

export function NewTabPage() {
  const { text } = useTranslation();
  const openBuiltinTab = useCenterTabs((state) => state.openBuiltinTab);

  // The last list paints at once; this page only refreshes it.
  const { applications, builtin } = useApplicationList();
  const [error, setError] = useState("");
  const { launch, error: launchError, dialog } = useApplicationLauncher();
  const launchRef = useRef(launch);
  launchRef.current = launch;
  const refresh = useCallback(() => refreshApplications()
    .then(() => setError(""))
    .catch(reason => setError(String(reason.message ?? reason))), []);
  useEffect(() => {
    void refresh();
    window.addEventListener("focus", refresh);
    const timer = setInterval(refresh, 10000);
    return () => { clearInterval(timer); window.removeEventListener("focus", refresh); };
  }, [refresh]);

  const items = useMemo<LaunchItem[]>(() => [
    { id: "builtin:files", title: text("Files", "文件"), art: "files", open: () => openBuiltinTab("files") },
    {
      id: "builtin:chat", title: text("New chat", "新建对话"), art: "chat",
      open: () => {
        const draftId = useCenterTabs.getState().claimDraftSessionTab();
        newSession(draftId);
      },
    },
    { id: "builtin:browser", title: text("Browser", "浏览器"), art: "browser", open: () => openBuiltinTab("browser") },
    { id: "builtin:terminal", title: text("Terminal", "终端"), art: "terminal", open: () => openBuiltinTab("terminal") },
    // Built-in applications with a window of their own (the browser is above).
    ...builtin.filter(application => application.enabled && application.id !== "browser" && BUILTIN_TABS[application.id])
      .map((application): LaunchItem => ({
        id: `builtin:${application.id}`,
        title: application.title || application.id,
        art: artFor(application.icon),
        tone: toneFor(application.icon, application.id),
        open: () => openBuiltinTab(BUILTIN_TABS[application.id]!),
      })),
    ...applications.filter(application => application.enabled && !application.hidden).map((application): LaunchItem => ({
      id: `app:${application.id}`,
      title: application.display_title || application.title,
      art: artFor(application.icon),
      tone: toneFor(application.icon, application.id),
      open: () => void launchRef.current(application),
      // Taking an application off the Launchpad is the "Show in new tab"
      // switch on the Applications page, so it can be brought back there.
      remove: () => void applicationRequest(`/api/applications/${encodeURIComponent(application.id)}`, "PATCH", { hidden: true })
        .then(() => refreshApplications({ fresh: true }))
        .catch(reason => setError(String(reason.message ?? reason))),
    })),
  ], [applications, builtin, openBuiltinTab, text]);

  return (
    <div className={styles.ntp}>
      <Launchpad items={items} />
      {dialog}
      {(error || launchError) && <p role="alert" className={styles.ntpError}>{error || launchError}</p>}
    </div>
  );
}
