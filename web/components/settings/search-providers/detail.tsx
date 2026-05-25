"use client";

import { ApiKey } from "../providers";
import styles from "../settings-page.module.css";
import { SearchConnectivity } from "./connectivity";
import { SearchProviderSetup } from "./setup";
import type { SearchProvider } from "./types";

export function SearchProviderDetail({
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

