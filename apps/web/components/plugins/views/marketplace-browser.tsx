"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import styles from "../plugins.module.css";
import { usePluginsStore } from "@/lib/abilities/plugins-store";
import { useTranslation } from "@/lib/i18n";
import { SearchInput } from "@/components/ui/search-input";
import { Button } from "@/components/ui/button";
import { SimpleSelect } from "@/components/ui/select";
import { AddMarketplaceDialog } from "../dialogs/add-marketplace-dialog";
import { ManageCatalogCard, managePageStyles as shared } from "@/components/ui/manage-page";
import { PlusIcon } from "@/components/animated-icons";
import { confirmAction } from "@/components/ui/confirm-dialog";
import { showToast } from "@/lib/format-utils/toast";

interface IndexItem {
  name?: string;
  displayName?: string;
  description?: string;
  source?: string;
  spec?: string;
  url?: string;
  version?: string;
  tags?: string[];
  official?: boolean;
}

type SortKey = "default" | "name" | "official";

export function MarketplaceBrowser({ externalFilter }: { externalFilter?: string } = {}) {
  const { text } = useTranslation();
  const {
    plugins,
    marketplaces,
    refreshMarketplaces,
    removeMarketplace,
    fetchMarketplaceIndex,
    fetchBuiltinPlugins,
    install,
  } = usePluginsStore();

  // Built-in curated catalog — fetched once on mount, always shown.
  const [builtin, setBuiltin] = useState<IndexItem[]>([]);
  // Per-marketplace selected + fetched item list.
  const [selectedId, setSelectedId] = useState<string>("");
  const [items, setItems] = useState<IndexItem[]>([]);
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState("");
  const [addOpen, setAddOpen] = useState(false);
  const [busySpec, setBusySpec] = useState("");
  const [filter, setFilter] = useState("");
  const [sort, setSort] = useState<SortKey>("default");
  const filterValue = externalFilter !== undefined ? externalFilter : filter;

  useEffect(() => {
    refreshMarketplaces();
    fetchBuiltinPlugins().then(setBuiltin).catch(() => { /* leave empty */ });
  }, [refreshMarketplaces, fetchBuiltinPlugins]);

  useEffect(() => {
    if (!selectedId) {
      setItems([]);
      return;
    }
    setLoading(true);
    setErr("");
    fetchMarketplaceIndex(selectedId)
      .then((r) => setItems(r as IndexItem[]))
      .catch((e) => setErr(String(e)))
      .finally(() => setLoading(false));
  }, [selectedId, fetchMarketplaceIndex]);

  const installedNames = useMemo(
    () => new Set(plugins.map((p) => p.name)),
    [plugins],
  );

  const doInstall = async (item: IndexItem) => {
    const source = item.source || (item.url ? "git" : "pip");
    const spec = item.spec || item.url || item.name || "";
    if (!spec) {
      showToast(text("Missing source/spec/url. Cannot install.", "缺少 source/spec/url，无法安装。"), { tone: "error" });
      return;
    }
    setBusySpec(spec);
    try {
      const r = await install(source, spec);
      if (!r.success) {
        setErr(text(`Install failed: ${r.log.slice(0, 500)}`, `安装失败：${r.log.slice(0, 500)}`));
      }
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setBusySpec("");
    }
  };

  const filterAndSort = useCallback((arr: IndexItem[]): IndexItem[] => {
    let out = arr;
    if (filterValue.trim()) {
      const q = filterValue.toLowerCase();
      out = out.filter(
        (i) =>
          (i.name || "").toLowerCase().includes(q) ||
          (i.displayName || "").toLowerCase().includes(q) ||
          (i.description || "").toLowerCase().includes(q) ||
          (i.tags || []).some((t) => t.toLowerCase().includes(q)),
      );
    }
    if (sort === "name") {
      out = [...out].sort((a, b) => (a.displayName || a.name || "").localeCompare(b.displayName || b.name || ""));
    } else if (sort === "official") {
      out = [...out].sort((a, b) => (b.official ? 1 : 0) - (a.official ? 1 : 0));
    }
    return out;
  }, [filterValue, sort]);

  const shownBuiltin = useMemo(() => filterAndSort(builtin), [builtin, filterAndSort]);
  const shownItems = useMemo(() => filterAndSort(items), [items, filterAndSort]);

  return (
    <div className="space-y-4">
      {/* Search + sort */}
      <div className="flex items-center gap-2">
        {externalFilter === undefined && (
        <SearchInput
          className="flex-1"
          value={filter}
          onChange={setFilter}
          placeholder={text("Search plugins...", "搜索插件...")}
        />
        )}
        <SimpleSelect
          className="shrink-0"
          aria-label={text("Sort", "排序")}
          value={sort}
          onValueChange={setSort}
          options={[
            { value: "default" as SortKey, label: text("Sort: Default", "排序：默认") },
            { value: "name" as SortKey, label: text("Name", "名称") },
            { value: "official" as SortKey, label: text("Official first", "官方优先") },
          ]}
        />
      </div>

      {/* Built-in catalog */}
      <section>
        <h3 className="text-fs-xs font-semibold text-text-bright mb-1">
          {text("Curated plugins", "精选插件")}
          <span className="ml-2 text-fs-2xs font-normal text-text-secondary">
            {text(`built-in catalog · ${builtin.length} total`, `内置目录 · 共 ${builtin.length} 个`)}
          </span>
        </h3>
        <p className="text-fs-2xs text-text-secondary mb-3">
          {text(
            "One-click installs maintained by OpenProgram. Source and package manager are bundled with each entry. No marketplace registration needed.",
            "由 OpenProgram 维护的一键安装项。每个条目都包含来源和包管理器信息，不需要注册 marketplace。",
          )}
        </p>
        <div className={shared.catalogGrid}>
          {shownBuiltin.map((it) => (
            <PluginCard
              key={it.name || it.spec || it.url}
              item={it}
              installed={installedNames.has(it.name || "")}
              installing={busySpec === (it.spec || it.url || it.name)}
              onInstall={() => doInstall(it)}
            />
          ))}
          {shownBuiltin.length === 0 && (
            <div className="col-span-full text-fs-2xs text-text-secondary py-2">
              {filterValue.trim() ? text("No matches.", "没有匹配结果。") : text("No built-in plugins available.", "没有可用的内置插件。")}
            </div>
          )}
        </div>
      </section>

      {/* Custom marketplaces */}
      <section>
        <h3 className="text-fs-xs font-semibold text-text-bright mb-1">{text("External marketplaces", "外部 Marketplace")}</h3>
        <p className="text-fs-2xs text-text-secondary mb-3">
          {text("Add any JSON index URL (claude-code marketplace schema compatible).", "添加任意 JSON 索引 URL（兼容 claude-code marketplace schema）。")}
        </p>
        <div className="flex items-center gap-2 mb-3">
          <SimpleSelect
            aria-label={text("Marketplace", "Marketplace")}
            placeholder={text("Select a marketplace", "选择 Marketplace")}
            value={selectedId}
            onValueChange={setSelectedId}
            options={marketplaces.map((m) => ({ value: m.id, label: m.name }))}
          />
          <Button variant="elevated" size="xs" onClick={() => setAddOpen(true)}><PlusIcon size={16} aria-hidden />{text("Add", "添加")}</Button>
          {selectedId && (
            <Button
              variant="destructive"
              onClick={async () => {
                if (!(await confirmAction({
                  title: text("Remove this marketplace?", "移除这个 Marketplace？"),
                  confirmLabel: text("Remove", "移除"),
                  destructive: true,
                }))) return;
                await removeMarketplace(selectedId);
                setSelectedId("");
                setItems([]);
              }}
            >{text("Remove", "移除")}</Button>
          )}
        </div>

        {loading && <div className="text-fs-2xs text-text-secondary">{text("Loading...", "加载中...")}</div>}
        {err && <div className={styles.errorBox}>{err}</div>}
        {!loading && !err && selectedId && shownItems.length === 0 && (
          <div className="text-fs-2xs text-text-secondary">{text("No items in this marketplace.", "这个 Marketplace 中没有条目。")}</div>
        )}
        {!selectedId && !loading && (
          <div className="text-fs-2xs text-text-secondary">{text("Pick a marketplace to browse its plugins.", "选择一个 Marketplace 浏览插件。")}</div>
        )}
        {shownItems.length > 0 && (
          <div className={shared.catalogGrid}>
            {shownItems.map((it, i) => (
              <PluginCard
                key={it.name || i}
                item={it}
                installed={installedNames.has(it.name || "")}
                installing={busySpec === (it.spec || it.url || it.name)}
                onInstall={() => doInstall(it)}
              />
            ))}
          </div>
        )}
      </section>

      {addOpen && <AddMarketplaceDialog onClose={() => setAddOpen(false)} />}
    </div>
  );
}

function PluginCard({
  item, installed, installing, onInstall,
}: {
  item: IndexItem;
  installed: boolean;
  installing: boolean;
  onInstall: () => void;
}) {
  const { text } = useTranslation();
  const title = item.displayName || item.name || "(unnamed)";
  const slug = item.name || item.spec || "";
  return (
    <ManageCatalogCard
      title={title}
      subtitle={slug && slug !== title ? slug : item.version ? `v${item.version}` : undefined}
      description={item.description}
      status={<>
        {item.official && <span className={shared.badge}>{text("official", "官方")}</span>}
        {installed && <span className={`${shared.badge} ${shared.badgeGreen}`}>{text("installed", "已安装")}</span>}
      </>}
      meta={<>
        {item.source && <span>{item.source.toUpperCase()}</span>}
        {(item.tags || []).slice(0, 2).map((tag) => <span key={tag}>{tag}</span>)}
      </>}
      actions={
        <Button
          variant={installed ? "elevated" : "default"}
          size="xs"
          onClick={onInstall}
          disabled={installing}
        >
          {installing ? text("Installing...", "安装中...") : installed ? text("Reinstall", "重新安装") : text("Install", "安装")}
        </Button>
      }
    />
  );
}
