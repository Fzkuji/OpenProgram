// /settings/providers/<id> opens that provider's page: the route wins over
// the list, the catalogue is read once, and a fresh cache shows the page on
// the first render, before any effect runs.
import assert from "node:assert/strict";
import test from "node:test";
import { installLoader, STUBS } from "../_support/load.mjs";
import { installDom, render } from "../_support/dom.mjs";

installLoader({
  stubs: {
    "@tanstack/react-query": "export const useQueryClient=()=>({invalidateQueries(){}});",
    "@/lib/i18n": STUBS.i18n,
    "@/lib/prefs/settings-cache": "export const readCachedSettings=(url)=>globalThis.cached?.[url];export const cachedFetch=(url)=>globalThis.load(url);export const invalidate=()=>{};",
    "@/lib/nav/shallow-nav": "export const pushPath=()=>{};",
    "./detail": "export const Detail=({provider})=>globalThis.providerElement('output',{},provider.id);",
    "./routing-overview": "export const RoutingOverview=()=>null;",
    "./add/add-provider-dialog": "export const AddProviderDialog=()=>null;",
    "./add/pick": "export const PickProvider=()=>null;",
    "./types": "export const refreshAgentChip=()=>{};",
    "./auth/api-key": "export const ApiKey=()=>null;",
  },
});
installDom();
const { act, createElement } = await import("react");
globalThis.providerElement = createElement;
const { ProvidersSection } = await import("../../components/settings/providers/index.tsx");

test("the provider in the URL opens once the catalogue arrives, read once", async () => {
  let resolveList;
  const listCalls = [];
  const list = new Promise((resolve) => { resolveList = resolve; });
  globalThis.load = (url) => {
    if (url === "/api/providers/list") { listCalls.push(url); return list; }
    if (url === "/api/providers/status") return Promise.resolve({ generated_at: 0, stale_after_s: 900, providers: [] });
    return Promise.resolve({ env: [], local: [], popular: [], catalogue_count: 0 });
  };
  const view = await render(createElement(ProvidersSection));
  await view.rerender(createElement(ProvidersSection, { initialProviderId: "ollama" }));
  await act(async () => resolveList({ providers: [{ id: "default", label: "Default", enabled: true }, { id: "ollama", label: "Ollama", enabled: false }] }));
  assert.equal(view.container.querySelector("output").textContent, "ollama");
  assert.equal(listCalls.length, 1);
  await view.unmount();
});

test("a fresh cached catalogue shows the provider on the first render", async () => {
  const { renderToStaticMarkup } = await import("react-dom/server");
  globalThis.cached = { "/api/providers/list": { providers: [{ id: "ollama", label: "Ollama", enabled: true }] } };
  assert.match(renderToStaticMarkup(createElement(ProvidersSection, { initialProviderId: "ollama" })), /<output>ollama<\/output>/);
  globalThis.cached = undefined;
});
