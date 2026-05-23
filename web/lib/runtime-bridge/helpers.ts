/**
 * Shared helper utilities — TS port of `public/js/shared/helpers.js`.
 *
 * Escaping, markdown, scroll-to-bottom, usage formatting, etc. Bridged
 * onto `window.*` for the still-legacy ui.js / history-graph.js.
 * Imported for side effects by AppShell.
 */

interface HelpersWindow {
  marked?: { parse(src: string, opts?: { breaks?: boolean }): string };
  renderMathInElement?: (el: HTMLElement, opts: unknown) => void;
  __stickToBottom?: () => boolean;
  __stickListenerInstalled?: boolean;
  __sessionStore?: { getState: () => { setWelcomeVisible: (v: boolean) => void } };
  _agentSettings?: { exec?: { provider?: string } };
  [k: string]: unknown;
}

const W = window as unknown as HelpersWindow;

export function escHtml(s: unknown): string {
  if (typeof s !== "string") s = String(s ?? "");
  const div = document.createElement("div");
  div.textContent = s as string;
  return div.innerHTML;
}

export function escAttr(s: unknown): string {
  if (typeof s !== "string") s = String(s ?? "");
  return (s as string)
    .replace(/&/g, "&amp;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;");
}

export function truncate(s: string, len: number): string {
  if (!s) return "";
  return s.length > len ? s.slice(0, len - 3) + "..." : s;
}

export function renderMd(s: unknown): string {
  if (typeof s !== "string") s = String(s ?? "");
  let str = s as string;
  if (W.marked) {
    const mathBlocks: string[] = [];
    const stash = (m: string): string => {
      mathBlocks.push(m);
      return "%%MATH" + (mathBlocks.length - 1) + "%%";
    };
    str = str.replace(/\$\$([\s\S]*?)\$\$/g, stash);
    str = str.replace(/\\\[([\s\S]*?)\\\]/g, stash);
    str = str.replace(/\\\(([\s\S]*?)\\\)/g, stash);
    str = str.replace(/\$([^$\n]+?)\$/g, stash);
    let html = W.marked.parse(str, { breaks: true });
    for (let i = 0; i < mathBlocks.length; i++) {
      html = html.replace("%%MATH" + i + "%%", mathBlocks[i]);
    }
    return '<span class="md-rendered">' + html + "</span>";
  }
  return "<pre>" + escHtml(str) + "</pre>";
}

export function renderMathInChat(): void {
  if (typeof W.renderMathInElement !== "function") return;
  document.querySelectorAll<HTMLElement>(".md-rendered").forEach((el) => {
    if (el.dataset.mathRendered) return;
    W.renderMathInElement!(el, {
      delimiters: [
        { left: "$$", right: "$$", display: true },
        { left: "$", right: "$", display: false },
        { left: "\\[", right: "\\]", display: true },
        { left: "\\(", right: "\\)", display: false },
      ],
      throwOnError: false,
    });
    el.dataset.mathRendered = "1";
  });
}

let stickToBottom = true;

function setupStickToBottomListener(): void {
  if (W.__stickListenerInstalled) return;
  const area = document.getElementById("chatArea");
  if (!area) return;
  area.addEventListener(
    "scroll",
    () => {
      const distFromBottom =
        area.scrollHeight - area.scrollTop - area.clientHeight;
      stickToBottom = distFromBottom < 60;
    },
    { passive: true },
  );
  W.__stickListenerInstalled = true;
}

export function scrollToBottom(opts?: { force?: boolean }): void {
  renderMathInChat();
  const area = document.getElementById("chatArea");
  const messages = document.getElementById("chatMessages");
  if (!area || !messages) return;
  setupStickToBottomListener();
  const force = !!(opts && opts.force);
  if (!force && !stickToBottom) return;
  requestAnimationFrame(() => {
    const bubbles = messages.querySelectorAll<HTMLElement>(".message");
    const last = bubbles.length ? bubbles[bubbles.length - 1] : null;
    if (!last) {
      area.scrollTop = area.scrollHeight;
      return;
    }
    const areaRect = area.getBoundingClientRect();
    const msgRect = last.getBoundingClientRect();
    const delta = msgRect.bottom - areaRect.bottom + 16;
    if (delta > 0) area.scrollTop += delta;
    if (area.scrollTop > area.scrollHeight - area.clientHeight) {
      area.scrollTop = area.scrollHeight - area.clientHeight;
    }
  });
}

// React <MessageList /> owns the message stream — legacy bubble
// builders' DOM nodes must not enter #chatMessages. No-op chokepoint.
export function appendToChat(): void {}

export function autoResize(el: HTMLTextAreaElement): void {
  el.style.height = "auto";
  el.style.height = Math.min(el.scrollHeight, 200) + "px";
}

export function setWelcomeVisible(show: boolean): void {
  W.__sessionStore?.getState().setWelcomeVisible(!!show);
  const cm = document.getElementById("chatMessages");
  const ca = document.getElementById("chatArea");
  if (cm) cm.style.paddingBottom = show ? "150px" : "";
  if (ca) ca.classList.toggle("welcome-visible", !!show);
}

export function addSystemMessage(text: string): void {
  const container = document.getElementById("chatMessages");
  if (!container) return;
  const welcome = document.getElementById("welcomeScreen") as HTMLElement | null;
  if (welcome && welcome.style.display !== "none") return;
  const div = document.createElement("div");
  div.className = "system-message";
  div.textContent = text;
  appendToChat();
  scrollToBottom();
}

export function parseRunCommandForDisplay(text: string): {
  funcName: string;
  params: string;
} {
  const t = text.trim();
  const match = t.match(/^(?:run\s+)(\S+)\s*(.*)/i);
  if (match) return { funcName: match[1], params: match[2] || "" };
  const match2 = t.match(/^(create|fix)\s+(.*)/i);
  if (match2) return { funcName: match2[1], params: match2[2] || "" };
  return { funcName: t, params: "" };
}

export function fmtTokenNum(n: number): string | number {
  if (n >= 1000000) return (n / 1000000).toFixed(1) + "m";
  if (n >= 1000) return (n / 1000).toFixed(1) + "k";
  return n;
}

interface Usage {
  input_tokens?: number;
  output_tokens?: number;
  cache_read?: number;
  cache_create?: number;
}

type UsageText = "" | { text: string; tooltip: string };

function buildUsageText(usage: Usage | null | undefined): UsageText {
  if (
    !usage ||
    (!usage.input_tokens &&
      !usage.output_tokens &&
      !usage.cache_read &&
      !usage.cache_create)
  ) {
    return "";
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

export function formatUsageBadge(usage: Usage): string {
  const result = buildUsageText(usage);
  if (!result) return "";
  const tip = result.tooltip
    ? ' title="' + escAttr(result.tooltip) + '"'
    : "";
  return (
    '<span style="font-size:10px;color:var(--text-muted);font-family:var(--font-mono);margin-left:auto;padding-left:8px"' +
    tip +
    ">" +
    escHtml(result.text) +
    "</span>"
  );
}

export function formatUsageFooterLabel(usage: Usage): string {
  const result = buildUsageText(usage);
  if (!result) return "";
  const tip = result.tooltip
    ? ' title="' + escAttr(result.tooltip) + '"'
    : "";
  return '<span class="usage-footer-label"' + tip + ">" + escHtml(result.text) + "</span>";
}

export function formatProviderLabel(info: {
  provider?: string;
  type?: string;
  model?: string;
}): string {
  if (!info || !info.provider) return "No provider";
  const parts = [info.provider];
  if (info.type) parts.push(info.type);
  if (info.model) parts.push(info.model);
  return parts.join(" · ");
}

interface ProgramOutput {
  final_state?: unknown;
  output?: unknown;
  reasoning?: unknown;
  history?: { output?: unknown; reasoning?: unknown }[];
  action?: unknown;
  target?: unknown;
}

export function formatProgramResultContent(output: unknown): string {
  if (output == null) return "";
  if (typeof output === "string") return output;
  if (typeof output !== "object") return String(output);
  const o = output as ProgramOutput;
  if (typeof o.final_state === "string" && o.final_state.trim()) {
    return o.final_state;
  }
  if (typeof o.output === "string" && o.output.trim()) return o.output;
  if (typeof o.reasoning === "string" && o.reasoning.trim()) return o.reasoning;
  if (Array.isArray(o.history) && o.history.length > 0) {
    const last = o.history[o.history.length - 1] || {};
    if (typeof last.output === "string" && last.output.trim()) return last.output;
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
      hl = hl.replace(/^(\s*)(@\w+)/gm, '$1<span class="syn-decorator">$2</span>');
      hl = hl.replace(/\b(\d+\.?\d*)\b/g, '<span class="syn-number">$1</span>');
      hl = hl.replace(/\b(self)\b/g, '<span class="syn-self">$1</span>');
      hl = hl.replace(/\x00TOK(\d+)\x00/g, (_, idx) => tokens[Number(idx)]);
      return num + hl;
    })
    .join("\n");
}

/* ===== window bridges ============================================ */

W.escHtml = escHtml;
W.escAttr = escAttr;
W.truncate = truncate;
W.renderMd = renderMd;
W.renderMathInChat = renderMathInChat;
W.scrollToBottom = scrollToBottom;
W.appendToChat = appendToChat;
W.autoResize = autoResize;
W.setWelcomeVisible = setWelcomeVisible;
W.addSystemMessage = addSystemMessage;
W.parseRunCommandForDisplay = parseRunCommandForDisplay;
W.fmtTokenNum = fmtTokenNum;
W._buildUsageText = buildUsageText;
W.formatUsageBadge = formatUsageBadge;
W.formatUsageFooterLabel = formatUsageFooterLabel;
W.formatProviderLabel = formatProviderLabel;
W.formatProgramResultContent = formatProgramResultContent;
W.highlightPython = highlightPython;
W.__stickToBottom = () => stickToBottom;
