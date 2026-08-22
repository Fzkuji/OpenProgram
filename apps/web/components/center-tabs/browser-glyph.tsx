"use client";

import { ChromeIcon } from "@/components/animated-icons";

import styles from "./center-tabs.module.css";

export function BrowserGlyph({ size = 24 }: { size?: number }) {
  return (
    <span
      className={styles.browserGlyph}
      style={{ width: size, height: size }}
      aria-hidden="true"
    >
      <ChromeIcon size={Math.round(size * 0.62)} />
    </span>
  );
}
