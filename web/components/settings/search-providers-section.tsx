"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import styles from "./settings-page.module.css";
import { ApiKey } from "./providers-section";

interface SearchProvider {
  id: string;
  name: string;
  description: string;
  priority: number;
  env_var: string | null;
  configured: boolean;
  available: boolean;
}

export function SearchProvidersSection() {
  const [providers, setProviders] = useState<SearchProvider[]>([]);
  const [loading, setLoading] = useState(true);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [search, setSearch] = useState("");

  const load = useCallback(async () => {
    try {
      const r = await fetch("/api/search-providers/list");
      const d = await r.json();
      const list: SearchProvider[] = d.providers || [];
      setProviders(list);
      setSelectedId((cur) => cur ?? list[0]?.id ?? null);
    } catch {
      /* ignore */
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { load(); }, [load]);

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
            <SearchProviderDetail provider={selected} onChanged={load} />
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
  onChanged,
}: {
  provider: SearchProvider;
  onChanged: () => void;
}) {
  const subtitle = provider.env_var
    ? `API key env: ${provider.env_var}`
    : "No key needed — zero-config";

  return (
    <>
      <header className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <h2
            className="text-[18px] font-semibold"
            style={{ color: "var(--text-bright)" }}
          >
            {provider.name}
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
          Priority {provider.priority} — tried in ascending order until one returns results.
        </div>
      </div>

      {provider.env_var && (
        <div>
          <div
            style={{
              color: "var(--text-bright)",
              fontWeight: 600,
              fontSize: 14,
              marginBottom: 8,
            }}
          >
            API Key
          </div>
          <ApiKey
            envVar={provider.env_var}
            configured={provider.configured}
            onChanged={onChanged}
          />
        </div>
      )}

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
