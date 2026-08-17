/**
 * CDN libraries loaded once per page: marked (chat markdown) and KaTeX
 * (math rendering). Both publish themselves as `window.marked` /
 * `window.renderMathInElement` — see `lib/cdn-globals.d.ts`.
 *
 * `AppShell` kicks this off on mount; anything that needs the globals
 * awaits `externalLibsReady()` instead of polling for a window flag.
 */

const KATEX_CSS =
  "https://cdnjs.cloudflare.com/ajax/libs/KaTeX/0.16.9/katex.min.css";

const EXTERNAL_LIBS = [
  "https://cdnjs.cloudflare.com/ajax/libs/marked/12.0.2/marked.min.js",
  "https://cdnjs.cloudflare.com/ajax/libs/KaTeX/0.16.9/katex.min.js",
  "https://cdnjs.cloudflare.com/ajax/libs/KaTeX/0.16.9/contrib/auto-render.min.js",
];

function loadStylesheet(href: string): void {
  const existing = Array.from(document.styleSheets).find((s) => s.href === href);
  if (existing) return;
  const link = document.createElement("link");
  link.rel = "stylesheet";
  link.href = href;
  document.head.appendChild(link);
}

function loadExternalScript(src: string): Promise<void> {
  return new Promise((resolve, reject) => {
    const existing = Array.from(document.scripts).find(
      (s) => s.getAttribute("data-src") === src,
    );
    if (existing) {
      resolve();
      return;
    }
    const el = document.createElement("script");
    el.src = src;
    el.async = false;
    el.setAttribute("data-app-script", "1");
    el.setAttribute("data-src", src);
    el.onload = () => resolve();
    el.onerror = () => reject(new Error(`Failed to load ${src}`));
    document.head.appendChild(el);
  });
}

let readyPromise: Promise<void> | null = null;

/** Start loading (idempotent) and return the shared completion promise. */
export function loadExternalLibs(): Promise<void> {
  if (typeof window === "undefined") return Promise.resolve();
  if (!readyPromise) {
    loadStylesheet(KATEX_CSS);
    readyPromise = Promise.all(EXTERNAL_LIBS.map(loadExternalScript)).then(
      () => undefined,
    );
  }
  return readyPromise;
}

/** Resolves once the CDN libs are on the page. Safe before AppShell mounts. */
export function externalLibsReady(): Promise<void> {
  return loadExternalLibs();
}
