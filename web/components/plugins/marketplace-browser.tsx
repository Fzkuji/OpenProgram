"use client";

import { useEffect, useState } from "react";
import styles from "./plugins.module.css";
import { usePluginsStore } from "@/lib/plugins-store";
import { AddMarketplaceDialog } from "./add-marketplace-dialog";

interface IndexItem {
  name?: string;
  description?: string;
  source?: string;
  spec?: string;
  url?: string;
  version?: string;
}

export function MarketplaceBrowser() {
  const { marketplaces, refreshMarketplaces, removeMarketplace, fetchMarketplaceIndex, install } =
    usePluginsStore();
  const [selectedId, setSelectedId] = useState<string>("");
  const [items, setItems] = useState<IndexItem[]>([]);
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState("");
  const [addOpen, setAddOpen] = useState(false);
  const [busySpec, setBusySpec] = useState("");

  useEffect(() => {
    refreshMarketplaces();
  }, [refreshMarketplaces]);

  useEffect(() => {
    if (!selectedId && marketplaces.length > 0) {
      setSelectedId(marketplaces[0].id);
    }
  }, [marketplaces, selectedId]);

  useEffect(() => {
    if (!selectedId) return;
    setLoading(true);
    setErr("");
    fetchMarketplaceIndex(selectedId)
      .then((r) => setItems(r as IndexItem[]))
      .catch((e) => setErr(String(e)))
      .finally(() => setLoading(false));
  }, [selectedId, fetchMarketplaceIndex]);

  const doInstall = async (item: IndexItem) => {
    // marketplace schema 兼容 claude-code：source 是 {pip|npm|git|path}，spec 是包名/url
    const source = item.source || (item.url ? "git" : "pip");
    const spec = item.spec || item.url || item.name || "";
    if (!spec) {
      alert("此条目缺 source/spec/url，无法安装");
      return;
    }
    setBusySpec(spec);
    try {
      const r = await install(source, spec);
      alert(r.success ? "安装成功" : `安装失败：\n${r.log.slice(0, 500)}`);
    } finally {
      setBusySpec("");
    }
  };

  return (
    <div>
      <div style={{ display: "flex", gap: 8, alignItems: "center", marginBottom: 12 }}>
        <select
          className={styles.select}
          value={selectedId}
          onChange={(e) => setSelectedId(e.target.value)}
        >
          {marketplaces.length === 0 && <option value="">(暂无 marketplace)</option>}
          {marketplaces.map((m) => (
            <option key={m.id} value={m.id}>{m.name}</option>
          ))}
        </select>
        <button className={styles.btn} onClick={() => setAddOpen(true)}>+ 添加</button>
        {selectedId && (
          <button
            className={styles.btnDanger}
            onClick={async () => {
              if (!confirm("移除此 marketplace?")) return;
              await removeMarketplace(selectedId);
              setSelectedId("");
              setItems([]);
            }}
          >移除</button>
        )}
        <div className={styles.spacer} />
      </div>

      {loading && <div className={styles.empty}>加载中…</div>}
      {err && <div className={styles.errorBox}>{err}</div>}
      {!loading && !err && items.length === 0 && (
        <div className={styles.empty}>该 marketplace 没有条目。</div>
      )}
      {items.map((it, i) => (
        <div key={i} className={styles.card}>
          <div className={styles.cardMain}>
            <div className={styles.rowName}>
              {it.name || "(unnamed)"}{" "}
              {it.version && (
                <span style={{ fontSize: 12, fontWeight: 400, color: "var(--text-dim)" }}>
                  v{it.version}
                </span>
              )}
            </div>
            {it.description && <div className={styles.rowDesc}>{it.description}</div>}
            <div className={styles.rowMeta}>
              {it.source && <span className={styles.badge}>{it.source}</span>}
              <span style={{ color: "var(--text-dim)" }}>{it.spec || it.url}</span>
            </div>
          </div>
          <button
            className={styles.btnPrimary}
            disabled={busySpec === (it.spec || it.url || it.name)}
            onClick={() => doInstall(it)}
          >
            {busySpec === (it.spec || it.url || it.name) ? "安装中…" : "Install"}
          </button>
        </div>
      ))}

      {addOpen && <AddMarketplaceDialog onClose={() => setAddOpen(false)} />}
    </div>
  );
}
