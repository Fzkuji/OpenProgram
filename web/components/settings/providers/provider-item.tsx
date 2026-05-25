"use client";

import { ProviderIcon } from "../provider-icon";

import styles from "../settings-page.module.css";
import type { Provider } from "./types";

/** One row in the left sidebar — provider icon, label, and a colored
 *  status dot (green = enabled, yellow = configured-but-off, grey =
 *  unconfigured). */
export function ProviderItem({
  p,
  active,
  onSelect,
}: {
  p: Provider;
  active: boolean;
  onSelect: () => void;
}) {
  const dot = p.enabled ? "on" : p.configured ? "off" : "unconfigured";
  return (
    <div
      className={styles.providerItem + (active ? " " + styles.active : "")}
      onClick={onSelect}
    >
      <ProviderIcon id={p.id} size={24} />
      <span className={styles.providerLabel}>{p.label}</span>
      <span
        className={
          styles.providerDot +
          " " +
          (dot === "on" ? styles.on : dot === "off" ? styles.off : styles.unconfigured)
        }
        title={
          p.enabled ? "Enabled" : p.configured ? "Not enabled" : "Not configured"
        }
      />
    </div>
  );
}
