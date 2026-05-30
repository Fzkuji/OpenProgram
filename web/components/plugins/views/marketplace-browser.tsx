"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import styles from "../plugins.module.css";
import { usePluginsStore } from "@/lib/plugins-store";
import { useTranslation } from "@/lib/i18n";
import { AddMarketplaceDialog } from "../dialogs/add-marketplace-dialog";

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

export function MarketplaceBrowser() {
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
      alert(text("Missing source/spec/url. Cannot install.", "缺少 source/spec/url，无法安装。"));
      return;
    }
    setBusySpec(spec);
    try {
      const r = await install(source, spec);
      if (!r.success) {
        alert(text(`Install failed:\n${r.log.slice(0, 500)}`, `安装失败：\n${r.log.slice(0, 500)}`));
      }
    } finally {
      setBusySpec("");
    }
  };

  const filterAndSort = useCallback((arr: IndexItem[]): IndexItem[] => {
    let out = arr;
    if (filter.trim()) {
      const q = filter.toLowerCase();
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
  }, [filter, sort]);

  const shownBuiltin = useMemo(() => filterAndSort(builtin), [builtin, filterAndSort]);
  const shownItems = useMemo(() => filterAndSort(items), [items, filterAndSort]);

  return (
    <div className="space-y-5">
      {/* Search + sort */}
      <div className="flex items-center gap-2">
        <div className="relative flex-1">
          <svg
            className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-[var(--text-tertiary)]"
            width="14" height="14" viewBox="0 0 20 20" fill="currentColor"
          >
            <path fillRule="evenodd" clipRule="evenodd"
              d="M9 3.5a5.5 5.5 0 1 0 0 11 5.5 5.5 0 0 0 0-11ZM2 9a7 7 0 1 1 12.452 4.391l3.328 3.329a.75.75 0 1 1-1.06 1.06l-3.329-3.328A7 7 0 0 1 2 9Z" />
          </svg>
          <input
            value={filter}
            onChange={(e) => setFilter(e.target.value)}
            placeholder={text("Search plugins...", "搜索插件...")}
            className="w-full rounded-[var(--ui-button-radius)] border border-[var(--border)] bg-[var(--bg-input)] py-2 pl-9 pr-3 text-sm text-[var(--text-primary)] placeholder:text-[var(--text-tertiary)] outline-none transition-colors focus:border-[color:var(--accent-blue)]"
          />
        </div>
        <select
          value={sort}
          onChange={(e) => setSort(e.target.value as SortKey)}
          className="shrink-0 rounded-[var(--ui-button-radius)] border border-[var(--border)] bg-[var(--bg-input)] px-3 py-2 text-sm text-[var(--text-primary)] outline-none transition-colors focus:border-[color:var(--accent-blue)]"
        >
          <option value="default">{text("Sort: default", "排序：默认")}</option>
          <option value="name">{text("Name", "名称")}</option>
          <option value="official">{text("Official first", "官方优先")}</option>
        </select>
      </div>

      {/* Built-in catalog */}
      <section>
        <h3 className="text-sm font-semibold text-[var(--text-bright)] mb-1">
          {text("Curated plugins", "精选插件")}
          <span className="ml-2 text-[11px] font-normal text-[var(--text-tertiary)]">
            {text(`built-in catalog · ${builtin.length} total`, `内置目录 · 共 ${builtin.length} 个`)}
          </span>
        </h3>
        <p className="text-xs text-[var(--text-tertiary)] mb-3">
          {text(
            "One-click installs maintained by OpenProgram. Source and package manager are bundled with each entry. No marketplace registration needed.",
            "由 OpenProgram 维护的一键安装项。每个条目都包含来源和包管理器信息，不需要注册 marketplace。",
          )}
        </p>
        <div className="grid grid-cols-1 gap-2 sm:grid-cols-2 lg:grid-cols-3">
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
            <div className="col-span-full text-xs text-[var(--text-tertiary)] py-2">
              {filter.trim() ? text("No matches.", "没有匹配结果。") : text("No built-in plugins available.", "没有可用的内置插件。")}
            </div>
          )}
        </div>
      </section>

      {/* Custom marketplaces */}
      <section>
        <h3 className="text-sm font-semibold text-[var(--text-bright)] mb-1">{text("External marketplaces", "外部 Marketplace")}</h3>
        <p className="text-xs text-[var(--text-tertiary)] mb-3">
          {text("Add any JSON index URL (claude-code marketplace schema compatible).", "添加任意 JSON 索引 URL（兼容 claude-code marketplace schema）。")}
        </p>
        <div className="flex items-center gap-2 mb-3">
          <select
            value={selectedId}
            onChange={(e) => setSelectedId(e.target.value)}
            className="rounded-[var(--ui-button-radius)] border border-[var(--border)] bg-[var(--bg-input)] px-3 py-1.5 text-sm text-[var(--text-primary)] outline-none transition-colors focus:border-[color:var(--accent-blue)]"
          >
            <option value="">{text("- Select a marketplace -", "- 选择 Marketplace -")}</option>
            {marketplaces.map((m) => (
              <option key={m.id} value={m.id}>{m.name}</option>
            ))}
          </select>
          <button className={styles.btn} onClick={() => setAddOpen(true)}>+ {text("Add", "添加")}</button>
          {selectedId && (
            <button
              className={styles.btnDanger}
              onClick={async () => {
                if (!confirm(text("Remove this marketplace?", "移除这个 Marketplace？"))) return;
                await removeMarketplace(selectedId);
                setSelectedId("");
                setItems([]);
              }}
            >{text("Remove", "移除")}</button>
          )}
        </div>

        {loading && <div className="text-xs text-[var(--text-tertiary)]">{text("Loading...", "加载中...")}</div>}
        {err && <div className={styles.errorBox}>{err}</div>}
        {!loading && !err && selectedId && shownItems.length === 0 && (
          <div className="text-xs text-[var(--text-tertiary)]">{text("No items in this marketplace.", "这个 Marketplace 中没有条目。")}</div>
        )}
        {!selectedId && !loading && (
          <div className="text-xs text-[var(--text-tertiary)]">{text("Pick a marketplace to browse its plugins.", "选择一个 Marketplace 浏览插件。")}</div>
        )}
        {shownItems.length > 0 && (
          <div className="grid grid-cols-1 gap-2 sm:grid-cols-2 lg:grid-cols-3">
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
    <div className="flex flex-col gap-2 rounded-md border border-[var(--border)] bg-[var(--bg-secondary)]/40 p-3 hover:border-[var(--text-dim)] transition-colors">
      <div className="flex items-start gap-2 min-w-0">
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2 flex-wrap min-w-0">
            <span className="text-sm font-medium text-nav-color-hover truncate">{title}</span>
            {item.version && (
              <span className="text-[10px] text-[var(--text-tertiary)]">v{item.version}</span>
            )}
            {item.official && (
              <span className="rounded border border-blue-500/40 bg-blue-500/15 px-1.5 py-[1px] text-[10px] uppercase tracking-wide text-blue-400">{text("official", "官方")}</span>
            )}
            {installed && (
              <span className="rounded border border-emerald-500/40 bg-emerald-500/15 px-1.5 py-[1px] text-[10px] uppercase tracking-wide text-emerald-400">{text("installed", "已安装")}</span>
            )}
          </div>
          {slug && slug !== title && (
            <div className="text-[10px] font-mono text-[var(--text-tertiary)] truncate">{slug}</div>
          )}
        </div>
      </div>
      {item.description && (
        <p className="text-xs text-[var(--text-secondary)] line-clamp-3">{item.description}</p>
      )}
      <div className="flex items-center justify-between gap-2 mt-auto">
        <div className="flex items-center gap-1 text-[10px] text-[var(--text-tertiary)]">
          {item.source && <span className="rounded border border-[var(--border)] px-1.5 py-[1px] uppercase">{item.source}</span>}
          {(item.tags || []).slice(0, 2).map((t) => (
            <span key={t} className="rounded bg-[var(--bg-tertiary)] px-1.5 py-[1px]">{t}</span>
          ))}
        </div>
        <button
          onClick={onInstall}
          disabled={installing}
          className={installed ? styles.btn : styles.btnPrimary}
        >
          {installing ? text("Installing...", "安装中...") : installed ? text("Reinstall", "重新安装") : text("Install", "安装")}
        </button>
      </div>
    </div>
  );
}
