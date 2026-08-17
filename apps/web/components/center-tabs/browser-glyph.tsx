import { Globe2 } from "lucide-react";

import styles from "./center-tabs.module.css";

export function BrowserGlyph({ size = 24 }: { size?: number }) {
  return (
    <span
      className={styles.browserGlyph}
      style={{ width: size, height: size }}
      aria-hidden="true"
    >
      <Globe2 size={Math.round(size * 0.62)} strokeWidth={2.1} />
    </span>
  );
}
