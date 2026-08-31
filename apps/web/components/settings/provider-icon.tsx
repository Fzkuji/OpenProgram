"use client";

import styles from "./settings-page.module.css";

/** Offline-safe provider identity. Provider brand assets used to come from
 * runtime CDNs that strict CSP rejected; the stable initial is available on
 * every deployment without adding network or cache dependencies. */
export function ProviderIcon({ id, size = 24 }: { id: string; size?: number }) {
  const letter = (id[0] || "?").toUpperCase();
  return (
    <span
      className={styles.providerIconLetter}
      style={{ width: size, height: size }}
      title={id}
    >
      {letter}
    </span>
  );
}
