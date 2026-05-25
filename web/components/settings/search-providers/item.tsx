"use client";

import styles from "../settings-page.module.css";
import { SearchProviderGlyph } from "./glyph";
import type { SearchProvider } from "./types";

export function SearchProviderItem({
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

