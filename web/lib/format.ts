// Pure formatting helpers. Mirrors web/public/js/shared/helpers.js but
// strictly typed, no DOM side effects, no implicit globals. The legacy
// helpers.js still ships for code that hasn't migrated yet — this file
// is the React-side import surface.

/** HTML-escape arbitrary text for safe interpolation into innerHTML. */
export function escHtml(s: unknown): string {
  const str = typeof s === "string" ? s : String(s ?? "");
  // Use a detached element so the browser's text-to-HTML serializer
  // does the escaping (matches legacy behavior exactly).
  if (typeof document !== "undefined") {
    const div = document.createElement("div");
    div.textContent = str;
    return div.innerHTML;
  }
  // SSR fallback: do the standard 5-char escape manually.
  return str
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

/** Escape a string for use inside an HTML attribute value. */
export function escAttr(s: unknown): string {
  const str = typeof s === "string" ? s : String(s ?? "");
  return str
    .replace(/&/g, "&amp;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;");
}

/** Truncate to `len` chars, appending `...` when cut. */
export function truncate(s: string | null | undefined, len: number): string {
  if (!s) return "";
  return s.length > len ? s.slice(0, len - 3) + "..." : s;
}

/** Compact token count → "1.2k" / "3.4m" / raw integer string. */
export function fmtTokenNum(n: number): string {
  if (n >= 1_000_000) return (n / 1_000_000).toFixed(1) + "m";
  if (n >= 1_000) return (n / 1_000).toFixed(1) + "k";
  return String(n);
}

/** Parse a `/run foo bar` or `create/fix ...` user-typed command for the
 * "function call" display row. Returns the func name and any trailing
 * params (joined with a single space). */
export function parseRunCommandForDisplay(text: string): {
  funcName: string;
  params: string;
} {
  const t = text.trim();
  const m1 = t.match(/^(?:run\s+)(\S+)\s*(.*)/i);
  if (m1) return { funcName: m1[1], params: m1[2] || "" };
  const m2 = t.match(/^(create|fix)\s+(.*)/i);
  if (m2) return { funcName: m2[1], params: m2[2] || "" };
  return { funcName: t, params: "" };
}

export interface ProviderInfo {
  provider?: string | null;
  model?: string | null;
  type?: string | null;
}

/** Format `{provider, type, model}` as `"provider · type · model"`. */
export function formatProviderLabel(info: ProviderInfo | null | undefined): string {
  if (!info || !info.provider) return "No provider";
  const parts: string[] = [info.provider];
  if (info.type) parts.push(info.type);
  if (info.model) parts.push(info.model);
  return parts.join(" · ");
}

/** Best-effort extraction of a human-readable string from a program's
 * structured output (dict / array / final_state / etc). Falls back to
 * pretty-printed JSON. */
export function formatProgramResultContent(output: unknown): string {
  if (output == null) return "";
  if (typeof output === "string") return output;
  if (typeof output !== "object") return String(output);

  const o = output as Record<string, unknown>;

  if (typeof o.final_state === "string" && o.final_state.trim()) {
    return o.final_state;
  }
  if (typeof o.output === "string" && o.output.trim()) {
    return o.output;
  }
  if (typeof o.reasoning === "string" && o.reasoning.trim()) {
    return o.reasoning;
  }
  if (Array.isArray(o.history) && o.history.length > 0) {
    const last = (o.history[o.history.length - 1] || {}) as Record<string, unknown>;
    if (typeof last.output === "string" && last.output.trim()) {
      return last.output;
    }
    if (typeof last.reasoning === "string" && last.reasoning.trim()) {
      return last.reasoning;
    }
  }
  if (typeof o.action === "string" && o.action) {
    let summary = o.action;
    if (typeof o.target === "string" && o.target.trim()) {
      summary += ": " + o.target.trim();
    }
    return summary;
  }

  try {
    return JSON.stringify(output, null, 2);
  } catch {
    return String(output);
  }
}

// ===== Usage badge / footer =====

export interface Usage {
  input_tokens?: number;
  output_tokens?: number;
  cache_read?: number;
  cache_create?: number;
}

export interface UsageText {
  text: string;
  tooltip: string;
}

/** Build the compact "X in · Y out" string + tooltip from a usage record.
 * Provider is passed in explicitly (legacy version pulled from a global
 * `_agentSettings`). Returns `null` when usage is empty.
 *
 * Exported so React components (e.g. <ContextBadge>) can drive their own
 * rendering instead of injecting the HTML string from `formatUsageBadge`. */
export function buildUsageText(
  usage: Usage | null | undefined,
  // `provider` is accepted to match the legacy signature and to let
  // callers branch on provider-specific behavior in the future. The
  // current formatter doesn't actually need it — the backend already
  // normalizes `input_tokens` to "new" tokens for every provider.
  // eslint-disable-next-line @typescript-eslint/no-unused-vars
  _provider: string | null | undefined,
): UsageText | null {
  if (
    !usage ||
    (!usage.input_tokens &&
      !usage.output_tokens &&
      !usage.cache_read &&
      !usage.cache_create)
  ) {
    return null;
  }
  const base = usage.input_tokens || 0;
  const cached = usage.cache_read || 0;
  const cacheWrite = usage.cache_create || 0;
  const total = base + cached + cacheWrite;
  const outTok = usage.output_tokens || 0;

  const short = fmtTokenNum(total) + " in · " + fmtTokenNum(outTok) + " out";
  const detail: string[] = [];
  if (base > 0) detail.push(fmtTokenNum(base) + " base");
  if (cacheWrite > 0) detail.push(fmtTokenNum(cacheWrite) + " write");
  if (cached > 0) detail.push(fmtTokenNum(cached) + " hit");
  detail.push(fmtTokenNum(outTok) + " out");
  return { text: short, tooltip: detail.join(" · ") };
}

/** Render the small inline usage badge HTML used in chat headers. */
export function formatUsageBadge(
  usage: Usage | null | undefined,
  provider?: string | null,
): string {
  const result = buildUsageText(usage, provider);
  if (!result) return "";
  const tip = ' title="' + escAttr(result.tooltip) + '"';
  return (
    '<span style="font-size:10px;color:var(--text-muted);' +
    'font-family:var(--font-mono);margin-left:auto;padding-left:8px"' +
    tip +
    ">" +
    escHtml(result.text) +
    "</span>"
  );
}

/** Render the usage label HTML used in the message footer. */
export function formatUsageFooterLabel(
  usage: Usage | null | undefined,
  provider?: string | null,
): string {
  const result = buildUsageText(usage, provider);
  if (!result) return "";
  const tip = ' title="' + escAttr(result.tooltip) + '"';
  return (
    '<span class="usage-footer-label"' +
    tip +
    ">" +
    escHtml(result.text) +
    "</span>"
  );
}

/** Lightweight Python syntax highlighter used for inline code previews.
 * Keeps the same token classes the legacy CSS targets. */
export function highlightPython(code: string): string {
  const lines = code.split("\n");
  return lines
    .map((line, i) => {
      const num = '<span class="line-num">' + (i + 1) + "</span>";
      let hl = escHtml(line);
      const tokens: string[] = [];
      hl = hl.replace(
        /("""[\s\S]*?"""|'''[\s\S]*?'''|"[^"]*"|'[^']*'|#.*$)/gm,
        (m) => {
          const idx = tokens.length;
          const cls = m.startsWith("#") ? "syn-comment" : "syn-string";
          tokens.push('<span class="' + cls + '">' + m + "</span>");
          return "\x00TOK" + idx + "\x00";
        },
      );
      hl = hl.replace(
        /\b(from|import|def|class|return|if|else|elif|for|while|try|except|finally|with|as|raise|yield|pass|break|continue|and|or|not|in|is|lambda|True|False|None)\b/g,
        '<span class="syn-keyword">$1</span>',
      );
      hl = hl.replace(
        /^(\s*)(@\w+)/gm,
        '$1<span class="syn-decorator">$2</span>',
      );
      hl = hl.replace(/\b(\d+\.?\d*)\b/g, '<span class="syn-number">$1</span>');
      hl = hl.replace(/\b(self)\b/g, '<span class="syn-self">$1</span>');
      hl = hl.replace(/\x00TOK(\d+)\x00/g, (_, idx: string) => tokens[Number(idx)]);
      return num + hl;
    })
    .join("\n");
}
