import assert from "node:assert/strict";
import { describe, test } from "node:test";

import { installLoader, STUBS } from "../_support/load.mjs";
import { installDom, render } from "../_support/dom.mjs";

installLoader({ stubs: { "@/lib/i18n": STUBS.i18n } });
installDom({ pathname: "/functions" });

const { createElement } = await import("react");
const { Spinner } = await import("../../components/ui/spinner.tsx");
const { default: ShellLoading } = await import("../../app/(shell)/loading.tsx");
const { default: SettingsLoading } = await import("../../app/(shell)/settings/loading.tsx");
const { createAppQueryClient, QUERY_KEEP_MS } = await import("../../lib/query-client.ts");

const visibleText = (element) => element.textContent.replace(/ /g, "").trim();

describe("waiting says nothing", () => {
  test("a shell route on its way shows an empty page, not a title or Loading", async () => {
    for (const Fallback of [ShellLoading, SettingsLoading]) {
      const view = await render(createElement(Fallback));
      assert.ok(view.container.querySelector("[data-route-pending]"));
      assert.equal(visibleText(view.container), "");
      await view.unmount();
    }
  });
  test("a spinner says nothing of its own; one shown alone says what its caller names", async () => {
    const view = await render(createElement("div", null,
      createElement(Spinner, { "data-plain": "" }),
      createElement(Spinner, { "data-named": "", "aria-label": "Importing" })));
    const plain = view.container.querySelector("[data-plain]");
    assert.equal(plain.getAttribute("aria-hidden"), "true");
    assert.equal(plain.getAttribute("aria-label"), null);
    assert.equal(plain.getAttribute("role"), null);
    const named = view.container.querySelector("[data-named]");
    assert.equal(named.getAttribute("role"), "status");
    assert.equal(named.getAttribute("aria-label"), "Importing");
    assert.equal(named.getAttribute("aria-hidden"), null);
    await view.unmount();
  });
});

describe("the app's query client", () => {
  test("keeps answers for half an hour after their page closes", () => {
    const client = createAppQueryClient();
    assert.equal(client.getDefaultOptions().queries.gcTime, QUERY_KEEP_MS);
    assert.equal(QUERY_KEEP_MS, 30 * 60_000);
  });

  test("a revisit within that time shows the cached answer with no read in between", async () => {
    const client = createAppQueryClient();
    let reads = 0;
    const options = { queryKey: ["runs"], queryFn: async () => { reads += 1; return ["run"]; } };
    await client.fetchQuery(options);
    assert.deepEqual(client.getQueryData(["runs"]), ["run"]);
    assert.equal(client.getQueryState(["runs"]).status, "success");
    assert.equal(reads, 1);
    client.clear();
  });

  test("retries a read the worker could not answer, but not a 4xx", () => {
    const retry = createAppQueryClient().getDefaultOptions().queries.retry;
    assert.equal(retry(0, new TypeError("Failed to fetch")), true);
    assert.equal(retry(2, Object.assign(new Error("busy"), { status: 503 })), true);
    assert.equal(retry(12, new TypeError("Failed to fetch")), true);
    assert.equal(retry(0, Object.assign(new Error("gone"), { status: 404 })), false);
  });

  test("a failed read is logged, and the last data stays", async () => {
    const realWarn = console.warn;
    const warnings = [];
    console.warn = (...args) => warnings.push(args);
    try {
      const client = createAppQueryClient();
      client.setQueryData(["runs"], ["kept"]);
      await client.fetchQuery({
        queryKey: ["runs"], staleTime: 0, retry: false,
        queryFn: async () => { throw new Error("worker restarting"); },
      }).catch(() => {});
      assert.deepEqual(client.getQueryData(["runs"]), ["kept"]);
      assert.equal(warnings.length, 1);
      assert.match(String(warnings[0][0]), /read failed/);
      client.clear();
    } finally {
      console.warn = realWarn;
    }
  });
});
