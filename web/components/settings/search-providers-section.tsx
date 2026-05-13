"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import styles from "./settings-page.module.css";
import { ApiKey } from "./providers-section";

interface SearchProvider {
  id: string;
  name: string;
  description: string;
  /**
   * Catalog metadata sourced from
   * ``openprogram.tools.web_search.catalog``. Optional on the wire
   * because older builds of the API endpoint didn't return these
   * fields — UI degrades gracefully (hides Setup block) when absent.
   */
  tier?: string;
  signup_url?: string | null;
  docs_url?: string | null;
  setup_steps?: string[];
  priority: number;
  env_var: string | null;
  configured: boolean;
  available: boolean;
  is_default: boolean;
}

export function SearchProvidersSection() {
  const [providers, setProviders] = useState<SearchProvider[]>([]);
  const [defaultId, setDefaultId] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [search, setSearch] = useState("");
  const [saving, setSaving] = useState(false);

  const load = useCallback(async () => {
    try {
      const r = await fetch("/api/search-providers/list");
      const d = await r.json();
      const list: SearchProvider[] = d.providers || [];
      const def = d.default ?? null;
      setProviders(list);
      setDefaultId(def);
      // Initial selection: prefer the configured default, then the
      // first available backend, then the first row. Picking
      // ``list[0]`` blindly lands on Tavily (priority 100) which is
      // typically un-configured — confusing.
      setSelectedId((cur) => {
        if (cur) return cur;
        if (def && list.some((p) => p.id === def)) return def;
        const firstAvailable = list.find((p) => p.available);
        if (firstAvailable) return firstAvailable.id;
        return list[0]?.id ?? null;
      });
    } catch {
      /* ignore */
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  const setDefault = useCallback(async (id: string | null) => {
    setSaving(true);
    try {
      await fetch("/api/search-providers/default", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ provider: id }),
      });
      setDefaultId(id);
      // Refresh `is_default` flags on every row.
      setProviders((prev) =>
        prev.map((p) => ({ ...p, is_default: p.id === id })),
      );
    } catch {
      /* ignore */
    } finally {
      setSaving(false);
    }
  }, []);

  const matches = useCallback(
    (p: SearchProvider) =>
      !search.trim() ||
      p.name.toLowerCase().includes(search.toLowerCase()) ||
      p.id.toLowerCase().includes(search.toLowerCase()),
    [search],
  );

  const { active, inactive } = useMemo(() => {
    const visible = providers.filter(matches).sort((a, b) => a.priority - b.priority);
    return {
      active: visible.filter((p) => p.available),
      inactive: visible.filter((p) => !p.available),
    };
  }, [providers, matches]);

  const selected = providers.find((p) => p.id === selectedId);

  if (loading) {
    return (
      <div className={styles.section}>
        <div style={{ padding: 24, opacity: 0.6 }}>Loading…</div>
      </div>
    );
  }

  return (
    <div className={styles.section}>
      <div className={styles.providersLayout}>
        <div className={styles.providersSidebar}>
          <h2 className={styles.sectionTitle}>Web Search</h2>
          <div className={styles.providersSearch}>
            <input
              type="search"
              placeholder="Search backends…"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
            />
          </div>
          {active.length > 0 && (
            <>
              <div className={styles.providersGroupLabel}>Available</div>
              {active.map((p) => (
                <SearchProviderItem
                  key={p.id}
                  p={p}
                  active={selectedId === p.id}
                  onSelect={() => setSelectedId(p.id)}
                />
              ))}
            </>
          )}
          {inactive.length > 0 && (
            <>
              <div className={styles.providersGroupLabel}>Not configured</div>
              {inactive.map((p) => (
                <SearchProviderItem
                  key={p.id}
                  p={p}
                  active={selectedId === p.id}
                  onSelect={() => setSelectedId(p.id)}
                />
              ))}
            </>
          )}
        </div>

        <div className={styles.detail}>
          {selected ? (
            <SearchProviderDetail
              provider={selected}
              defaultId={defaultId}
              saving={saving}
              onSetDefault={setDefault}
              onChanged={load}
            />
          ) : (
            <div className={styles.detailEmpty}>
              Select a search backend on the left
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

function SearchProviderItem({
  p,
  active,
  onSelect,
}: {
  p: SearchProvider;
  active: boolean;
  onSelect: () => void;
}) {
  const dot = p.available ? "on" : p.configured ? "off" : "unconfigured";
  return (
    <div
      className={styles.providerItem + (active ? " " + styles.active : "")}
      onClick={onSelect}
    >
      <SearchProviderGlyph id={p.id} />
      <span className={styles.providerLabel}>{p.name}</span>
      {p.is_default && (
        <span className={styles.providerDefaultBadge}>Default</span>
      )}
      <span
        className={
          styles.providerDot +
          " " +
          (dot === "on"
            ? styles.on
            : dot === "off"
              ? styles.off
              : styles.unconfigured)
        }
        title={
          p.available
            ? "Available"
            : p.configured
              ? "Configured (inactive)"
              : "Not configured"
        }
      />
    </div>
  );
}

function SearchProviderDetail({
  provider,
  defaultId,
  saving,
  onSetDefault,
  onChanged,
}: {
  provider: SearchProvider;
  defaultId: string | null;
  saving: boolean;
  onSetDefault: (id: string | null) => void;
  onChanged: () => void;
}) {
  const subtitle = provider.env_var
    ? `API key env: ${provider.env_var}`
    : "No key needed — zero-config";
  const isDefault = provider.id === defaultId;

  return (
    <>
      <header className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <h2
            className="text-[18px] font-semibold"
            style={{ color: "var(--text-bright)" }}
          >
            {provider.name}
            {provider.tier && (
              <span className={styles.searchTierChip} title="Pricing / availability">
                {provider.tier}
              </span>
            )}
          </h2>
          <p className="mt-1 text-[13px]" style={{ color: "var(--text-muted)" }}>
            {subtitle}
          </p>
        </div>
        <div className="flex items-center gap-2 shrink-0">
          <span
            className={
              styles.providerDot +
              " " +
              (provider.available
                ? styles.on
                : provider.configured
                  ? styles.off
                  : styles.unconfigured)
            }
          />
          <span style={{ color: "var(--text-muted)", fontSize: 12 }}>
            {provider.available
              ? "Available"
              : provider.configured
                ? "Configured"
                : "Not configured"}
          </span>
        </div>
      </header>

      <div
        className="rounded-md p-3 text-[14px]"
        style={{
          background: "var(--bg-tertiary)",
          border: "1px solid var(--border)",
          color: "var(--text-primary)",
          lineHeight: 1.55,
        }}
      >
        {provider.description}
        <div className="mt-2" style={{ color: "var(--text-muted)", fontSize: 12 }}>
          Priority {provider.priority} — used as a fallback when no default is set
          or the default backend is unavailable.
        </div>
      </div>

      {/* Setup block — surfaces the signup URL + numbered setup_steps
          from openprogram.tools.web_search.catalog so users can go
          from "I picked this backend" to "I have a working API key"
          without leaving the panel. Hidden entirely when the catalog
          has nothing to add (i.e. signup_url + setup_steps both
          empty) so zero-config backends like DuckDuckGo don't show
          empty boilerplate. */}
      {(provider.signup_url ||
        (provider.setup_steps && provider.setup_steps.length > 0)) && (
        <SearchProviderSetup provider={provider} />
      )}

      {/* Default-provider control. Only meaningful when the backend is
          actually usable; otherwise pinning it as default would just
          fall back to whatever else is configured. Users switch the
          default by selecting another provider and clicking Set as
          default there — no explicit "clear" needed. */}
      <div className="flex items-center gap-3">
        <button
          type="button"
          disabled={!provider.available || saving || isDefault}
          onClick={() => onSetDefault(provider.id)}
          style={{
            padding: "6px 12px",
            borderRadius: 6,
            fontSize: 13,
            fontWeight: 500,
            background: isDefault
              ? "var(--bg-tertiary)"
              : "var(--accent-blue, #4f8ef7)",
            color: isDefault ? "var(--text-muted)" : "#fff",
            border: isDefault ? "1px solid var(--border)" : "1px solid transparent",
            cursor:
              !provider.available || saving || isDefault ? "default" : "pointer",
            opacity: !provider.available ? 0.5 : 1,
          }}
        >
          {isDefault ? "Default backend" : "Set as default"}
        </button>
      </div>

      {provider.env_var && (
        <ApiKey
          envVar={provider.env_var}
          configured={provider.configured}
          onChanged={onChanged}
        />
      )}

      {/* Live connectivity check — runs a tiny real query against the
          backend so users can confirm the key actually works before
          relying on it from the chat plus-menu. */}
      <SearchConnectivity providerId={provider.id} disabled={!provider.configured} />

      {!provider.env_var && (
        <div
          className="rounded-md p-3 text-[14px]"
          style={{
            background: "var(--bg-tertiary)",
            border: "1px solid var(--border)",
            color: "var(--text-primary)",
          }}
        >
          This backend doesn&apos;t require an API key — it&apos;s always available
          as a zero-config fallback when no other configured backend returns results.
        </div>
      )}
    </>
  );
}

/**
 * "Setup" block in the provider detail panel: a "Get API key →" button
 * (when ``signup_url`` is present) plus a numbered list of
 * ``setup_steps`` from the catalog. Hidden entirely by the caller
 * when both fields are empty (e.g. zero-config DuckDuckGo).
 */
function SearchProviderSetup({ provider }: { provider: SearchProvider }) {
  const steps = provider.setup_steps || [];
  return (
    <div className={styles.detailSection}>
      <div className={styles.detailSectionTitle}>
        <span>Setup</span>
      </div>
      {provider.signup_url && (
        <div className={styles.detailRow}>
          <a
            className={styles.searchSetupGetKey}
            href={provider.signup_url}
            target="_blank"
            rel="noopener noreferrer"
          >
            Get API key <span aria-hidden>→</span>
          </a>
          {provider.docs_url && provider.docs_url !== provider.signup_url && (
            <a
              href={provider.docs_url}
              target="_blank"
              rel="noopener noreferrer"
              className={styles.miniAction}
              style={{ textDecoration: "none" }}
            >
              Docs
            </a>
          )}
        </div>
      )}
      {steps.length > 0 && (
        <ol className={styles.searchSetupSteps}>
          {steps.map((step, i) => (
            <li key={i}>{step}</li>
          ))}
        </ol>
      )}
    </div>
  );
}

function SearchConnectivity({
  providerId,
  disabled,
}: {
  providerId: string;
  disabled: boolean;
}) {
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<{
    kind: "ok" | "err";
    text: string;
    title?: string;
  } | null>(null);

  // Reset state when the user switches to a different provider —
  // otherwise the previous "✓ 200 ms" stays visible on the new panel.
  useEffect(() => {
    setResult(null);
  }, [providerId]);

  async function test() {
    setBusy(true);
    setResult({ kind: "ok", text: "…" });
    try {
      const r = await fetch(
        `/api/search-providers/${encodeURIComponent(providerId)}/test`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({}),
        },
      );
      const d = await r.json();
      if (d.ok) {
        setResult({
          kind: "ok",
          text: `✓ ${d.latency_ms || 0} ms`,
          title:
            typeof d.result_count === "number"
              ? `Returned ${d.result_count} result${d.result_count === 1 ? "" : "s"}`
              : undefined,
        });
      } else {
        setResult({ kind: "err", text: "✗ failed", title: d.error });
      }
    } catch (e) {
      setResult({ kind: "err", text: "✗", title: (e as Error).message });
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className={styles.detailSection}>
      <div className={styles.detailSectionTitle}>
        <span>Connectivity check</span>
      </div>
      <div className={styles.detailRow}>
        <span className={styles.modelCountSummary} style={{ flex: 1 }}>
          Runs a tiny live query to validate the API key.
        </span>
        {result && (
          <span
            className={
              styles.testResult +
              " " +
              (result.kind === "ok" ? styles.ok : styles.err)
            }
            title={result.title}
          >
            {result.text}
          </span>
        )}
        <button
          className={styles.btn}
          onClick={test}
          disabled={busy || disabled}
          title={disabled ? "Configure the API key first" : undefined}
        >
          Check
        </button>
      </div>
    </div>
  );
}

/* Small monogram circle for search providers — same shape as the LLM
   provider icons but using letters instead of brand SVGs. */
function SearchProviderGlyph({ id }: { id: string }) {
  const letter = id.charAt(0).toUpperCase();
  const colors: Record<string, string> = {
    t: "#3b82f6",
    e: "#7c6fcd",
    d: "#f97316",
    b: "#10b981",
    g: "#84cc16",
  };
  const c = colors[letter.toLowerCase()] || "#6b7280";
  return (
    <div
      className={styles.providerIconLetter}
      style={{
        background: c + "22",
        color: c,
        border: `1px solid ${c}55`,
        width: 24,
        height: 24,
        display: "inline-flex",
        alignItems: "center",
        justifyContent: "center",
        borderRadius: 6,
        flexShrink: 0,
        fontWeight: 600,
        fontSize: 12,
      }}
    >
      {letter}
    </div>
  );
}
