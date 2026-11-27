// A provider's ways to sign in, as the choice list (MethodChoices) read from
// the registry's method metadata: the first is the recommended one, each has
// its one-line description and an import its file. Continue starts the
// chosen way; a way named by the add dialog starts at once.
import assert from "node:assert/strict";
import { afterEach, describe, test } from "node:test";
import { installLoader, STUBS } from "../../_support/load.mjs";
import { installDom, render } from "../../_support/dom.mjs";
import { UI_STUBS, button, fakeFetch, settle } from "../../_support/provider-fixtures.mjs";

installLoader({ stubs: { "@/lib/i18n": STUBS.i18n }, replace: { "/components/ui/radio-group.tsx": UI_STUBS["/components/ui/radio-group.tsx"], "/components/ui/spinner.tsx": UI_STUBS["/components/ui/spinner.tsx"] } });
installDom();

const { createElement: h } = await import("react");
const { ProviderLogin } = await import("../../../components/settings/providers/auth/provider-login.tsx");

const method = (id, label, description, path) => ({
  id, label, label_zh: label, description, description_zh: description, ...(path ? { path } : {}),
});
const QWEN = {
  id: "qwen", label: "Qwen",
  login_methods: [
    method("import_from_cli", "Import existing login", "Uses the sign-in Qwen Code saved on this machine.", "~/.qwen/oauth_creds.json"),
    method("api_key", "Paste an API key", "A key from the provider's console."),
    method("api_key_alt", "Paste a second key", "Another key from the console."),
  ],
};
const SINGLE = {
  id: "qwen-cli", label: "Qwen CLI",
  login_methods: [method("import_from_cli", "Import existing login", "Uses the sign-in Qwen Code saved on this machine.")],
};

const rows = (view) => [...view.container.querySelectorAll("[data-method]")];

afterEach(() => { document.body.innerHTML = ""; });

describe("ways to sign in", () => {
  test("the first is recommended; each way has its description and an import its file", async () => {
    fakeFetch({});
    const view = await render(h(ProviderLogin, { provider: QWEN, bare: true }));
    const [first, second, third] = rows(view);
    assert.deepEqual(rows(view).map((r) => r.dataset.method), ["import_from_cli", "api_key", "api_key_alt"]);
    assert.equal(first.hasAttribute("data-recommended"), true);
    assert.match(first.textContent, /Import existing loginRecommended/);
    assert.match(first.textContent, /Import existing login.*~\/\.qwen\/oauth_creds\.json/);
    assert.equal(second.hasAttribute("data-recommended"), false);
    assert.match(second.textContent, /Paste an API key/);
    assert.match(third.textContent, /Paste a second key/);
    assert.equal(first.querySelector('[role="radio"]').getAttribute("aria-checked"), "true", "the recommended way is chosen to begin with");
    assert.doesNotMatch(view.container.textContent, /Other ways/);
    // The picked card reads by its border, with no second ring on focus, and
    // each description is bright enough to read beside its title.
    assert.match(first.className, /has-data-checked:border-primary(\s|$)/);
    assert.match(first.className, /has-data-checked:has-\[>\[data-slot=field\]\]:has-\[:focus-visible\]:ring-0/);
    assert.match(first.querySelector('[data-slot="field-description"]').className, /text-text-secondary/);
    await view.unmount();
  });

  test("a single way shows no recommendation", async () => {
    fakeFetch({});
    const view = await render(h(ProviderLogin, { provider: SINGLE, bare: true }));
    assert.equal(rows(view).length, 1);
    assert.equal(rows(view)[0].hasAttribute("data-recommended"), false);
    assert.doesNotMatch(view.container.textContent, /Recommended/);
    await view.unmount();
  });

  test("Continue starts the chosen way's login", async () => {
    const calls = fakeFetch({
      "POST /api/providers/qwen/login/start": { session: "s1" },
      "POST /api/providers/qwen/login/cancel": {},
    });
    const view = await render(h(ProviderLogin, { provider: QWEN, bare: true }));
    await view.act(() => rows(view)[2].querySelector('[role="radio"]').click());
    await view.act(() => button(view.container, "Continue").click());
    await settle(view);
    assert.deepEqual(calls.find(([m, url]) => m === "POST" && url.endsWith("/login/start"))[2], { method: "api_key_alt" });
    await view.unmount();
  });

  test("a way named by the add dialog starts at once, without the list", async () => {
    const calls = fakeFetch({
      "POST /api/providers/qwen/login/start": { session: "s1" },
      "POST /api/providers/qwen/login/cancel": {},
    });
    const view = await render(h(ProviderLogin, { provider: QWEN, bare: true, method: "import_from_cli" }));
    await settle(view);
    assert.equal(rows(view).length, 0);
    assert.deepEqual(calls.filter(([m, url]) => m === "POST" && url.endsWith("/login/start")).map(([, , b]) => b), [{ method: "import_from_cli" }]);
    await view.unmount();
  });

  test("each read waits at the worker for the next change; none is asked on a timer", async () => {
    const answers = [
      { events: [], cursor: 0, version: 0, waiting: false, done: false },
      { events: [{ type: "progress", message: "Completing sign-in…" }], cursor: 1, version: 1, waiting: false, done: false },
      { events: [], cursor: 1, version: 2, waiting: false, done: true, ok: true, name: "c1" },
    ];
    const calls = fakeFetch({
      "POST /api/providers/qwen-cli/login/start": { session: "s1" },
      "GET /api/providers/qwen-cli/login/poll": () => answers.shift(),
    });
    const view = await render(h(ProviderLogin, { provider: SINGLE, bare: true, method: "import_from_cli" }));
    await settle(view, 8);
    const reads = calls.filter(([m, url]) => m === "GET" && url.includes("/login/poll")).map(([, url]) => url);
    assert.equal(reads.length, 3, "each read follows the one before at once");
    assert.doesNotMatch(reads[0], /wait=/, "the first read answers at once");
    assert.match(reads[1], /cursor=0&version=0&wait=25/);
    assert.match(reads[2], /cursor=1&version=1&wait=25/);
    assert.match(view.container.textContent, /Signed in/);
    await view.unmount();
  });
});
