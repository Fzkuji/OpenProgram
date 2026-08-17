"use client";

import { Fragment, useCallback, useEffect, useMemo, useState } from "react";
import { useRouter } from "next/navigation";

import { BotIcon, BoxesIcon, HeartIcon, WrenchIcon } from "@/components/animated-icons";
import { Button } from "@/components/ui/button";
import { SearchInput } from "@/components/ui/search-input";
import { useTranslation } from "@/lib/i18n";
import { jsonFetch } from "@/lib/net/fetch-client";
import { getLastChatPath } from "@/lib/last-chat-path";
import { runtimeState } from "@/lib/runtime-bridge/state";
import { useFunctions } from "@/lib/state/functions-store";
import { setPendingRunFunction } from "@/lib/use-pending-run-function";

import { CustomSelect } from "./custom-select";
import { CtxMenu, type CtxMenuState } from "./ctx-menu";
import { FunctionCard, ToolCard, cardGridClass, cardListClass } from "./function-card";
import { IconPicker, normalizeIcon } from "./icon-picker";
import {
  matchesProgramSearch,
  programsForSelection,
  toolsForSelection,
} from "./program-source-categories";
import { groupTools, TOOL_GROUPS, type ToolInfo } from "./tool-groups";
import { ProfileNavRow } from "./functions-page-parts";
import styles from "./functions-page.module.css";
import type { FunctionInfo, FunctionsMeta } from "./types";

export function FunctionsPage() {
  const { t, text, locale } = useTranslation();
  const router = useRouter();
  const [functions, setFunctions] = useState<FunctionInfo[]>([]);
  const [tools, setTools] = useState<ToolInfo[]>([]);
  const [meta, setMeta] = useState<FunctionsMeta>({ favorites: [], profiles: {}, icons: {} });
  const [selection, setSelection] = useState("__functions__");
  const [view, setView] = useState<"grid" | "list">("grid");
  const [sort, setSort] = useState<"name" | "recent">("name");
  const [search, setSearch] = useState("");
  const [refreshing, setRefreshing] = useState(false);
  const [iconPickerFor, setIconPickerFor] = useState<string | null>(null);
  const [contextMenu, setContextMenu] = useState<CtxMenuState | null>(null);

  const reload = useCallback(async (signal?: AbortSignal) => {
    try {
      const [programRows, programMeta, toolRows] = await Promise.all([
        jsonFetch<unknown>("/api/programs", { signal }),
        fetch("/api/programs/meta", { signal }).then((response) => response.json()),
        fetch("/api/tools", { signal }).then((response) => response.json()),
      ]);
      if (signal?.aborted) return;
      if (!Array.isArray(programRows)) throw new TypeError("/api/programs must return an array");
      setFunctions(programRows as FunctionInfo[]);
      setTools(Array.isArray(toolRows) ? toolRows : []);
      setMeta({
        favorites: programMeta?.favorites ?? [],
        profiles: {},
        icons: programMeta?.icons ?? {},
      });
    } catch (error) {
      if ((error as Error).name === "AbortError") return;
      setFunctions([]);
      setTools([]);
    }
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    void reload(controller.signal);
    return () => controller.abort();
  }, [reload]);

  const saveMeta = useCallback(async (next: FunctionsMeta) => {
    setMeta(next);
    await fetch("/api/programs/meta", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ favorites: next.favorites, icons: next.icons }),
    }).catch(() => undefined);
    runtimeState.programsMeta = {
      favorites: [...next.favorites],
      profiles: {},
      icons: { ...next.icons },
    };
    const store = useFunctions.getState();
    store.setMeta({ ...store.meta, favorites: [...next.favorites], icons: { ...next.icons } });
  }, []);

  const catalogTools = tools.filter((tool) => tool.source !== "mcp");
  const sourceCategories = [
    {
      id: "__functions__",
      name: text("Functions", "函数"),
      icon: <WrenchIcon size={16} />,
      count: catalogTools.length,
    },
    {
      id: "__agentic_functions__",
      name: text("Agentic Functions", "Agentic 函数"),
      icon: <BotIcon size={16} />,
      count: functions.filter((program) => program.category !== "app").length,
    },
    {
      id: "__applications__",
      name: text("Applications", "应用"),
      icon: <BoxesIcon size={16} />,
      count: functions.filter((program) => program.category === "app").length,
    },
    {
      id: "__favorites__",
      name: text("Favorites", "收藏"),
      icon: <HeartIcon size={16} />,
      count: meta.favorites.filter((name) =>
        functions.some((program) => program.name === name) || catalogTools.some((tool) => tool.name === name),
      ).length,
    },
  ];

  const visibleFunctions = useMemo(() => {
    let rows = programsForSelection(selection, functions, meta.favorites)
      .filter((program) => matchesProgramSearch(program, search));
    rows = [...rows].sort(sort === "recent"
      ? (a, b) => (b.mtime || 0) - (a.mtime || 0)
      : (a, b) => a.name.localeCompare(b.name));
    return rows;
  }, [functions, meta.favorites, search, selection, sort]);

  const visibleTools = useMemo(
    () => toolsForSelection(selection, catalogTools, meta.favorites)
      .filter((tool) => matchesProgramSearch(tool, search))
      .sort((a, b) => a.name.localeCompare(b.name)),
    [catalogTools, meta.favorites, search, selection],
  );
  const visibleToolGroups = groupTools(visibleTools);
  const visibleAgenticFunctions = visibleFunctions.filter((program) => program.category !== "app");
  const visibleApplications = visibleFunctions.filter((program) => program.category === "app");
  const showSourceCatalog = selection !== "__favorites__";
  const toolGroupLabels = new Map<string, string>(TOOL_GROUPS.map(([id, en, zh]) => [id, text(en, zh)]));

  function formatDate(timestamp?: number) {
    if (!timestamp) return "";
    return new Date(timestamp * 1000).toLocaleDateString(locale === "zh" ? "zh-CN" : undefined);
  }

  function toggleFavorite(name: string, event: React.MouseEvent) {
    event.stopPropagation();
    const favorites = meta.favorites.includes(name)
      ? meta.favorites.filter((item) => item !== name)
      : [...meta.favorites, name];
    void saveMeta({ ...meta, favorites });
  }

  function toggleTool(name: string, enabled: boolean) {
    setTools((rows) => rows.map((tool) => tool.name === name ? { ...tool, disabled: !enabled } : tool));
    void fetch("/api/settings", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ key: `tools.disabled.${name}`, value: enabled }),
    });
  }

  function openProgramMenu(event: React.MouseEvent, name: string) {
    event.preventDefault();
    setContextMenu({
      x: event.clientX,
      y: event.clientY,
      items: [
        {
          label: meta.favorites.includes(name)
            ? text("Unfavorite", "取消收藏")
            : text("Favorite", "收藏"),
          action: () => toggleFavorite(name, event),
        },
        { label: text("Change icon...", "更换图标..."), action: () => setIconPickerFor(name) },
        {
          label: text("Edit...", "编辑..."),
          action: () => {
            setPendingRunFunction({ name: "edit", cat: "", fn: name });
            router.push(getLastChatPath() || "/chat");
          },
        },
      ],
    });
  }

  async function refreshPrograms() {
    setRefreshing(true);
    await fetch("/api/programs/refresh", { method: "POST" }).catch(() => undefined);
    await reload();
    setRefreshing(false);
  }

  function selectSource(id: string) {
    setSelection(id);
    if (id === "__favorites__") return;
    const sectionId = id === "__functions__"
      ? "program-source-functions"
      : id === "__agentic_functions__"
        ? "program-source-agentic"
        : "program-source-applications";
    requestAnimationFrame(() => {
      document.getElementById(sectionId)?.scrollIntoView({ block: "start", behavior: "smooth" });
    });
  }

  function renderProgramCards(rows: FunctionInfo[]) {
    if (rows.length === 0) return null;
    return (
      <div className={view === "grid" ? cardGridClass : cardListClass}>
        {rows.map((program) => (
          <FunctionCard
            key={program.name}
            p={program}
            icon={normalizeIcon(meta.icons[program.name])}
            fav={meta.favorites.includes(program.name)}
            profileName={null}
            formatDate={formatDate}
            onClick={() => router.push(`/chat?${new URLSearchParams({ run: program.name, cat: program.category || "" })}`)}
            onContextMenu={(event) => openProgramMenu(event, program.name)}
            onToggleFav={(event) => toggleFavorite(program.name, event)}
            onChangeIcon={(event) => { event.stopPropagation(); setIconPickerFor(program.name); }}
          />
        ))}
      </div>
    );
  }

  function renderToolGroups() {
    if (visibleTools.length === 0) return null;
    return visibleToolGroups.map((group) => (
      <section key={group.name} className={styles.toolGroup}>
        <div className={styles.toolsHeader}>{toolGroupLabels.get(group.name) || group.name}<span>{group.items.length}</span></div>
        <div className={view === "grid" ? cardGridClass : cardListClass}>
          {group.items.map((tool) => (
            <ToolCard
              key={tool.name}
              name={tool.name}
              description={tool.description || ""}
              enabled={!tool.disabled}
              onToggle={(enabled) => toggleTool(tool.name, enabled)}
            />
          ))}
        </div>
      </section>
    ));
  }

  return (
    <div className="main">
      <div className={styles.view}>
        <div className={styles.topbar}>
          <span className={styles.title}>{t("nav.programs")}</span>
          <div className={styles.toolbar}>
            <SearchInput
              className="flex-1 max-w-[320px]"
              placeholder={text("Search programs...", "搜索程序...")}
              value={search}
              onChange={setSearch}
            />
            <CustomSelect
              value={sort}
              onChange={setSort}
              options={[
                { value: "name", label: text("Sort: Name", "排序：名称") },
                { value: "recent", label: text("Sort: Recent", "排序：最近") },
              ]}
            />
            <Button variant="outline" size="sm" onClick={() => setView(view === "grid" ? "list" : "grid")}>
              {view === "grid" ? text("List", "列表") : text("Grid", "网格")}
            </Button>
            <Button variant="outline" size="sm" onClick={refreshPrograms} disabled={refreshing}>
              {refreshing ? text("Refreshing…", "刷新中…") : text("Refresh", "刷新")}
            </Button>
          </div>
        </div>
        <div className={styles.body}>
          <div className={styles.profilesNav}>
            {sourceCategories.map((category, index) => (
              <Fragment key={category.id}>
                {index === 3 ? <div className={styles.profileSep} /> : null}
                <ProfileNavRow
                  icon={category.icon}
                  name={category.name}
                  count={category.count}
                  active={selection === category.id}
                  onClick={() => selectSource(category.id)}
                />
              </Fragment>
            ))}
          </div>
          <div className={styles.content}>
            {showSourceCatalog ? (
              <>
                {visibleFunctions.length === 0 && visibleTools.length === 0 ? (
                  <div className={styles.empty}>{text("No matching programs", "没有匹配的程序")}</div>
                ) : null}
                {visibleTools.length > 0 || !search ? (
                  <section id="program-source-functions" className={styles.sourceSection}>
                    <div className={styles.sourceHeader}><h2>{text("Functions", "函数")}</h2><span>{visibleTools.length}</span></div>
                    {visibleTools.length > 0 ? renderToolGroups() : <div className={styles.sourceEmpty}>{text("No matching functions", "没有匹配的函数")}</div>}
                  </section>
                ) : null}
                {visibleAgenticFunctions.length > 0 || !search ? (
                  <section id="program-source-agentic" className={styles.sourceSection}>
                    <div className={styles.sourceHeader}><h2>{text("Agentic Functions", "Agentic 函数")}</h2><span>{visibleAgenticFunctions.length}</span></div>
                    {renderProgramCards(visibleAgenticFunctions) || <div className={styles.sourceEmpty}>{text("No agentic functions", "暂无 Agentic 函数")}</div>}
                  </section>
                ) : null}
                {visibleApplications.length > 0 || !search ? (
                  <section id="program-source-applications" className={styles.sourceSection}>
                    <div className={styles.sourceHeader}><h2>{text("Applications", "应用")}</h2><span>{visibleApplications.length}</span></div>
                    {renderProgramCards(visibleApplications) || <div className={styles.sourceEmpty}>{text("No applications installed", "尚未安装应用")}</div>}
                  </section>
                ) : null}
              </>
            ) : (
              <>
                {visibleFunctions.length === 0 && visibleTools.length === 0 ? (
                  <div className={styles.empty}>{search ? text("No matching favorites", "没有匹配的收藏") : text("No favorites yet", "暂无收藏")}</div>
                ) : null}
                {renderProgramCards(visibleFunctions)}
                {visibleTools.length > 0 ? (
                  <div className={visibleFunctions.length ? styles.toolsSection : undefined}>{renderToolGroups()}</div>
                ) : null}
              </>
            )}
          </div>
        </div>
      </div>
      {iconPickerFor ? (
        <IconPicker
          name={iconPickerFor}
          current={normalizeIcon(meta.icons[iconPickerFor])}
          onPick={(icon) => {
            const icons = { ...meta.icons };
            if (icon) icons[iconPickerFor] = icon;
            else delete icons[iconPickerFor];
            void saveMeta({ ...meta, icons });
            setIconPickerFor(null);
          }}
          onClose={() => setIconPickerFor(null)}
        />
      ) : null}
      {contextMenu ? <CtxMenu state={contextMenu} onClose={() => setContextMenu(null)} /> : null}
    </div>
  );
}
