"use client";

import styles from "../settings-page.module.css";
import { SearchProviderGlyph } from "./glyph";
import type { SearchProvider } from "./types";
import { useTranslation } from "@/lib/i18n";

export function SearchProviderItem({
  p,
  active,
  onSelect,
}: {
  p: SearchProvider;
  active: boolean;
  onSelect: () => void;
}) {
  const { text } = useTranslation();
  const dot = p.available ? "on" : p.configured ? "off" : "unconfigured";
  return (
    <div
      className={styles.providerItem + (active ? " " + styles.active : "")}
      onClick={onSelect}
    >
      <SearchProviderGlyph id={p.id} />
      <span className={styles.providerLabel}>{p.name}</span>
      {p.is_default && (
        <span className={styles.providerDefaultBadge}>{text("Default", "默认")}</span>
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
            ? text("Available", "可用")
            : p.configured
              ? text("Configured (inactive)", "已配置（未启用）")
              : text("Not configured", "未配置")
        }
      />
    </div>
  );
}
