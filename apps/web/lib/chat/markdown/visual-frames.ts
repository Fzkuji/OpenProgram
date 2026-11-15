/**
 * Mounting ```visual blocks (visual.ts) as sandboxed views.
 *
 * Each mount point gets an iframe of the server's /visual-frame page with
 * `sandbox="allow-scripts"` and no `allow-same-origin`: the view's origin
 * is opaque, so it cannot reach this page, its cookies or its storage, and
 * the page's own policy limits it to the public CDNs. The two sides talk
 * only through postMessage:
 *
 * - App to view: the block's source and the theme's colours (`render`),
 *   and new colours whenever the theme changes (`theme`).
 * - View to App: its height (`size`), a link to open (`open-link`), and
 *   text the user chose to send as their next message (`prompt`).
 *
 * A message is trusted only from an iframe this module mounted, matched by
 * its window, and a prompt is sent to the conversation the reply is in.
 */
import { translateText } from "@/lib/i18n";
import { sendFromTranscript } from "@/lib/chat/send/send-queue";

const FRAME_PATH = "/visual-frame";
const MAX_HEIGHT = 4000;

/** The theme tokens a view may use, under the names the model is told. */
const TOKENS: Array<[string, string]> = [
  ["--bg-primary", "--bg-primary"],
  ["--bg-secondary", "--bg-secondary"],
  ["--bg-tertiary", "--bg-tertiary"],
  ["--bg-hover", "--bg-hover"],
  ["--bg-input", "--bg-input"],
  ["--border", "--border"],
  ["--border-light", "--border-light"],
  ["--text-primary", "--text-primary"],
  ["--text-secondary", "--text-secondary"],
  ["--text-muted", "--text-muted"],
  ["--text-bright", "--text-bright"],
  ["--accent", "--accent-fill"],
  ["--accent-green", "--accent-green"],
  ["--accent-red", "--accent-red"],
  ["--accent-yellow", "--accent-yellow"],
  ["--accent-purple", "--accent-purple"],
  ["--accent-cyan", "--accent-cyan"],
  ["--font-sans", "--font-sans"],
  ["--font-mono", "--font-mono"],
];

type Frame = { iframe: HTMLIFrameElement; source: string; ready: boolean };
const frames = new Map<Window, Frame>();

function themeScheme(): "light" | "dark" {
  return getComputedStyle(document.documentElement).colorScheme.includes("dark") ? "dark" : "light";
}

/** The current theme as a `:root { … }` block for the view. */
export function themeCss(): string {
  const style = getComputedStyle(document.documentElement);
  const body = TOKENS
    .map(([name, token]) => [name, style.getPropertyValue(token).trim()] as const)
    .filter(([, value]) => value && !/[<>{}]/.test(value))
    .map(([name, value]) => `${name}:${value};`)
    .join("");
  return `:root{${body}}`;
}

function post(frame: Frame, message: Record<string, unknown>): void {
  // The view's origin is opaque, so no target origin can name it.
  frame.iframe.contentWindow?.postMessage({ opVisual: 1, ...message }, "*");
}

function onMessage(event: MessageEvent): void {
  const data = event.data as { opVisual?: number; type?: string; height?: number; url?: string; text?: string } | null;
  if (!data || data.opVisual !== 1 || !event.source) return;
  const frame = frames.get(event.source as Window);
  if (!frame) return;
  if (!frame.iframe.isConnected) {
    frames.delete(event.source as Window);
    return;
  }
  switch (data.type) {
    case "ready":
      if (frame.ready) return;
      frame.ready = true;
      post(frame, { type: "render", html: frame.source, css: themeCss(), scheme: themeScheme() });
      return;
    case "size": {
      const height = Math.max(0, Math.min(MAX_HEIGHT, Math.round(Number(data.height) || 0)));
      frame.iframe.style.height = `${height}px`;
      return;
    }
    case "rendered":
      frame.iframe.closest(".op-visual")?.setAttribute("data-visual", "shown");
      return;
    case "open-link": {
      const url = String(data.url || "");
      if (/^https?:\/\//i.test(url)) window.open(url, "_blank", "noopener,noreferrer");
      return;
    }
    case "prompt": {
      const text = typeof data.text === "string" ? data.text.trim() : "";
      const sessionId = frame.iframe.closest<HTMLElement>("[data-session-id]")?.dataset.sessionId;
      if (text && sessionId) sendFromTranscript(sessionId, text.slice(0, 4000));
      return;
    }
    case "error":
      frame.iframe.closest(".op-visual")?.setAttribute("data-visual-error", "1");
      return;
  }
}

function broadcastTheme(): void {
  const css = themeCss();
  const scheme = themeScheme();
  for (const [win, frame] of frames) {
    if (!frame.iframe.isConnected) frames.delete(win);
    else if (frame.ready) post(frame, { type: "theme", css, scheme });
  }
}

let installed = false;
function install(): void {
  if (installed) return;
  installed = true;
  window.addEventListener("message", onMessage);
  new MutationObserver(broadcastTheme).observe(document.documentElement, {
    attributes: true,
    attributeFilter: ["data-theme", "data-theme-mode", "data-custom-css", "class", "style"],
  });
  window.matchMedia?.("(prefers-color-scheme: dark)").addEventListener?.("change", broadcastTheme);
}

/** Mount every ```visual block under `root` that has no view yet. */
export function mountVisualsIn(root: ParentNode | null | undefined): void {
  if (!root || typeof document === "undefined") return;
  const points = root.querySelectorAll<HTMLElement>('.op-visual[data-visual="pending"]');
  if (!points.length) return;
  install();
  points.forEach((point) => {
    const source = point.querySelector(".op-visual-source")?.textContent ?? "";
    point.dataset.visual = "loading";
    const iframe = document.createElement("iframe");
    iframe.className = "op-visual-frame";
    iframe.setAttribute("sandbox", "allow-scripts");
    iframe.setAttribute("referrerpolicy", "no-referrer");
    iframe.setAttribute("title", translateText("Visual", "图示"));
    iframe.src = FRAME_PATH;
    point.append(iframe);
    if (iframe.contentWindow) frames.set(iframe.contentWindow, { iframe, source, ready: false });
  });
}
