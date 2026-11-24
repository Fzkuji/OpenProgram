/** The user's own browsers a ``computer_use`` browser call can name as ``app``, as the worker names them
 *  (openprogram/programs/applications/browser/catalog.py): the full name an approval asks about, and the
 *  short one a row or a summary shows. */
export interface UserBrowser {
  /** "Google Chrome", "Brave", "Safari". */
  label: string;
  /** "Chrome", "Brave", "Safari". */
  short: string;
}

export const USER_BROWSERS: Record<string, UserBrowser> = {
  chrome: { label: "Google Chrome", short: "Chrome" },
  "chrome-beta": { label: "Google Chrome Beta", short: "Chrome Beta" },
  "chrome-dev": { label: "Google Chrome Dev", short: "Chrome Dev" },
  "chrome-canary": { label: "Google Chrome Canary", short: "Chrome Canary" },
  edge: { label: "Microsoft Edge", short: "Edge" },
  "edge-beta": { label: "Microsoft Edge Beta", short: "Edge Beta" },
  "edge-dev": { label: "Microsoft Edge Dev", short: "Edge Dev" },
  "edge-canary": { label: "Microsoft Edge Canary", short: "Edge Canary" },
  brave: { label: "Brave", short: "Brave" },
  vivaldi: { label: "Vivaldi", short: "Vivaldi" },
  opera: { label: "Opera", short: "Opera" },
  arc: { label: "Arc", short: "Arc" },
  chromium: { label: "Chromium", short: "Chromium" },
  safari: { label: "Safari", short: "Safari" },
  firefox: { label: "Firefox", short: "Firefox" },
};

/** The names a model writes for a browser, by the one ``app`` it means (registry.py's ``_BROWSERS``). */
const ALIASES: Record<string, string> = {
  "google chrome": "chrome", "google-chrome": "chrome", "com.google.chrome": "chrome",
  "google chrome beta": "chrome-beta", "google chrome dev": "chrome-dev", "google chrome canary": "chrome-canary",
  msedge: "edge", "microsoft edge": "edge", "microsoft-edge": "edge", "com.microsoft.edgemac": "edge",
  "msedge-beta": "edge-beta", "microsoft edge beta": "edge-beta", "msedge-dev": "edge-dev",
  "microsoft edge dev": "edge-dev", "msedge-canary": "edge-canary", "microsoft edge canary": "edge-canary",
  "brave browser": "brave", "brave-browser": "brave", "com.brave.browser": "brave",
  "com.vivaldi.vivaldi": "vivaldi", "opera browser": "opera", "com.operasoftware.opera": "opera",
  "arc browser": "arc", "company.thebrowser.browser": "arc", "chromium-browser": "chromium",
  "apple safari": "safari", "com.apple.safari": "safari",
  "mozilla firefox": "firefox", "org.mozilla.firefox": "firefox",
};

/** The browser an ``app`` names, or "" for OpenProgram's own and for a name of no browser. */
export function browserOf(app: string): string {
  const named = app.trim().toLowerCase().replace(/\s+/g, " ");
  if (USER_BROWSERS[named]) return named;
  if (ALIASES[named]) return ALIASES[named];
  const joined = named.replace(/[_ ]/g, "-");
  return USER_BROWSERS[joined] ? joined : ALIASES[named.replace(/_/g, " ")] ?? "";
}
