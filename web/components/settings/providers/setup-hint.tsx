"use client";

import styles from "../settings-page.module.css";
import type { Provider } from "./types";

/** Provider-specific setup blurb rendered in the detail pane.
 *  Tiny markdown subset:
 *    - backticked spans → inline <code>;
 *    - lines starting with "$ " render as a command row (own block);
 *    - consecutive non-command, non-empty lines collapse into ONE
 *      paragraph that reflows at the container width;
 *    - blank lines separate paragraphs.
 *  The "collapse" rule matters: hint strings in
 *  ``openprogram/webui/_model_catalog.py`` are hard-wrapped to ~72
 *  chars for Python source readability. Without collapse every
 *  source-line ends up as its own <div>, producing the ragged column
 *  the user complained about. */
export function SetupHint({ hint, configured }: { hint: string; configured: boolean }) {
  type Block = { kind: "para"; text: string } | { kind: "cmd"; text: string };
  const blocks: Block[] = [];
  let buf: string[] = [];
  const flushPara = () => {
    if (buf.length) {
      blocks.push({ kind: "para", text: buf.join(" ") });
      buf = [];
    }
  };
  for (const raw of hint.split("\n")) {
    const line = raw.trimEnd();
    if (line.startsWith("$ ")) {
      flushPara();
      blocks.push({ kind: "cmd", text: line.slice(2) });
    } else if (!line.trim()) {
      flushPara();
    } else {
      buf.push(line.trim());
    }
  }
  flushPara();

  const renderInline = (text: string) =>
    text.split(/(`[^`]+`|\*\*[^*]+\*\*)/g).map((seg, j) => {
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
      if (seg.startsWith("**") && seg.endsWith("**")) {
        return <strong key={j}>{seg.slice(2, -2)}</strong>;
      }
      return <span key={j}>{seg}</span>;
    });

  return (
    <div className={styles.detailSection}>
      <div className={styles.detailSectionTitle}>
        <span>Setup</span>
        <span className={styles.modelCountSummary}>
          {configured ? "Detected" : "Not running"}
        </span>
      </div>
      <div style={{ color: "var(--text-muted)", fontSize: 13, lineHeight: 1.55 }}>
        {blocks.map((b, i) =>
          b.kind === "cmd" ? (
            <pre
              key={i}
              style={{
                margin: "8px 0",
                padding: "6px 10px",
                background: "var(--bg-secondary)",
                border: "1px solid var(--border)",
                borderRadius: 6,
                fontFamily: "var(--font-mono)",
                fontSize: 12,
                overflow: "auto",
              }}
            >
              {b.text}
            </pre>
          ) : (
            <p key={i} style={{ margin: "8px 0" }}>
              {renderInline(b.text)}
            </p>
          ),
        )}
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
