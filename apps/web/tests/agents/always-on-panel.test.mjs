// The Agent's Always-on tab: the budget box against Discard changes, and
// what the tab says about when a conversation wakes and what stopping keeps.
import assert from "node:assert/strict";
import { existsSync, readFileSync } from "node:fs";
import { registerHooks } from "node:module";
import test from "node:test";
import { fileURLToPath } from "node:url";
import ts from "typescript";
import { parseHTML } from "linkedom";

const webRoot = new URL("../../", import.meta.url);
const stub = source => ({ url: `data:text/javascript,${encodeURIComponent(source)}`, shortCircuit: true });
const passThrough = names => stub(`const part = ({ children }) => globalThis.createPart("div", null, children);
${names.map(name => `export const ${name} = part;`).join("\n")}`);
registerHooks({
  resolve(specifier, context, nextResolve) {
    if (specifier.endsWith(".module.css")) return stub("export default new Proxy({}, { get: (_, name) => String(name) })");
    if (specifier === "@tanstack/react-query") return stub("export const useQuery = () => ({ data: [] })");
    if (specifier === "@/lib/net/api") return stub("export const api = {}");
    if (specifier === "@/lib/net/fetch-client") return stub("export const jsonFetch = async () => ({ agent_id: 'scout', channel_targets: [] })");
    if (specifier === "@/lib/chat/transcript/channel-speaker") return stub("export const channelName = channel => channel");
    if (specifier === "@/components/ui/checkbox") return stub(`export const Checkbox = ({ checked, onCheckedChange }) => globalThis.createPart("input", { type: "checkbox", checked: checked === true, readOnly: true, onClick: () => onCheckedChange(!checked) });`);
    if (specifier === "@/components/ui/field") return passThrough(["Field", "FieldLabel"]);
    if (specifier === "@/components/ui/select") return passThrough(["SimpleSelect"]);
    if (specifier === "@/components/ui/radio-group") return passThrough(["RadioGroup", "RadioGroupItem"]);
    if (specifier === "@/components/ui/toggle-group") return passThrough(["ToggleGroup", "ToggleGroupItem"]);
    if (specifier === "@/components/ui/textarea") return passThrough(["Textarea"]);
    const base = specifier.startsWith("@/") ? new URL(specifier.slice(2), webRoot).href
      : specifier.startsWith(".") && !/\.[a-z]+$/i.test(specifier) ? new URL(specifier, context.parentURL).href : null;
    if (base) for (const suffix of [".ts", ".tsx", "/index.ts", "/index.tsx"]) if (existsSync(fileURLToPath(base + suffix))) return { url: base + suffix, shortCircuit: true };
    return nextResolve(specifier, context);
  },
  load(url, context, nextLoad) {
    if (url.endsWith(".tsx")) return { format: "module", shortCircuit: true, source: ts.transpileModule(readFileSync(fileURLToPath(url), "utf8"), {
      compilerOptions: { jsx: ts.JsxEmit.ReactJSX, module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 } }).outputText };
    return nextLoad(url, context);
  },
});
const { window } = parseHTML("<!doctype html><html><body></body></html>");
Object.assign(globalThis, { window, document: window.document, HTMLElement: window.HTMLElement, IS_REACT_ACT_ENVIRONMENT: true });

const { act, createElement, useState } = await import("react");
globalThis.createPart = createElement;
const { createRoot } = await import("react-dom/client");
const { AlwaysOnPanel } = await import("../../components/agents/always-on-panel.tsx");
const { normalizeAlwaysOn } = await import("../../lib/agents/always-on.ts");
const { configuration, normalizeAgent } = await import("../../components/agents/agent-types.ts");

const english = value => value;

/** The panel inside the page's draft handling: Discard restores the saved Agent at the same revision. */
async function renderPanel(savedBudget) {
  const saved = { id: "scout", revision: 3, hold_outbound: false, memory: { mode: "read_write" }, always_on: normalizeAlwaysOn({ enabled: true, daily_budget_usd: savedBudget }) };
  const handles = {};
  function Page() {
    const [draft, setDraft] = useState(saved);
    handles.draft = draft;
    handles.discard = () => setDraft({ ...saved, always_on: { ...saved.always_on } });
    return createElement(AlwaysOnPanel, { draft, text: english, update: patch => setDraft(current => ({ ...current, ...patch })) });
  }
  const host = document.createElement("div");
  document.body.appendChild(host);
  const root = createRoot(host);
  await act(async () => { root.render(createElement(Page)); });
  const box = () => host.querySelector('input[placeholder="No limit"]');
  const type = async value => {
    const props = box()[Object.keys(box()).find(key => key.startsWith("__reactProps"))];
    await act(async () => { props.onChange({ target: { value } }); });
  };
  const discard = async () => { await act(async () => { handles.discard(); }); };
  const close = async () => { await act(async () => { root.unmount(); }); host.remove(); };
  return { host, box, type, discard, close, handles };
}

test("an Agent saved without the setting does not hold", () => {
  assert.equal(normalizeAgent({ always_on: undefined }).hold_outbound, false);
  assert.equal(normalizeAgent({ always_on: undefined, hold_outbound: true }).hold_outbound, true);
});

test("the last line of the tab holds outbound messages, off by default, and says what it does", async () => {
  const panel = await renderPanel(null);
  try {
    const row = panel.host.lastElementChild;
    assert.match(row.textContent, /Hold what it sends for my approval/);
    assert.match(row.textContent, /waits for your approval, in every permission mode/);
    assert.equal(row.closest("fieldset"), null);
    const box = row.querySelector("input");
    assert.equal(box.checked, false);
    const props = box[Object.keys(box).find(key => key.startsWith("__reactProps"))];
    await act(async () => { props.onClick(); });
    assert.equal(panel.handles.draft.hold_outbound, true);
    assert.equal(configuration({ ...panel.handles.draft, name: "", avatar: {} }).hold_outbound, true);
    await panel.discard();
    assert.equal(panel.handles.draft.hold_outbound, false);
  } finally { await panel.close(); }
});

test("Discard changes puts the saved budget back in the box", async () => {
  const panel = await renderPanel(2);
  try {
    assert.equal(panel.box().value, "2");
    await panel.type("7");
    assert.equal(panel.handles.draft.always_on.daily_budget_usd, 7);
    assert.equal(panel.box().value, "7");
    await panel.discard();
    assert.equal(panel.handles.draft.always_on.daily_budget_usd, 2);
    assert.equal(panel.box().value, "2");
  } finally { await panel.close(); }
});

test("Discard changes empties the box when no budget was saved", async () => {
  const panel = await renderPanel(null);
  try {
    await panel.type("0");
    assert.equal(panel.box().value, "0");
    await panel.discard();
    assert.equal(panel.box().value, "");
  } finally { await panel.close(); }
});

test("a half-typed amount stays in the box while it is typed", async () => {
  const panel = await renderPanel(null);
  try {
    await panel.type("0.");
    assert.equal(panel.handles.draft.always_on.daily_budget_usd, 0);
    assert.equal(panel.box().value, "0.");
  } finally { await panel.close(); }
});

test("the tab says when it wakes, where it runs and what stopping keeps", async () => {
  const panel = await renderPanel(null);
  try {
    const words = panel.host.textContent;
    assert.match(words, /1 hour after you last wrote, then further apart, up to every 100 hours/);
    assert.doesNotMatch(words, /once a day/);
    assert.match(words, /runs on this computer, in each conversation's folder and sandbox/);
    assert.match(words, /Turning this off stops the wakes and keeps the conversations, reports, memory and these settings/);
  } finally { await panel.close(); }
});
