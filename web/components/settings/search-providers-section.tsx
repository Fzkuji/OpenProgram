"use client";

import { useCallback, useEffect, useState } from "react";
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

  const load = useCallback(async () => {
    try {
      const r = await fetch("/api/search-providers/list");
      const d = await r.json();
      setProviders(d.providers || []);
    } catch {
      /* ignore */
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  if (loading) {
    return <div style={{ padding: 16, opacity: 0.6 }}>Loading…</div>;
  }

  return (
    <div>
      <div className={styles.detailHeader}>
        <div className={styles.detailTitle}>Web Search Providers</div>
        <div className={styles.detailSubtitle}>
          Tried in priority order until one is available. Tavily first (LLM-tuned),
          Exa second (neural), DuckDuckGo as zero-key fallback.
        </div>
      </div>
      {providers.map((p) => (
        <div key={p.id} className={styles.detailSection}>
          <div className={styles.detailSectionTitle}>
            <span>
              {p.name}
              <span
                className={styles.modelCountSummary}
                style={{ marginLeft: 8 }}
                title={p.available ? "Available" : "Not configured"}
              >
                {p.available ? "● available" : "○ not configured"}
              </span>
            </span>
            <span className={styles.modelCountSummary}>priority {p.priority}</span>
          </div>
          <div style={{ fontSize: 12, opacity: 0.7, padding: "4px 0 8px" }}>
            {p.description}
          </div>
          {p.env_var && (
            <ApiKey
              envVar={p.env_var}
              configured={p.configured}
              onChanged={load}
            />
          )}
        </div>
      ))}
    </div>
  );
}
