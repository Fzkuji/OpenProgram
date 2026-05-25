"use client";

import styles from "../settings-page.module.css";
import type { Provider } from "./types";

/** Provider-specific setup blurb rendered in the detail pane.
 *  Tiny markdown subset: backticked spans → inline <code>; lines
 *  starting with "$ " render as a command row. Avoids pulling a
 *  markdown lib for what is essentially a small static help string
 *  per provider. */
export function SetupHint({ hint, configured }: { hint: string; configured: boolean }) {
  const lines = hint.split("\n");
  return (
    <div className={styles.detailSection}>
      <div className={styles.detailSectionTitle}>
        <span>Setup</span>
        <span className={styles.modelCountSummary}>
          {configured ? "Detected" : "Not running"}
        </span>
      </div>
      <div style={{ color: "var(--text-muted)", fontSize: 13, lineHeight: 1.55 }}>
        {lines.map((line, i) => {
          const isCmd = line.startsWith("$ ");
          if (isCmd) {
            return (
              <pre
                key={i}
                style={{
                  margin: "4px 0",
                  padding: "6px 10px",
                  background: "var(--bg-secondary)",
                  border: "1px solid var(--border)",
                  borderRadius: 6,
                  fontFamily: "var(--font-mono)",
                  fontSize: 12,
                  overflow: "auto",
                }}
              >
                {line.slice(2)}
              </pre>
            );
          }
          const segments = line.split(/(`[^`]+`)/g).map((seg, j) => {
            if (seg.startsWith("`") && seg.endsWith("`")) {
              return (
                <code
                  key={j}
                  style={{
                    background: "var(--bg-tertiary)",
                    padding: "0 4px",
                    borderRadius: 3,
                    fontSize: 12,
                  }}
                >
                  {seg.slice(1, -1)}
                </code>
              );
            }
            return <span key={j}>{seg}</span>;
          });
          return <div key={i}>{segments.length && line ? segments : <br />}</div>;
        })}
      </div>
    </div>
  );
}

/** CLI-runtime providers (e.g. claude-code) — show the binary name
 *  and whether it was found in PATH instead of an API-key input. */
export function CliInfo({ provider }: { provider: Provider }) {
  return (
    <div className={styles.detailSection}>
      <div className={styles.detailSectionTitle}>
        <span>CLI Binary</span>
        <span className={styles.modelCountSummary}>
          {provider.configured ? "Found in PATH" : "Not found"}
        </span>
      </div>
      <p style={{ color: "var(--text-muted)", fontSize: 13 }}>
        This provider wraps the <code>{provider.cli_binary}</code> CLI. Install it
        and run its own login command; enable the toggle above to use it here.
      </p>
    </div>
  );
}
