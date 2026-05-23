/**
 * Programs / favorites data + click glue.
 *
 * TS port of the legacy `public/js/shared/programs-panel.js`. Bridged
 * onto `window.*` for the React sidebar / welcome screen / programs
 * page and the WS `functions_list` handler.
 *
 * Imported for side effects by AppShell.
 */

interface ProgramsMeta {
  favorites: string[];
  folders: Record<string, unknown>;
}

interface FnDef {
  name: string;
  [k: string]: unknown;
}

interface PanelWindow {
  programsMeta?: ProgramsMeta;
  availableFunctions?: FnDef[];
  __navigate?: (path: string) => void;
  __pendingRunFunction?: { name: string; cat: string };
  __sessionStore?: {
    getState: () => {
      openFnForm: (fn: FnDef) => void;
      closeFnForm: () => void;
      fnFormFunction: unknown;
      setComposerInput: (t: string) => void;
      focusComposer: () => void;
    };
  };
  addAssistantMessage?: (text: string) => void;
  renderFunctions?: () => void;
  [k: string]: unknown;
}

const W = window as unknown as PanelWindow;

export async function loadProgramsMeta(): Promise<void> {
  try {
    const resp = await fetch("/api/programs/meta");
    W.programsMeta = (await resp.json()) || { favorites: [], folders: {} };
  } catch {
    W.programsMeta = { favorites: [], folders: {} };
  }
}

// React owns favorites rendering (components/sidebar/favorites-list.tsx).
// Kept as a no-op so the WS `functions_list` handler doesn't crash.
export function renderFunctions(): void {}

function storeState() {
  const s = W.__sessionStore;
  return s && typeof s.getState === "function" ? s.getState() : null;
}

export async function deleteFunction(name: string): Promise<void> {
  if (!confirm('Delete function "' + name + '"?')) return;
  try {
    const resp = await fetch("/api/function/" + encodeURIComponent(name), {
      method: "DELETE",
    });
    const data = await resp.json();
    if (data.deleted) {
      W.addAssistantMessage?.('Deleted function "' + name + '".');
      const fResp = await fetch("/api/functions");
      W.availableFunctions = await fResp.json();
      renderFunctions();
    } else {
      W.addAssistantMessage?.("Cannot delete: " + (data.error || "unknown error"));
    }
  } catch (e) {
    alert("Delete failed: " + (e as Error).message);
  }
}

export function fixFunction(name: string): void {
  const instruction = prompt("What should be fixed in " + name + "?");
  if (!instruction) return;
  setInput("fix " + name + " " + instruction);
}

export function clickFunction(name: string, category?: string): void {
  const fn = (W.availableFunctions || []).find((f) => f.name === name);
  if (!fn) return;
  const p = location.pathname;
  const onChat = p === "/chat" || p.indexOf("/s/") === 0;
  if (!onChat) {
    W.__pendingRunFunction = { name, cat: category || "" };
    W.__navigate?.("/chat");
    return;
  }
  storeState()?.openFnForm(fn);
}

export function clickFnExample(fnName: string): void {
  const fn = (W.availableFunctions || []).find((f) => f.name === fnName);
  if (!fn) return;
  storeState()?.openFnForm(fn);
}

export function setInput(text: string): void {
  const state = storeState();
  if (state) {
    if (state.fnFormFunction) state.closeFnForm();
    state.setComposerInput(text);
    state.focusComposer();
  }
}

/* ===== window bridges ============================================ */

W.loadProgramsMeta = loadProgramsMeta;
W.renderFunctions = renderFunctions;
W.deleteFunction = deleteFunction;
W.fixFunction = fixFunction;
W.clickFunction = clickFunction;
W.clickFnExample = clickFnExample;
W.setInput = setInput;
