// The worker names the Web export it serves on every WebSocket connect and
// whenever a hot self-update replaces it
// (openprogram/interfaces/server/frontend.py). Once it differs from
// the export this page loaded, the hashed chunks this page would load next
// are gone, so reload onto the new export — but never while something typed
// would be lost.

const RETRY_MS = 2000;
let loadedBuild: string | undefined;
let pending = false;

function holdsUnsavedText(): boolean {
  for (const node of Array.from(document.querySelectorAll("textarea, input, [contenteditable]"))) {
    const element = node as HTMLElement;
    if (element instanceof HTMLTextAreaElement && element.value.trim()) return true;
    if (
      element instanceof HTMLInputElement &&
      element === document.activeElement &&
      !["button", "checkbox", "radio", "range", "submit"].includes(element.type) &&
      element.value.trim()
    ) {
      return true;
    }
    if (element.isContentEditable && element.textContent?.trim()) return true;
  }
  return false;
}

export function noteFrontendBuild(id: unknown): void {
  if (typeof id !== "string" || !id) return;
  if (loadedBuild === undefined) {
    loadedBuild = id;
    return;
  }
  if (id !== loadedBuild) reloadForNewFrontend();
}

function reloadForNewFrontend(): void {
  if (pending) return;
  pending = true;
  const attempt = () => {
    if (holdsUnsavedText()) {
      window.setTimeout(attempt, RETRY_MS);
      return;
    }
    window.location.reload();
  };
  attempt();
}
