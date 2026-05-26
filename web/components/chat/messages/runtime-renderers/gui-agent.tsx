"use client";

/**
 * gui_agent return-renderer.
 *
 * gui_agent returns a structured payload:
 *   { task, success, summary, history[...], total_time, steps_taken, issues? }
 *
 * The full ``history`` is already drawn in the Execution DAG below;
 * here we only surface the user-facing verdict:
 *   - success / failed badge
 *   - the ``summary`` text (markdown)
 *   - a meta line: "{steps_taken} steps · {total_time}s"
 *   - if ``issues`` is present, a red warning row
 */
import { renderMarkdown } from "../markdown";
import type { RuntimeRendererProps, RuntimePreview } from "./types";

interface GuiAgentReturn {
  task?: string;
  success?: boolean;
  summary?: string;
  total_time?: number;
  steps_taken?: number;
  issues?: string | null;
}

function parse(raw: string): GuiAgentReturn | null {
  if (!raw) return null;
  const s = raw.trim();
  if (!s.startsWith("{")) return null;
  try {
    const obj = JSON.parse(s);
    if (obj && typeof obj === "object" && !Array.isArray(obj)) {
      return obj as GuiAgentReturn;
    }
  } catch {
    /* not JSON */
  }
  return null;
}

function fmtTime(t: number | undefined): string | null {
  if (typeof t !== "number" || !Number.isFinite(t)) return null;
  if (t < 60) return `${t.toFixed(1)}s`;
  const m = Math.floor(t / 60);
  const s = Math.round(t - m * 60);
  return `${m}m${s}s`;
}

export function GuiAgentRenderer({ rawOutput }: RuntimeRendererProps) {
  const obj = parse(rawOutput);
  if (!obj) {
    // Fall back to raw markdown when the payload isn't the expected
    // JSON shape (e.g. an early error before the agent produced its
    // structured return).
    return (
      <div
        className="runtime-output"
        dangerouslySetInnerHTML={{ __html: renderMarkdown(rawOutput) }}
      />
    );
  }

  const badgeOk = obj.success === true;
  const badgeBad = obj.success === false;
  const meta: string[] = [];
  if (typeof obj.steps_taken === "number") meta.push(`${obj.steps_taken} steps`);
  const t = fmtTime(obj.total_time);
  if (t) meta.push(t);

  return (
    <div className="runtime-output runtime-gui-agent" style={{ lineHeight: 1.45 }}>
      <div
        style={{
          display: "flex",
          alignItems: "center",
          gap: 8,
          flexWrap: "wrap",
          marginBottom: obj.summary ? 4 : 0,
        }}
      >
        {badgeOk ? (
          <span
            style={{
              fontSize: 10,
              padding: "1px 6px",
              borderRadius: 8,
              background: "rgba(34,197,94,0.15)",
              color: "#16a34a",
              fontWeight: 600,
              border: "1px solid rgba(34,197,94,0.35)",
              lineHeight: 1.4,
            }}
          >
            success
          </span>
        ) : null}
        {badgeBad ? (
          <span
            style={{
              fontSize: 10,
              padding: "1px 6px",
              borderRadius: 8,
              background: "rgba(239,68,68,0.15)",
              color: "#dc2626",
              fontWeight: 600,
              border: "1px solid rgba(239,68,68,0.35)",
              lineHeight: 1.4,
            }}
          >
            failed
          </span>
        ) : null}
        {meta.length ? (
          <span style={{ fontSize: 11, color: "var(--muted, #888)" }}>
            {meta.join(" · ")}
          </span>
        ) : null}
      </div>
      {obj.summary ? (
        <div
          style={{ fontSize: 13 }}
          dangerouslySetInnerHTML={{ __html: renderMarkdown(obj.summary) }}
        />
      ) : null}
      {obj.issues ? (
        <div
          style={{
            marginTop: 4,
            padding: "4px 8px",
            background: "rgba(239,68,68,0.08)",
            border: "1px solid rgba(239,68,68,0.25)",
            borderRadius: 5,
            color: "#b91c1c",
            fontSize: 12,
            lineHeight: 1.4,
          }}
        >
          <strong>issues: </strong>
          {obj.issues}
        </div>
      ) : null}
    </div>
  );
}

export const guiAgentPreview: RuntimePreview = ({ rawOutput }) => {
  const obj = parse(rawOutput);
  if (!obj) return null;
  const parts: string[] = [];
  if (obj.success === true) parts.push("success");
  else if (obj.success === false) parts.push("failed");
  if (typeof obj.steps_taken === "number") parts.push(`${obj.steps_taken} steps`);
  const t = fmtTime(obj.total_time);
  if (t) parts.push(t);
  if (parts.length === 0) return null;
  return parts.join(" · ");
};
