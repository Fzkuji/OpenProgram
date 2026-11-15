import assert from "node:assert/strict";
import { afterEach, describe, test } from "node:test";
import { installLoader, STUBS } from "../_support/load.mjs";
import { installDom, render } from "../_support/dom.mjs";

installLoader({
  stubs: {
    "@/lib/i18n": STUBS.i18n,
    "@/lib/net/fetch-client": "export const jsonFetch = (...args) => globalThis.__jsonFetch(...args);",
  },
  replace: {
    "/components/ui/tooltip.tsx": "export const HoverTip = ({ children }) => children;",
  },
});
installDom();

const { createElement } = await import("react");
const { CatalogPanel } = await import("../../components/mcp/mcp-catalog-panel.tsx");

function deferred() {
  let resolve, reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
}

/** Every jsonFetch call, answered by `routes(url, init)`. */
let requests = [];
function serve(routes) {
  requests = [];
  globalThis.__jsonFetch = (url, init = {}) => {
    const request = { url, init };
    requests.push(request);
    return routes(url, init, request);
  };
}

const SUGGESTED = {
  suggested: [{ label: "Official", url: "https://a.example/catalog.json" }],
  quick_install: [{ name: "fetch", type: "local", command: ["uvx", "mcp-fetch"], tags: ["web"] }],
};
const catalog = (name, servers) => ({ name, servers, skipped: 0 });

const buttons = (container) => [...container.querySelectorAll("button")];
const button = (container, label) => buttons(container).find((node) => node.textContent.trim() === label);
const click = (view, node) => view.act(() => { node.dispatchEvent(new window.Event("click", { bubbles: true })); });
async function type(view, input, value) {
  input.type ||= "text"; // linkedom has no default type, and React only treats text inputs as changing.
  Object.getOwnPropertyDescriptor(Object.getPrototypeOf(input), "value").set.call(input, value);
  await view.act(() => { input.dispatchEvent(new window.Event("input", { bubbles: true })); });
}
const flush = (view) => view.act(async () => { for (let i = 0; i < 5; i += 1) await Promise.resolve(); });

let view;
afterEach(async () => { await view?.unmount(); view = null; });

function mount(props = {}) {
  return render(createElement(CatalogPanel, {
    existingNames: new Set(),
    onInstalled: async () => {},
    ...props,
  }));
}

describe("MCP catalog panel", () => {
  test("a catalog binds to the URL it was requested for, and installs carry that URL", async () => {
    const pending = deferred();
    serve((url) => {
      if (url === "/api/mcp/catalog/suggested") return Promise.resolve(SUGGESTED);
      if (url.startsWith("/api/mcp/catalog?")) return pending.promise;
      return Promise.resolve({});
    });
    view = await mount();
    await flush(view);
    const input = view.container.querySelector("#cat-url");
    await type(view, input, "https://first.example/c.json");
    await click(view, button(view.container, "Load"));
    assert.equal(requests.at(-1).url, `/api/mcp/catalog?url=${encodeURIComponent("https://first.example/c.json")}`);

    // The user edits the field while the request is in flight.
    await type(view, input, "https://second.example/c.json");
    pending.resolve(catalog("First", [{ name: "linear", type: "remote", url: "https://mcp.linear.app" }]));
    await flush(view);
    assert.match(view.container.textContent, /First - 1 installable/);

    const install = buttons(view.container).filter((node) => node.textContent.trim() === "Install").at(-1);
    await click(view, install);
    await flush(view);
    const post = requests.find((request) => request.url === "/api/mcp/servers");
    assert.equal(post.init.method, "POST");
    assert.deepEqual(JSON.parse(post.init.body), {
      name: "linear", type: "remote", url: "https://mcp.linear.app",
      source_catalog_url: "https://first.example/c.json",
    });
  });

  test("a quick-install entry has no catalog provenance", async () => {
    serve((url) => Promise.resolve(url === "/api/mcp/catalog/suggested" ? SUGGESTED : {}));
    view = await mount();
    await flush(view);
    await click(view, button(view.container, "Install"));
    await flush(view);
    const post = requests.find((request) => request.url === "/api/mcp/servers");
    assert.equal(JSON.parse(post.init.body).source_catalog_url, undefined);
  });

  test("while a catalog loads, catalog and install buttons are disabled; unmounting aborts the request", async () => {
    const pending = deferred();
    serve((url) => (url === "/api/mcp/catalog/suggested" ? Promise.resolve(SUGGESTED) : pending.promise));
    view = await mount();
    await flush(view);
    await click(view, button(view.container, "Official"));
    const catalogRequest = requests.at(-1);
    assert.equal(catalogRequest.url, `/api/mcp/catalog?url=${encodeURIComponent("https://a.example/catalog.json")}`);
    assert.equal(view.container.querySelector("#cat-url").value, "https://a.example/catalog.json");
    assert.ok(button(view.container, "Fetching..."), "the Load button says it is fetching");
    assert.equal(button(view.container, "Fetching...").disabled, true);
    assert.equal(button(view.container, "Official").disabled, true);
    assert.equal(button(view.container, "Install").disabled, true);
    assert.equal(catalogRequest.init.signal.aborted, false);
    await view.unmount();
    view = null;
    assert.equal(catalogRequest.init.signal.aborted, true);
  });

  test("a newer catalog request aborts the previous one, whose late answer is ignored", async () => {
    const first = deferred();
    const second = deferred();
    const answers = [first.promise, second.promise];
    serve((url) => (url === "/api/mcp/catalog/suggested" ? Promise.resolve(SUGGESTED) : answers.shift()));
    view = await mount();
    await flush(view);
    // Two loads before React re-renders the disabled state (a double click).
    const official = button(view.container, "Official");
    await view.act(() => {
      official.dispatchEvent(new window.Event("click", { bubbles: true }));
      official.dispatchEvent(new window.Event("click", { bubbles: true }));
    });
    const [older, newer] = requests.filter((request) => request.url.startsWith("/api/mcp/catalog?"));
    assert.ok(older && newer, "both clicks issued a request");
    assert.equal(older.init.signal.aborted, true);
    assert.equal(newer.init.signal.aborted, false);
    second.resolve(catalog("Newer", []));
    await flush(view);
    first.reject(Object.assign(new Error("aborted"), { name: "AbortError" }));
    await flush(view);
    assert.match(view.container.textContent, /Newer - 0 installable/);
    assert.doesNotMatch(view.container.textContent, /aborted/);
    assert.equal(button(view.container, "Load").disabled, false, "loading ends with the newer request");
  });

  test("installing keeps its own progress through the parent refresh and does not show as fetching", async () => {
    const refreshed = deferred();
    const installed = [];
    serve((url) => Promise.resolve(url === "/api/mcp/catalog/suggested" ? SUGGESTED : {}));
    view = await mount({ onInstalled: (name) => { installed.push(name); return refreshed.promise; } });
    await flush(view);
    await click(view, button(view.container, "Install"));
    await flush(view);
    assert.deepEqual(installed, ["fetch"]);
    assert.ok(button(view.container, "Installing..."), "install progress lasts until the parent refresh ends");
    assert.ok(button(view.container, "Load"), "the catalog is not shown as loading");
    assert.equal(button(view.container, "Load").disabled, true);
    assert.equal(button(view.container, "Official").disabled, true);
    refreshed.resolve();
    await flush(view);
    assert.ok(button(view.container, "Install"));
    assert.equal(button(view.container, "Load").disabled, false);
  });

  test("install failures and an already-installed name are shown, not sent", async () => {
    serve((url) => (url === "/api/mcp/catalog/suggested"
      ? Promise.resolve(SUGGESTED)
      : Promise.reject(new Error("server did not start"))));
    view = await mount();
    await flush(view);
    await click(view, button(view.container, "Install"));
    await flush(view);
    assert.match(view.container.textContent, /server did not start/);
    await view.unmount();

    serve((url) => Promise.resolve(url === "/api/mcp/catalog/suggested" ? SUGGESTED : {}));
    view = await mount({ existingNames: new Set(["fetch"]) });
    await flush(view);
    assert.equal(button(view.container, "Installed").disabled, true);
    assert.equal(requests.some((request) => request.url === "/api/mcp/servers"), false);
  });

  test("loading with an empty URL asks for one and sends nothing", async () => {
    serve(() => Promise.resolve({}));
    view = await mount();
    await flush(view);
    await click(view, button(view.container, "Load"));
    assert.match(view.container.textContent, /paste a catalog URL first/);
    assert.equal(requests.some((request) => request.url.startsWith("/api/mcp/catalog?")), false);
  });

  test("the shared search query filters quick-install and catalog entries", async () => {
    serve((url) => Promise.resolve(url === "/api/mcp/catalog/suggested" ? SUGGESTED : {}));
    view = await mount({ query: "nothing-matches" });
    await flush(view);
    assert.doesNotMatch(view.container.textContent, /Quick install/);
    await view.rerender(createElement(CatalogPanel, { existingNames: new Set(), onInstalled: async () => {}, query: "WEB" }));
    assert.match(view.container.textContent, /Quick install/);
  });
});
