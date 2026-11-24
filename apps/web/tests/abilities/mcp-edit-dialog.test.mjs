import assert from "node:assert/strict";
import { afterEach, test } from "node:test";
import { installLoader, STUBS } from "../_support/load.mjs";
import { installDom, render } from "../_support/dom.mjs";

installLoader({
  stubs: { "@/lib/i18n": STUBS.i18n },
  replace: {
    "/components/ui/dialog.tsx": 'export const Dialog=({open,children})=>open?children:null; export const DialogContent=({children})=>children; export const DialogHeader=DialogContent; export const DialogTitle=DialogContent; export const DialogDescription=DialogContent; export const DialogFooter=DialogContent;',
    "/components/ui/select.tsx": 'export const SimpleSelect=()=>null;',
    "/components/ui/switch.tsx": 'export const Switch=()=>null;',
    "/components/ui/tooltip.tsx": "export const HoverTip = ({ children }) => children;",
  },
});
installDom();

const { createElement } = await import("react");
const { EditDialog } = await import("../../components/mcp/mcp-edit-dialog.tsx");

const TARGET = {
  mode: "edit", name: "files", transport: "local", command: "uvx mcp-files", env: "", storedEnvNames: [],
  url: "", headers: "", storedHeaderNames: [], authKind: "none", bearerToken: "", hasStoredBearerToken: false,
  oauthClientName: "", oauthScope: "", oauthClientId: "", oauthClientSecret: "", hasStoredClientSecret: false,
  oauthRedirectPort: 0, enabled: true, timeout_seconds: 30, alwaysLoad: false,
};

const realFetch = globalThis.fetch;
afterEach(() => { globalThis.fetch = realFetch; document.body.innerHTML = ""; });

const answer = (body, status) => new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
const button = (container, label) => [...container.querySelectorAll("button")].find((node) => node.textContent.trim() === label);

async function saveAgainst(response) {
  globalThis.fetch = async () => response;
  let saved = 0;
  const view = await render(createElement(EditDialog, { target: TARGET, onClose() {}, onSaved() { saved += 1; } }));
  await view.act(async () => {
    button(view.container, "Save").dispatchEvent(new window.Event("click", { bubbles: true }));
    await new Promise((done) => setTimeout(done, 0));
  });
  return { view, saved: () => saved };
}

test("a save the server answers with a code, not a sentence, says what happened and keeps the dialog", async () => {
  const { view, saved } = await saveAgainst(answer({
    detail: { code: "mcp_runtime_restart_failed", persisted_config: "unchanged", runtime_state: "restored", action: "retry_or_restart" },
  }, 500));
  assert.match(view.container.textContent, /could not be restarted with these settings\. The saved settings are unchanged\./);
  assert.ok(button(view.container, "Save"), "the dialog is still there");
  assert.equal(saved(), 0);
  await view.unmount();
});

test("a save refused for what is wrong with the body lists it", async () => {
  const { view } = await saveAgainst(answer({
    detail: [{ type: "missing", loc: ["body", "command"], msg: "Field required" }],
  }, 422));
  assert.match(view.container.textContent, /command: Field required/);
  await view.unmount();
});
