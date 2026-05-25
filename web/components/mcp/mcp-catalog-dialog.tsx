/**
 * MCP catalog dialog — browse a registry of pre-configured MCP
 * servers, install pick-one + bring the parent ``mcp-page`` up to
 * date when one is added. Pulled out so the page file isn't carrying
 * ~170 lines of unrelated dialog rendering.
 */
"use client";

import { useEffect, useState } from "react";

import {
  Dialog, DialogContent, DialogFooter, DialogHeader, DialogTitle,
} from "@/components/ui/dialog";
import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";

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

export function CatalogDialog({
  existingNames, onClose, onInstalled,
}: {
  existingNames: Set<string>;
  onClose: () => void;
  onInstalled: (name: string) => void;
}) {
  const [url, setUrl] = useState("");
  const [busy, setBusy] = useState<null | "fetch" | string>(null);
  const [err, setErr] = useState<string | null>(null);
  const [catalog, setCatalog] = useState<{
    name: string;
    description?: string;
    servers: CatalogServer[];
    skipped: number;
  } | null>(null);
  // Curated suggestions surfaced above the URL input — one-click to
  // pull in a known catalog or install a single quick-install entry.
  const [suggested, setSuggested] = useState<
    { label: string; url: string; description?: string }[]
  >([]);
  const [quickInstall, setQuickInstall] = useState<CatalogServer[]>([]);

  useEffect(() => {
    fetch("/api/mcp/catalog/suggested")
      .then((r) => (r.ok ? r.json() : null))
      .then((d) => {
        if (!d) return;
        setSuggested(Array.isArray(d.suggested) ? d.suggested : []);
        setQuickInstall(Array.isArray(d.quick_install) ? d.quick_install : []);
      })
      .catch(() => { /* offline / fresh install — leave empty */ });
  }, []);

  async function fetchCatalog() {
    setErr(null); setCatalog(null);
    if (!url.trim()) { setErr("paste a catalog URL first"); return; }
    setBusy("fetch");
    try {
      const r = await fetch(
        `/api/mcp/catalog?url=${encodeURIComponent(url.trim())}`,
      );
      const data = await r.json();
      if (!r.ok) {
        setErr(data.detail || `HTTP ${r.status}`);
        return;
      }
      setCatalog(data);
    } catch (e) {
      setErr(String(e));
    } finally {
      setBusy(null);
    }
  }

  async function install(entry: CatalogServer) {
    setErr(null);
    if (existingNames.has(entry.name)) {
      setErr(`already installed: ${entry.name}`);
      return;
    }
    setBusy(entry.name);
    try {
      // Carry catalog provenance into the install body so the backend
      // can stash source_catalog_url + source_entry_hash on the
      // persisted MCPServerConfig. Later diff calls compare the
      // upstream catalog against this stored hash to decide if the
      // server is outdated.
      const body = {
        ...entry,
        source_catalog_url: url.trim(),
      };
      const r = await fetch("/api/mcp/servers", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      const data = await r.json();
      if (!r.ok) {
        setErr(data.detail || `HTTP ${r.status}`);
        return;
      }
      onInstalled(entry.name);
    } catch (e) {
      setErr(String(e));
    } finally {
      setBusy(null);
    }
  }

  return (
    <Dialog open={true} onOpenChange={(o) => { if (!o) onClose(); }}>
      <DialogContent className="sm:max-w-[640px]">
        <DialogHeader>
          <DialogTitle>Browse MCP catalog</DialogTitle>
        </DialogHeader>

        <div className="flex flex-col gap-3">
          {suggested.length > 0 && (
            <div className="flex flex-col gap-1.5">
              <Label>Suggested catalogs</Label>
              <div className="flex flex-wrap gap-1.5">
                {suggested.map((s) => (
                  <button
                    key={s.url}
                    title={s.description || s.url}
                    onClick={() => { setUrl(s.url); void fetchCatalog(); }}
                    className={cn(styles.actionBtn, styles.actionBtnSecondary)}
                  >
                    {s.label}
                  </button>
                ))}
              </div>
            </div>
          )}

          {quickInstall.length > 0 && (
            <div className="flex flex-col gap-1.5">
              <Label>Quick install</Label>
              <div className="flex flex-col gap-1.5">
                {quickInstall.map((s) => {
                  const installed = existingNames.has(s.name);
                  return (
                    <div key={s.name}
                         className="flex items-start gap-3 rounded-md border px-3 py-2"
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
                        className={cn(styles.actionBtn, installed ? "" : styles.actionBtnPrimary)}
                        onClick={() => void install(s)}
                        disabled={installed || busy === s.name}
                      >
                        {installed ? "Installed" : busy === s.name ? "Installing…" : "Install"}
                      </button>
                    </div>
                  );
                })}
              </div>
            </div>
          )}

          <div className="flex flex-col gap-1.5">
            <Label htmlFor="cat-url">Custom catalog URL</Label>
            <div className="flex gap-2">
              <Input
                id="cat-url"
                value={url}
                onChange={(e) => setUrl(e.target.value)}
                placeholder="https://example.com/mcp-catalog.json"
                className="font-mono"
              />
              <button
                className={cn(styles.actionBtn, styles.actionBtnPrimary)}
                onClick={() => void fetchCatalog()}
                disabled={busy === "fetch"}
              >
                {busy === "fetch" ? "Fetching…" : "Load"}
              </button>
            </div>
            <div className="text-xs" style={{ color: "var(--text-muted)" }}>
              Catalog is a JSON object with a <code>servers</code> array;
              each entry matches the local mcp_servers.json schema.
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
                {catalog.name} — {catalog.servers.length} installable
                {catalog.skipped > 0 && `, ${catalog.skipped} skipped (invalid)`}
              </div>
              <div className="flex max-h-[360px] flex-col gap-1.5 overflow-y-auto">
                {catalog.servers.map((s) => {
                  const installed = existingNames.has(s.name);
                  return (
                    <div key={s.name}
                         className="flex items-start gap-3 rounded-md border px-3 py-2"
                         style={{ borderColor: "var(--border)" }}>
                      <div className="flex-1">
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
                        <div className="mt-1 text-xs font-mono"
                             style={{ color: "var(--text-muted)" }}>
                          {s.type === "local"
                            ? <code>{(s.command || []).join(" ")}</code>
                            : <code>{s.url}</code>}
                        </div>
                      </div>
                      <button
                        className={cn(styles.actionBtn, styles.actionBtnPrimary)}
                        onClick={() => void install(s)}
                        disabled={installed || busy === s.name}
                      >
                        {installed
                          ? "Installed"
                          : busy === s.name
                            ? "Installing…"
                            : "Install"}
                      </button>
                    </div>
                  );
                })}
              </div>
            </div>
          )}
        </div>

        <DialogFooter>
          <button className={styles.actionBtn} onClick={onClose}>
            Close
          </button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
