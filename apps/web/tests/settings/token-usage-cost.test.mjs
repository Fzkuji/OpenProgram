// Token Usage splits cost the way it is billed: the cost card is what
// pay-per-token providers charge, with the count of calls that have no
// price and, on hover, what subscriptions cover at API prices; a
// subscription's model row says Included. An older Worker that sends only
// `cost` / `cost_known` still reads "Unknown" when a price is missing.
import assert from "node:assert/strict";
import { afterEach, test } from "node:test";
import { installLoader, STUBS } from "../_support/load.mjs";
import { installDom, render } from "../_support/dom.mjs";
import { UI_STUBS, settle } from "../_support/provider-fixtures.mjs";

installLoader({
  stubs: { "@/lib/i18n": STUBS.i18n },
  replace: {
    "/components/ui/tooltip.tsx": UI_STUBS["/components/ui/tooltip.tsx"],
    "/lib/prefs/settings-cache.ts": "export const readCachedSettings = () => undefined; export const invalidate = () => {}; export const cachedFetch = async (url) => globalThis.answer(url);",
  },
});
installDom();

const { createElement: h } = await import("react");
const { TokenUsageSection } = await import("../../components/settings/token-usage/index.tsx");

const row = (over) => ({ input_tokens: 1000, output_tokens: 200, cache_read_tokens: 0, cache_write_tokens: 0, total_tokens: 1200, cost: 0, events: 4, ...over });
const totals = (over) => ({ input_tokens: 4000, output_tokens: 800, cache_read_tokens: 500, cache_write_tokens: 0, total_tokens: 5300, cost: 47.5, cost_known: false, events: 31, ...over });

function serve(summaryTotals, byModel = [], byKind = [], byLabel = []) {
  globalThis.answer = async (url) => url.startsWith("/api/usage/summary")
    ? { totals: summaryTotals, by_model: byModel, by_kind: byKind, by_label: byLabel }
    : { series: {} };
}

async function open() {
  const view = await render(h(TokenUsageSection));
  await settle(view);
  return view;
}
const card = (view, label) => [...view.container.querySelectorAll(".card")].find((c) => c.querySelector(".cardLabel").textContent.startsWith(label));

afterEach(() => { document.body.innerHTML = ""; });

test("the cost card is the billed sum, with unpriced calls and subscriptions' list price", async () => {
  serve(totals({ billed_cost: 6.3, included_cost: 41.2, unpriced_events: 25 }), [
    row({ model: "gpt-5.5", provider: "openai-codex", cost: 41.2, included: true }),
    row({ model: "deepseek-chat", provider: "deepseek", cost: 6.3, unpriced_events: 25 }),
    row({ model: "glm-4.6", provider: "zai", cost: 0 }),
  ]);
  const view = await open();
  const cost = card(view, "usage.card.cost");
  assert.equal(cost.querySelector(".cardValue").textContent, "$6.30");
  assert.equal(cost.querySelector(".cardNote").textContent, " · 25 calls unpriced");
  assert.equal(cost.querySelector("[data-tip]").getAttribute("data-tip"), "Subscriptions cover $41.20 at API prices");
  const cells = [...view.container.querySelectorAll("tbody tr")].map((tr) => tr.lastElementChild.textContent);
  assert.deepEqual(cells, ["Included", "$6.30 · 25 unpriced", "$0.00"]);
  assert.ok(view.container.querySelector('[data-tip="$41.20 at API prices"]'));
  await view.unmount();
});

test("everything priced and nothing on a plan: just the cost", async () => {
  serve(totals({ billed_cost: 12, included_cost: 0, unpriced_events: 0 }));
  const view = await open();
  const cost = card(view, "usage.card.cost");
  assert.equal(cost.querySelector(".cardValue").textContent, "$12.00");
  assert.equal(cost.querySelector(".cardNote"), null);
  assert.equal(cost.querySelector("[data-tip]"), null);
  await view.unmount();
});

test("an older Worker without the split still says Unknown", async () => {
  serve(totals({ cost_known: false }));
  const view = await open();
  assert.equal(card(view, "usage.card.cost").querySelector(".cardValue").textContent, "Unknown");
  await view.unmount();
});

test("source and function rows read Included for subscription calls, not their list price", async () => {
  serve(totals({ billed_cost: 0.5, included_cost: 53.35, unpriced_events: 0 }), [], [
    row({ kind: "chat", cost: 53.35, billed_cost: 0, included_cost: 53.35, unpriced_events: 0 }),
    row({ kind: "exec", cost: 9.91, billed_cost: 0.5, included_cost: 9.41, unpriced_events: 2 }),
    row({ kind: "title", cost: 0.3 }),
  ], [
    row({ label: "", cost: 53.35, billed_cost: 0, included_cost: 53.35, unpriced_events: 0 }),
  ]);
  const view = await open();
  const cells = [...view.container.querySelectorAll("tbody tr")].map((tr) => tr.lastElementChild.textContent);
  assert.deepEqual(cells, ["Included", "$0.50 · 2 unpriced", "$0.30", "Included"]);
  assert.ok(view.container.querySelector('[data-tip="Subscriptions cover $53.35 at API prices"]'));
  await view.unmount();
});
