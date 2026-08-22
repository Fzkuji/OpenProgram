/**
 * Inline MCP catalog for the Discover tab.
 */
"use client";

import { useEffect, useRef, useState } from "react";

import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { cn } from "@/lib/utils";
import { useTranslation } from "@/lib/i18n";
import { jsonFetch } from "@/lib/net/fetch-client";

import styles from "./mcp-page.module.css";

export interface CatalogServer {
  name: string;
  type: string;
  description?: string;
  homepage?: string;
  tags?: string[];
  command?: string[];
  url?: string;
  auth?: { kind: string };
  source_entry_hash?: string;  // backend computes this so we can round-trip
  [k: string]: unknown;
}

interface CatalogData {
  name: string;
  description?: string;
  servers: CatalogServer[];
  skipped: number;
  sourceUrl: string;
}

export function CatalogPanel({
  existingNames, query = "", onInstalled,
}: {
  existingNames: Set<string>;
  query?: string;
  onInstalled: (name: string) => Promise<void>;
}) {
  const { text } = useTranslation();
  const [url, setUrl] = useState("");
  const [catalogLoading, setCatalogLoading] = useState(false);
  const [installing, setInstalling] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [catalog, setCatalog] = useState<CatalogData | null>(null);
  const catalogAbort = useRef<AbortController | null>(null);
  // Curated suggestions surfaced above the URL input — one-click to
  // pull in a known catalog or install a single quick-install entry.
  const [suggested, setSuggested] = useState<
    { label: string; url: string; description?: string }[]
  >([]);
  const [quickInstall, setQuickInstall] = useState<CatalogServer[]>([]);
  const needle = query.trim().toLowerCase();
  const matches = (server: CatalogServer) => {
    if (!needle) return true;
    return [server.name, server.description, server.type, ...(server.tags || [])]
      .some((value) => (value || "").toLowerCase().includes(needle));
  };
  const shownQuickInstall = quickInstall.filter(matches);
  const shownCatalogServers = (catalog?.servers || []).filter(matches);

  useEffect(() => {
    const ac = new AbortController();
    jsonFetch<{
      suggested?: { label: string; url: string; description?: string }[];
      quick_install?: CatalogServer[];
    }>("/api/mcp/catalog/suggested", { signal: ac.signal })
      .then((d) => {
        setSuggested(Array.isArray(d.suggested) ? d.suggested : []);
        setQuickInstall(Array.isArray(d.quick_install) ? d.quick_install : []);
      })
      .catch((error) => {
        if ((error as Error).name !== "AbortError") {
          /* offline / fresh install — leave empty */
        }
      });
    return () => {
      ac.abort();
      catalogAbort.current?.abort();
    };
  }, []);

  async function fetchCatalog(nextUrl?: string) {
    const target = (nextUrl ?? url).trim();
    if (nextUrl) setUrl(nextUrl);
    catalogAbort.current?.abort();
    const ac = new AbortController();
    catalogAbort.current = ac;
    setErr(null); setCatalog(null);
    if (!target) { setErr(text("paste a catalog URL first", "请先粘贴目录 URL")); return; }
    setCatalogLoading(true);
    try {
      const data = await jsonFetch<Omit<CatalogData, "sourceUrl">>(
        `/api/mcp/catalog?url=${encodeURIComponent(target)}`,
        { signal: ac.signal },
      );
      setCatalog({ ...data, sourceUrl: target });
    } catch (e) {
      if ((e as Error).name !== "AbortError") {
        setErr(e instanceof Error ? e.message : String(e));
      }
    } finally {
      if (catalogAbort.current === ac) setCatalogLoading(false);
    }
  }

  async function install(entry: CatalogServer, sourceUrl = "") {
    setErr(null);
    if (existingNames.has(entry.name)) {
      setErr(text(`already installed: ${entry.name}`, `已安装：${entry.name}`));
      return;
    }
    setInstalling(entry.name);
    try {
      // Carry catalog provenance into the install body so the backend
      // can stash source_catalog_url + source_entry_hash on the
      // persisted MCPServerConfig. Later diff calls compare the
      // upstream catalog against this stored hash to decide if the
      // server is outdated.
      const body = {
        ...entry,
        ...(sourceUrl ? { source_catalog_url: sourceUrl } : {}),
      };
      await jsonFetch("/api/mcp/servers", {
        method: "POST",
        body: JSON.stringify(body),
      });
      await onInstalled(entry.name);
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setInstalling(null);
    }
  }

  return (
    <div className="mx-auto flex w-full max-w-[880px] flex-col gap-5">
      <div>
        <h2 className="text-base font-semibold text-[var(--text-bright)]">
          {text("Discover MCP servers", "发现 MCP 服务器")}
        </h2>
        <p className="mt-1 text-xs text-[var(--text-secondary)]">
          {text("Install a curated server or load a compatible catalog URL.", "安装精选服务器，或载入兼容的目录 URL。")}
        </p>
      </div>
      <div className="flex flex-col gap-3">
          {suggested.length > 0 && (
            <div className="flex flex-col gap-1.5">
              <Label>{text("Suggested catalogs", "推荐目录")}</Label>
              <div className="flex flex-wrap gap-1.5">
                {suggested.map((s) => (
                  <button
                    key={s.url}
                    title={s.description || s.url}
                    onClick={() => { void fetchCatalog(s.url); }}
                    className={styles.actionBtn}
                    disabled={catalogLoading || installing !== null}
                  >
                    {s.label}
                  </button>
                ))}
              </div>
            </div>
          )}

          {shownQuickInstall.length > 0 && (
            <div className="flex flex-col gap-1.5">
              <Label>{text("Quick install", "快速安装")}</Label>
              <div className="flex flex-col gap-1.5">
                {shownQuickInstall.map((s) => {
                  const installed = existingNames.has(s.name);
                  return (
                    <div key={s.name}
                         className="flex min-w-0 flex-col gap-3 rounded-md border px-3 py-2 sm:flex-row sm:items-center"
                         style={{ borderColor: "var(--border)" }}>
                      <div className="flex-1 min-w-0">
                        <div className="font-mono text-sm font-semibold">{s.name}</div>
                        {s.description && (
                          <div className="mt-0.5 text-xs" style={{ color: "var(--text-muted)" }}>
                            {s.description}
                          </div>
                        )}
                      </div>
                      <button
                        className={cn(styles.actionBtn, installed ? "" : styles.actionBtnPrimary, "self-end sm:self-auto")}
                        onClick={() => void install(s)}
                        disabled={installed || catalogLoading || installing !== null}
                      >
                        {installed ? text("Installed", "已安装") : installing === s.name ? text("Installing...", "安装中...") : text("Install", "安装")}
                      </button>
                    </div>
                  );
                })}
              </div>
            </div>
          )}

          <div className="flex flex-col gap-1.5">
            <Label htmlFor="cat-url">{text("Custom catalog URL", "自定义目录 URL")}</Label>
            <div className="flex min-w-0 gap-2">
              <Input
                id="cat-url"
                value={url}
                onChange={(e) => setUrl(e.target.value)}
                placeholder="https://example.com/mcp-catalog.json"
                className="min-w-0 flex-1 font-mono"
              />
              <button
                className={cn(styles.actionBtn, styles.actionBtnPrimary)}
                onClick={() => void fetchCatalog()}
                disabled={catalogLoading || installing !== null}
              >
                {catalogLoading ? text("Fetching...", "获取中...") : text("Load", "加载")}
              </button>
            </div>
            <div className="text-xs" style={{ color: "var(--text-muted)" }}>
              {text("Catalog is a JSON object with a ", "目录是一个包含 ")}
              <code>servers</code>
              {text(" array; each entry matches the local mcp_servers.json schema.", " 数组的 JSON 对象；每一项都匹配本地 mcp_servers.json schema。")}
            </div>
          </div>

          {err && (
            <div className="rounded-md border p-2 font-mono text-xs"
                 style={{ borderColor: "var(--accent-red)", color: "var(--accent-red)" }}>
              {err}
            </div>
          )}

          {catalog && (
            <div className="flex flex-col gap-2">
              <div className="text-xs" style={{ color: "var(--text-muted)" }}>
                {text(
                  `${catalog.name} - ${catalog.servers.length} installable`,
                  `${catalog.name} - ${catalog.servers.length} 个可安装`,
                )}
                {catalog.skipped > 0 && text(
                  `, ${catalog.skipped} skipped (invalid)`,
                  `，跳过 ${catalog.skipped} 个无效项`,
                )}
              </div>
              <div className="flex flex-col gap-1.5">
                {shownCatalogServers.map((s) => {
                  const installed = existingNames.has(s.name);
                  return (
                    <div key={s.name}
                         className="flex min-w-0 flex-col gap-3 rounded-md border px-3 py-2 sm:flex-row sm:items-center"
                         style={{ borderColor: "var(--border)" }}>
                      <div className="min-w-0 flex-1">
                        <div className="font-mono text-sm font-semibold">
                          {s.name}
                          <span className="ml-2 text-xs font-normal"
                                style={{ color: "var(--text-muted)" }}>
                            {s.type}{s.auth?.kind && s.auth.kind !== "none"
                              ? ` · ${s.auth.kind}` : ""}
                          </span>
                        </div>
                        {s.description && (
                          <div className="mt-0.5 text-xs"
                               style={{ color: "var(--text-muted)" }}>
                            {s.description}
                          </div>
                        )}
                        <div className="mt-1 break-all text-xs font-mono"
                             style={{ color: "var(--text-muted)" }}>
                          {s.type === "local"
                            ? <code>{(s.command || []).join(" ")}</code>
                            : <code>{s.url}</code>}
                        </div>
                      </div>
                      <button
                        onClick={() => void install(s, catalog.sourceUrl)}
                        disabled={installed || catalogLoading || installing !== null}
                        className={cn(styles.actionBtn, styles.actionBtnPrimary, "self-end sm:self-auto")}
                      >
                        {installed
                          ? text("Installed", "已安装")
                          : installing === s.name
                            ? text("Installing...", "安装中...")
                            : text("Install", "安装")}
                      </button>
                    </div>
                  );
                })}
                {shownCatalogServers.length === 0 && (
                  <div className="py-8 text-center text-xs text-[var(--text-tertiary)]">
                    {text("No matching servers.", "没有匹配的服务器。")}
                  </div>
                )}
              </div>
            </div>
          )}
      </div>
    </div>
  );
}
