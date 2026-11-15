/**
 * Functions / favorites data + click glue.
 *
 * Consumers (React sidebar / welcome screen / functions page, the WS
 * `functions_list` handler) import these exports directly.
 *
 * Imported for side effects by AppShell.
 */

import { useSessionStore } from "@/lib/session-store";
import { addAssistantMessage } from "../session/chat-handlers";
import { runtimeState } from "../state";
import { navigate } from "@/lib/nav/navigate";
import { openFunctionForm, refreshFunctionsList } from "@/lib/abilities/functions-actions";
import { setPendingRunFunction } from "@/lib/execution/use-pending-run-function";
import { confirmAction } from "@/components/ui/confirm-dialog";
import { showToast } from "@/lib/format-utils/toast";

interface FnDef {
  name: string;
  [k: string]: unknown;
}

export async function loadProgramsMeta(): Promise<void> {
  try {
    const resp = await fetch("/api/programs/meta");
    const meta = (await resp.json()) || { favorites: [], folders: {} };
    runtimeState.programsMeta = meta;
    const { useFunctions } = await import("@/lib/abilities/functions-store");
    useFunctions.getState().setMeta(meta);
  } catch {
    runtimeState.programsMeta = { favorites: [], folders: {} };
  }
}

// React owns favorites rendering (components/sidebar/sessions/favorites-list.tsx).
// Kept as a no-op so the WS `functions_list` handler doesn't crash.
export function renderFunctions(): void {}

export async function deleteFunction(name: string): Promise<void> {
  if (!(await confirmAction({
    title: 'Delete function "' + name + '"?',
    confirmLabel: "Delete",
    destructive: true,
  }))) return;
  try {
    const resp = await fetch("/api/function/" + encodeURIComponent(name), {
      method: "DELETE",
    });
    const data = await resp.json();
    if (data.deleted) {
      addAssistantMessage('Deleted function "' + name + '".');
      await refreshFunctionsList();
    } else {
      addAssistantMessage("Cannot delete: " + (data.error || "unknown error"));
    }
  } catch (e) {
    showToast("Delete failed: " + (e as Error).message, { tone: "error" });
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
  void openFunctionForm(fn.name);
}

export function clickFnExample(fnName: string): void {
  const fn = findFunction(fnName);
  if (!fn) return;
  void openFunctionForm(fn.name);
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
