"use client";

import { ApiKey } from "../providers";
import styles from "../providers/provider-pane.module.css";
import { Button } from "@/components/ui/button";
import { SearchConnectivity } from "./connectivity";
import { SearchProviderGlyph } from "./glyph";
import { SearchProviderSetup } from "./setup";
import type { SearchProvider } from "./types";
import { useTranslation } from "@/lib/i18n";

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
  const { text } = useTranslation();
  const isDefault = provider.id === defaultId;

  return (
    <div className={`${styles.detailSurface} flex flex-col gap-[16px] min-w-0 p-[16px_20px_20px]`}>
      <div className={styles.detailHeader}>
        <div className={`${styles.detailIcon} w-[40px] h-[40px] flex items-center justify-center`}>
          <SearchProviderGlyph id={provider.id} size={40} />
        </div>
        <div className="flex-1 min-w-0">
          <div className={styles.detailTitle}>
            {provider.name}
            {provider.tier && (
              <span className={styles.searchTierChip} data-tip={text("Pricing / availability", "价格 / 可用性")}>
                {provider.tier}
              </span>
            )}
          </div>
          <div className={styles.detailSubtitle}>
            {provider.env_var ? (
              <>
                {text("API key env:", "API key 环境变量：")}{" "}
                <span className={styles.codeName}>{provider.env_var}</span>
              </>
            ) : (
              text("No key needed - zero-config", "不需要 key - 零配置")
            )}
          </div>
        </div>
        <div className={`${styles.detailHeaderActions} flex items-center shrink-0 gap-[12px]`}>
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
          <span className={styles.detailStatus}>
            {provider.available
              ? text("Available", "可用")
              : provider.configured
                ? text("Configured", "已配置")
                : text("Not configured", "未配置")}
          </span>
        </div>
      </div>

      <div className={styles.searchDescription}>
        {provider.description}
        <div className={styles.searchDescriptionMeta}>
          {text(
            `Priority ${provider.priority}. Used as a fallback when no default is set or the default backend is unavailable.`,
            `优先级 ${provider.priority}。未设置默认后端或默认后端不可用时，会作为 fallback 使用。`,
          )}
        </div>
      </div>

      {/* Setup block — surfaces the signup URL + numbered setup steps
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
      <Button
        type="button"
        variant="elevated"
        className={isDefault ? "disabled:opacity-100" : undefined}
        aria-pressed={isDefault}
        disabled={!provider.available || saving || isDefault}
        onClick={() => onSetDefault(provider.id)}
      >
        {isDefault ? text("✓ Default backend", "✓ 默认后端") : text("Set as default", "设为默认")}
      </Button>

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
      <SearchConnectivity providerId={provider.id} disabled={!provider.configured} needsKey={Boolean(provider.env_var)} />

      {!provider.env_var && (
        <div className={styles.searchDescription}>
          {text(
            "This backend doesn't require an API key. It's always available as a zero-config fallback when no other configured backend returns results.",
            "这个后端不需要 API key。当其他已配置后端没有返回结果时，它会作为零配置 fallback 使用。",
          )}
        </div>
      )}
    </div>
  );
}
