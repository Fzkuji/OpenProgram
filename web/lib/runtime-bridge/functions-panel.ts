/**
 * Functions / favorites data + click glue.
 *
 * Consumers (React sidebar / welcome screen / functions page, the WS
 * `functions_list` handler) import these exports directly.
 *
 * Imported for side effects by AppShell.
 */

import { useSessionStore } from "@/lib/session-store";
import { addAssistantMessage } from "./chat-handlers";
import { runtimeState } from "./state";
import { navigate } from "@/lib/navigate";
import { setPendingRunFunction } from "@/lib/use-pending-run-function";

interface FnDef {
  name: string;
  [k: string]: unknown;
}

export async function loadProgramsMeta(): Promise<void> {
  try {
    const resp = await fetch("/api/programs/meta");
    const meta = (await resp.json()) || { favorites: [], folders: {} };
    runtimeState.programsMeta = meta;
    const { useFunctions } = await import("@/lib/state/functions-store");
    useFunctions.getState().setMeta(meta);
  } catch {
    runtimeState.programsMeta = { favorites: [], folders: {} };
  }
}

// React owns favorites rendering (components/sidebar/favorites-list.tsx).
// Kept as a no-op so the WS `functions_list` handler doesn't crash.
export function renderFunctions(): void {}

export async function deleteFunction(name: string): Promise<void> {
  if (!confirm('Delete function "' + name + '"?')) return;
  try {
    const resp = await fetch("/api/function/" + encodeURIComponent(name), {
      method: "DELETE",
    });
    const data = await resp.json();
    if (data.deleted) {
      addAssistantMessage('Deleted function "' + name + '".');
      const fResp = await fetch("/api/functions");
      runtimeState.availableFunctions = await fResp.json();
      renderFunctions();
    } else {
      addAssistantMessage("Cannot delete: " + (data.error || "unknown error"));
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

function findFunction(name: string): FnDef | undefined {
  return (runtimeState.availableFunctions as FnDef[]).find(
    (f) => f && f.name === name,
  );
}

export function clickFunction(name: string, category?: string): void {
  const fn = findFunction(name);
  if (!fn) return;
  const p = location.pathname;
  const onChat = p === "/chat" || p.indexOf("/s/") === 0;
  if (!onChat) {
    setPendingRunFunction({ name, cat: category || "" });
    navigate("/chat");
    return;
  }
  useSessionStore.getState().openFnForm(fn as never);
}

export function clickFnExample(fnName: string): void {
  const fn = findFunction(fnName);
  if (!fn) return;
  useSessionStore.getState().openFnForm(fn as never);
}

export function setInput(text: string): void {
  const state = useSessionStore.getState();
  if (state.fnFormFunction) state.closeFnForm();
  state.setComposerInput(text);
  state.focusComposer();
}

/* No function bridges: `use-ws.ts` imports `loadProgramsMeta` /
   `renderFunctions` directly, and this module generates no inline
   `onclick=` markup. `W` above is only the two data stashes. */
