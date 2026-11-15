"use client";

import { useEffect, useMemo, useState } from "react";
import { usePathname, useRouter } from "next/navigation";
import { SettingsIcon } from "@/components/animated-icons";

import { PROJECTS_RAIL, ProjectsPage, type ProjectTab } from "@/components/projects/projects-page";
import { RailToggle } from "@/components/ui/secondary-rail";
import { SearchInput } from "@/components/ui/search-input";
import { ManagePage, ManagePageHeader, managePageStyles as shared } from "@/components/ui/manage-page";
import { useTranslation } from "@/lib/i18n";

import styles from "./history-page.module.css";

/** Survives remounts when tab routes swap page.tsx. */
let persistedQuery = "";

/**
 * History is organised by project: every project (the home project
 * included) lists its own chats and memory. The old /chats, /projects and
 * /memory routes land here and pick the matching tab.
 */
function tabFromPath(pathname: string): ProjectTab {
  return pathname.startsWith("/memory") ? "memory" : "chats";
}

export function HistoryPage() {
  const pathname = usePathname() || "";
  const router = useRouter();
  const { t, text } = useTranslation();
  const [initialTab, setInitialTab] = useState(() => tabFromPath(pathname));
  const [query, setQueryState] = useState(persistedQuery);

  useEffect(() => {
    const onPop = () => setInitialTab(tabFromPath(window.location.pathname));
    window.addEventListener("popstate", onPop);
    return () => window.removeEventListener("popstate", onPop);
  }, []);

  const setQuery = (value: string) => {
    persistedQuery = value;
    setQueryState(value);
  };

  const actions = useMemo(() => [
    { label: text("Memory settings", "记忆设置"), onClick: () => router.push("/settings/memory"), icon: SettingsIcon },
  ], [text, router]);

  return (
    <ManagePage className={styles.page}>
      <ManagePageHeader
        title={t("nav.history")}
        railToggle={(
          <RailToggle
            railKey={PROJECTS_RAIL}
            controls="projects-rail"
            label={{ collapse: text("Collapse projects", "收起项目列表"), expand: text("Expand projects", "展开项目列表") }}
          />
        )}
        toolbar={(
          <SearchInput
            className="min-w-[96px] flex-1 min-[761px]:flex-none min-[761px]:min-w-[140px] min-[761px]:w-[clamp(150px,24vw,280px)]"
            value={query}
            onChange={setQuery}
            placeholder={text("Search this project...", "搜索这个项目...")}
          />
        )}
        actions={actions}
      />
      <div className={shared.panel}>
        <ProjectsPage embedded query={query} initialTab={initialTab} />
      </div>
    </ManagePage>
  );
}
