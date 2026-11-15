// The Agents page's delegation settings: the General tab's switch that
// offers an Agent to other agents, and the Advanced tab's choice of where
// it works when one of them starts it.
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
    if (specifier === "@/lib/net/fetch-client") return stub("export const jsonFetch = () => new Promise(() => {})");
    if (specifier === "@/components/animated-icons") return stub("const icon = () => null; export const FileTextIcon = icon; export const CopyIcon = icon;");
    if (specifier === "@/components/avatar") return passThrough(["AvatarPicker"]);
    if (specifier === "./model-picker") return passThrough(["ModelPicker"]);
    if (specifier === "./agent-controls") return passThrough(["AgentButton"]);
    if (specifier === "@/components/ui/button") return passThrough(["Button"]);
    if (specifier === "@/components/ui/checkbox") return passThrough(["Checkbox"]);
    if (specifier === "@/components/ui/radio-group") return passThrough(["RadioGroup", "RadioGroupItem"]);
    if (specifier === "@/components/ui/input") return stub(`export const Input = ({ id, value }) => globalThis.createPart("input", { id, value, readOnly: true });`);
    if (specifier === "@/components/ui/textarea") return stub(`export const Textarea = ({ id, value }) => globalThis.createPart("textarea", { id, value, readOnly: true });`);
    if (specifier === "@/components/settings/settings-ui") return stub(`
      export const SettingsSection = ({ title, children }) => globalThis.createPart("section", { "data-section": title }, children);
      export const SettingsRow = ({ label, htmlFor, children }) => globalThis.createPart("div", { "data-row": label }, globalThis.createPart("label", { htmlFor }, label), children);`);
    // The system parts, reduced to what a test reads: which part drew the
    // control, its value, its options, and the tip on it.
    if (specifier === "@/components/ui/switch") return stub(`export const Switch = ({ id, checked, onCheckedChange }) =>
      globalThis.createPart("button", { id, role: "switch", "data-part": "Switch", "aria-checked": String(checked), onClick: () => onCheckedChange(!checked) });`);
    if (specifier === "@/components/ui/select") return stub(`export const SimpleSelect = ({ id, value, options, onValueChange, label }) =>
      globalThis.createPart("div", { id, "data-part": "SimpleSelect", "data-value": value, "aria-label": label },
        options.map(option => globalThis.createPart("button", { key: option.value, "data-option": option.value, onClick: () => onValueChange(option.value) }, option.label)));`);
    if (specifier === "@/components/ui/tooltip") return stub(`export const HoverTip = ({ label, children }) => globalThis.createPart("span", { "data-tip": label }, children);`);
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
const { AdvancedPanel, OverviewPanel } = await import("../../components/agents/agent-panels.tsx");
const { configuration, delegationOn } = await import("../../components/agents/agent-types.ts");

const english = value => value;
const chinese = (_english, value) => value;
const agent = extra => ({
  id: "pr-verifier", name: "PR Verifier", description: "", revision: 1, system_prompt: "",
  model: { provider: "", id: "" }, thinking_effort: "", tools: { mode: "automatic" },
  identity: { name: "", mention_patterns: [] }, ...extra,
});

async function render(Panel, saved, text = english) {
  const handles = {};
  function Page() {
    const [draft, setDraft] = useState(saved);
    handles.draft = draft;
    return createElement(Panel, { draft, text, catalog: { models: [] }, dirty: false, update: patch => setDraft(current => ({ ...current, ...patch })) });
  }
  const host = document.createElement("div");
  document.body.appendChild(host);
  const root = createRoot(host);
  await act(async () => { root.render(createElement(Page)); });
  const click = async node => {
    const props = node[Object.keys(node).find(key => key.startsWith("__reactProps"))];
    await act(async () => { props.onClick(); });
  };
  return { host, handles, click, close: async () => { await act(async () => { root.unmount(); }); host.remove(); } };
}

test("an Agent that never set it is offered when it has programs, and not when it has none", () => {
  assert.equal(delegationOn({ tools: { mode: "automatic" } }), true);
  assert.equal(delegationOn({ delegation: "", tools: { mode: "selected" } }), true);
  assert.equal(delegationOn({ delegation: "", tools: { mode: "none" } }), false);
  assert.equal(delegationOn({ delegation: "on", tools: { mode: "none" } }), true);
  assert.equal(delegationOn({ delegation: "off", tools: { mode: "automatic" } }), false);
});

test("General has the switch after Description, its explanation on the control", async () => {
  const page = await render(OverviewPanel, agent({ delegation: "" }));
  try {
    const rows = [...page.host.querySelectorAll("[data-section='Identity'] > [data-row]")].map(row => row.getAttribute("data-row"));
    assert.deepEqual(rows, ["Display name", "Agent ID", "Description", "Other agents can use it", "Avatar"]);
    const row = page.host.querySelector("[data-row='Other agents can use it']");
    const control = row.querySelector("[role=switch]");
    assert.equal(control.getAttribute("data-part"), "Switch");
    assert.equal(row.querySelector("label").getAttribute("for"), control.id);
    assert.equal(control.getAttribute("aria-checked"), "true");
    assert.equal(control.closest("[data-tip]").getAttribute("data-tip"), "Listed to other agents with its description.");
    // The tip's trigger writes its own data-state on what it wraps; on the
    // Switch itself that would replace "checked" and the track would lose its fill.
    assert.equal(control.parentElement.hasAttribute("data-tip"), false, "the tip wraps a span around the Switch");
    assert.equal(row.textContent, "Other agents can use it", "no paragraph beside the control");
    await page.click(control);
    assert.equal(page.handles.draft.delegation, "off");
    assert.equal(configuration({ ...page.handles.draft, avatar: {} }).delegation, "off");
    await page.click(page.host.querySelector("[role=switch]"));
    assert.equal(page.handles.draft.delegation, "on");
  } finally { await page.close(); }
});

test("Advanced chooses where a delegated Agent works with the page's select", async () => {
  const page = await render(AdvancedPanel, agent({ isolation: "" }));
  try {
    const select = page.host.querySelector("#agent-isolation");
    assert.equal(select.getAttribute("data-part"), "SimpleSelect");
    assert.equal(page.host.querySelector("select"), null, "no native select");
    assert.equal(select.closest("label").firstChild.textContent, "When delegated");
    assert.equal(select.getAttribute("data-value"), "");
    assert.deepEqual([...select.querySelectorAll("[data-option]")].map(option => [option.getAttribute("data-option"), option.textContent]),
      [["", "Automatic"], ["none", "Same folder"], ["worktree", "Own worktree"]]);
    assert.match(select.closest("[data-tip]").getAttribute("data-tip"), /when another agent starts it/);
    await page.click(select.querySelector("[data-option='worktree']"));
    assert.equal(page.handles.draft.isolation, "worktree");
    assert.equal(page.host.querySelector("#agent-isolation").getAttribute("data-value"), "worktree");
    assert.equal(configuration({ ...page.handles.draft, avatar: {} }).isolation, "worktree");
    await page.click(page.host.querySelector("[data-option='none']"));
    assert.equal(page.handles.draft.isolation, "none");
  } finally { await page.close(); }
});

test("both settings read in Chinese", async () => {
  const general = await render(OverviewPanel, agent({}), chinese);
  try { assert.ok(general.host.querySelector("[data-row='其他 Agent 可以调用']")); } finally { await general.close(); }
  const advanced = await render(AdvancedPanel, agent({ isolation: "none" }), chinese);
  try {
    const select = advanced.host.querySelector("#agent-isolation");
    assert.equal(select.closest("label").firstChild.textContent, "被委派时");
    assert.deepEqual([...select.querySelectorAll("[data-option]")].map(option => option.textContent), ["自动", "同一文件夹", "独立工作树"]);
  } finally { await advanced.close(); }
});
