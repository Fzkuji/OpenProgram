export const SHOW_BOOKMARKS_BAR_KEY = "openprogram.browser.show-bookmarks-bar";
export const BROWSER_IMPORT_PROMPT_FINISHED_KEY = "openprogram.browser.import-prompt-finished";
const BROWSER_IMPORT_REQUEST_KEY = "openprogram.browser.import-request";
const BROWSER_PREFS_CHANGE_EVENT = "openprogram:browser-prefs-changed";

export function showBookmarksBar(): boolean {
  return typeof window !== "undefined"
    && localStorage.getItem(SHOW_BOOKMARKS_BAR_KEY) === "1";
}

export function setShowBookmarksBar(visible: boolean): void {
  if (typeof window === "undefined") return;
  localStorage.setItem(SHOW_BOOKMARKS_BAR_KEY, visible ? "1" : "0");
  window.dispatchEvent(new Event(BROWSER_PREFS_CHANGE_EVENT));
}

export function subscribeBrowserPrefs(listener: () => void): () => void {
  if (typeof window === "undefined") return () => {};
  const onStorage = (event: StorageEvent) => {
    if (event.key === SHOW_BOOKMARKS_BAR_KEY) listener();
  };
  window.addEventListener(BROWSER_PREFS_CHANGE_EVENT, listener);
  window.addEventListener("storage", onStorage);
  return () => {
    window.removeEventListener(BROWSER_PREFS_CHANGE_EVENT, listener);
    window.removeEventListener("storage", onStorage);
  };
}

export function browserImportPromptFinished(): boolean {
  return typeof window !== "undefined"
    && localStorage.getItem(BROWSER_IMPORT_PROMPT_FINISHED_KEY) === "1";
}

export function markBrowserImportPromptFinished(): void {
  if (typeof window === "undefined") return;
  localStorage.setItem(BROWSER_IMPORT_PROMPT_FINISHED_KEY, "1");
}

/** Manual import remains available after the automatic prompt is finished. */
export function requestBrowserImport(): void {
  if (typeof window === "undefined") return;
  sessionStorage.setItem(BROWSER_IMPORT_REQUEST_KEY, "1");
}

export function consumeBrowserImportRequest(): boolean {
  if (typeof window === "undefined") return false;
  const requested = sessionStorage.getItem(BROWSER_IMPORT_REQUEST_KEY) === "1";
  sessionStorage.removeItem(BROWSER_IMPORT_REQUEST_KEY);
  return requested;
}
