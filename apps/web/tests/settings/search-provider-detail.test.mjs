// A web-search backend's detail pane: its status in words, a Set-as-default
// toggle that is only offered for a usable backend, the API key editor only
// for keyed backends, and the setup block only when the catalog has steps.
import assert from "node:assert/strict";
import { afterEach, test } from "node:test";
import { installLoader } from "../_support/load.mjs";
import { installDom, render } from "../_support/dom.mjs";

installLoader({
  replace: {
    "/components/settings/providers/index.tsx": "import { createElement as h } from 'react'; export const ApiKey = ({ envVar, configured }) => h('div', { 'data-api-key': envVar, 'data-configured': String(configured) });",
    "/components/settings/search-providers/connectivity.tsx": "import { createElement as h } from 'react'; export const SearchConnectivity = ({ providerId, disabled }) => h('div', { 'data-connectivity': providerId, 'data-disabled': String(disabled) });",
    "/components/settings/search-providers/glyph.tsx": "export const SearchProviderGlyph = () => null;",
  },
});
installDom();

const { createElement: h } = await import("react");
const { SearchProviderDetail } = await import("../../components/settings/search-providers/detail.tsx");

const TAVILY = {
  id: "tavily",
  name: "Tavily",
  description: "Search built for agents.",
  priority: 2,
  env_var: "TAVILY_API_KEY",
  configured: true,
  available: true,
  signup_url: "https://example.test/signup",
  setup_steps: ["Create an account", "Copy the key"],
};
const DDG = { id: "duckduckgo", name: "DuckDuckGo", description: "Free.", priority: 9, env_var: null, configured: true, available: true };

async function renderDetail(provider, props = {}) {
  const calls = [];
  const view = await render(h(SearchProviderDetail, {
    provider, defaultId: null, saving: false, onSetDefault: (id) => calls.push(id), onChanged() {}, ...props,
  }));
  return { view, calls };
}
const defaultButton = (container) => container.querySelector("button[aria-pressed]");

afterEach(() => { document.body.innerHTML = ""; });

test("a keyed, usable backend shows its key variable, setup steps, key editor and a Set as default", async () => {
  const { view, calls } = await renderDetail(TAVILY);
  const { container } = view;
  assert.match(container.textContent, /API key env: TAVILY_API_KEY/);
  assert.match(container.textContent, /Available/);
  assert.match(container.textContent, /Priority 2\./);
  assert.deepEqual([...container.querySelectorAll("ol li")].map((li) => li.textContent), ["Create an account", "Copy the key"]);
  assert.equal(container.querySelector('a[href="https://example.test/signup"]').getAttribute("rel"), "noopener noreferrer");
  assert.equal(container.querySelector("[data-api-key]").getAttribute("data-api-key"), "TAVILY_API_KEY");
  assert.equal(container.querySelector("[data-connectivity]").getAttribute("data-disabled"), "false");

  const button = defaultButton(container);
  assert.equal(button.getAttribute("aria-pressed"), "false");
  assert.equal(button.disabled, false);
  button.click();
  assert.deepEqual(calls, ["tavily"]);
  await view.unmount();
});

test("the current default reads as pressed and cannot be set again", async () => {
  const { view } = await renderDetail(TAVILY, { defaultId: "tavily" });
  const button = defaultButton(view.container);
  assert.equal(button.getAttribute("aria-pressed"), "true");
  assert.equal(button.disabled, true);
  assert.match(button.textContent, /Default backend/);
  await view.unmount();
});

test("a backend that is not usable cannot become the default and its connectivity check is off", async () => {
  const { view } = await renderDetail({ ...TAVILY, configured: false, available: false });
  assert.match(view.container.textContent, /Not configured/);
  assert.equal(defaultButton(view.container).disabled, true);
  assert.equal(view.container.querySelector("[data-connectivity]").getAttribute("data-disabled"), "true");
  await view.unmount();
});

test("a zero-config backend says no key is needed and shows no key editor or setup block", async () => {
  const { view } = await renderDetail(DDG);
  const { container } = view;
  assert.match(container.textContent, /No key needed - zero-config/);
  assert.match(container.textContent, /doesn't require an API key/);
  assert.equal(container.querySelector("[data-api-key]"), null);
  assert.equal(container.querySelector("ol"), null);
  assert.doesNotMatch(container.textContent, /Setup/);
  await view.unmount();
});
