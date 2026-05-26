"use client";

/**
 * Default RuntimeBlock return-renderer.
 *
 * Mirrors the original behaviour: try to distill a one-line summary
 * from a structured agentic JSON (``summary`` / ``output`` / ``result``
 * / ``message`` / ``text``), then render through the shared markdown
 * pipeline. Non-JSON output is passed through verbatim.
 */
import { renderMarkdown } from "../markdown";
import type { RuntimeRendererProps } from "./types";

export function distillReturn(raw: string): string {
  if (!raw) return raw;
  const s = raw.trim();
  if (!s.startsWith("{") && !s.startsWith("[")) return raw;
  try {
    const obj = JSON.parse(s);
    if (obj && typeof obj === "object" && !Array.isArray(obj)) {
      const o = obj as Record<string, unknown>;
      for (const k of ["summary", "output", "result", "message", "text"]) {
        const v = o[k];
        if (typeof v === "string" && v.trim()) return v;
      }
      const pieces: string[] = [];
      if (typeof o.success === "boolean") {
        pieces.push(o.success ? "success" : "failed");
      }
      if (typeof o.issues === "string" && o.issues) pieces.push(o.issues);
      if (typeof o.steps_taken === "number") {
        pieces.push(`${o.steps_taken} steps`);
      }
      if (pieces.length) return pieces.join(" · ");
    }
  } catch {
    /* not JSON — fall through */
  }
  return raw;
}

export function DefaultRenderer({ rawOutput }: RuntimeRendererProps) {
  const content = distillReturn(rawOutput);
  return (
    <div
      className="runtime-output"
      dangerouslySetInnerHTML={{ __html: renderMarkdown(content) }}
    />
  );
}
