"use client";

import { useEffect, useMemo, useState } from "react";
import { usePathname } from "next/navigation";
import {
  BlocksIcon,
  GraduationCapIcon,
  PlugZapIcon,
  RefreshCwIcon,
  WorkflowIcon,
} from "@/components/animated-icons";

import { McpPage } from "@/components/mcp/mcp-page";
import { PluginsPage } from "@/components/plugins/plugins-page";
import { ProgramsPage } from "@/components/programs/programs-page";
import { SkillsPage } from "@/components/skills/skills-page";
import { SearchInput } from "@/components/ui/search-input";
import { ManagePageHeader, managePageStyles as shared } from "@/components/ui/manage-page";
import { useTranslation } from "@/lib/i18n";
import { pushPath } from "@/lib/shallow-nav";
import { usePluginsStore } from "@/lib/state/plugins-store";
import { useSkills } from "@/lib/state/skills-store";

export type CapabilityKind = "programs" | "plugins" | "skills" | "mcp";

/** Survives remounts when tab routes swap page.tsx. */
let persistedQuery = "";

function kindFromPath(pathname: string): CapabilityKind {
  if (pathname.startsWith("/programs")) return "programs";
  if (pathname.startsWith("/skills")) return "skills";
  if (pathname.startsWith("/mcp")) return "mcp";
  return "plugins";
}

function kindHref(id: string): string {
  if (id === "mcp") return "/mcp";
  return `/${id}`;
}

export function CapabilitiesPage() {
  const pathname = usePathname() || "";
  const { t, text } = useTranslation();
  const [kind, setKind] = useState(() => kindFromPath(pathname));
  const [query, setQueryState] = useState(persistedQuery);
  const [mcpReloadNonce, setMcpReloadNonce] = useState(0);
  const [programReloadNonce, setProgramReloadNonce] = useState(0);
  if (typeof window !== "undefined") {
    try { sessionStorage.setItem("op.ability.kind", kind); } catch { /* ignore */ }
  }

  useEffect(() => {
    const onPop = () => setKind(kindFromPath(window.location.pathname));
    window.addEventListener("popstate", onPop);
    return () => window.removeEventListener("popstate", onPop);
  }, []);

  const refreshPlugins = usePluginsStore((s) => s.refresh);
  const fetchSkills = useSkills((s) => s.fetchSkills);

  const setQuery = (value: string) => {
    persistedQuery = value;
    setQueryState(value);
  };

  const goKind = (id: string) => {
    setMcpReloadNonce(0);
    setProgramReloadNonce(0);
    const href = kindHref(id);
    setKind(kindFromPath(href));
    pushPath(href);
  };

  const actions = useMemo(() => {
    const refresh = (onClick: () => void) => ({
      label: t("sidebar.refresh"),
      onClick,
      icon: RefreshCwIcon,
      iconOnly: true,
    });
    if (kind === "programs") {
      return [refresh(() => setProgramReloadNonce((n) => n + 1))];
    }
    if (kind === "plugins") {
      return [refresh(() => { void refreshPlugins(); })];
    }
    if (kind === "skills") {
      return [refresh(() => { void fetchSkills(); })];
    }
    return [refresh(() => setMcpReloadNonce((n) => n + 1))];
  }, [kind, t, text, refreshPlugins, fetchSkills]);

  return (
    <div className="main" style={{ minWidth: 0, overflow: "hidden" }}>
      <div className={shared.view}>
        <ManagePageHeader
          title={t("nav.ability")}
          tabs={[
            { id: "programs", label: t("nav.programs"), icon: WorkflowIcon },
            { id: "plugins", label: t("nav.plugins"), icon: BlocksIcon },
            { id: "skills", label: t("nav.skills"), icon: GraduationCapIcon },
            { id: "mcp", label: t("nav.mcp_short"), icon: PlugZapIcon },
          ]}
          activeTab={kind}
          onTabChange={goKind}
          tabLabel={text("Ability types", "能力类型")}
          toolbar={(
            <SearchInput
              className="min-w-[96px] flex-1 sm:flex-none sm:min-w-[140px] sm:w-[clamp(150px,24vw,280px)]"
              value={query}
              onChange={setQuery}
              placeholder={text("Search Programs, Plugins, Skills, MCP servers...", "搜索程序、插件、技能、MCP 服务器...")}
            />
          )}
          actions={actions}
        />
        {kind === "programs" && (
          <ProgramsPage
            embedded
            query={query}
            reloadNonce={programReloadNonce}
          />
        )}
        {kind === "plugins" && (
          <PluginsPage
            embedded
            query={query}
          />
        )}
        {kind === "skills" && (
          <SkillsPage
            embedded
            query={query}
          />
        )}
        {kind === "mcp" && (
          <McpPage
            embedded
            query={query}
            reloadNonce={mcpReloadNonce}
          />
        )}
      </div>
    </div>
  );
}
