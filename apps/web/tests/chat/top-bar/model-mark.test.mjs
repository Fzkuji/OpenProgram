// The model chip shows the logo of the provider the model is called
// through, the logos the Settings provider list uses; a provider without
// one shows the cpu glyph.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import { createElement as h } from "react";
import { installLoader } from "../../_support/load.mjs";
import { installDom, render } from "../../_support/dom.mjs";

installLoader({
  replace: {
    "/components/solar-icons/index.ts": "import { createElement as h } from 'react'; export const SolarIcon = ({ name }) => h('svg', { 'data-solar': name });",
  },
});
installDom();

const { ModelMark, MODEL_FALLBACK_ICON } = await import("../../../components/chat/top-bar/chips/model-mark.tsx");
const { providerLogo } = await import("../../../components/settings/providers/icons/provider-icon.tsx");
const source = (path) => readFileSync(new URL(`../../../${path}`, import.meta.url), "utf8");

async function mark(provider) {
  const view = await render(h(ModelMark, { provider }));
  const html = view.container.innerHTML;
  await view.unmount();
  return html;
}

test("a one-colour logo is a BrandMark masked in the text colour", async () => {
  const html = await mark("openai");
  assert.match(html, /data-provider-logo="openai"/);
  assert.match(html, /data-brand-mark="fill"/);
  assert.match(html, /background-color:currentColor/);
  assert.match(html, /mask:url\(&quot;\/images\/providers\/openai\.svg&quot;\)/);
});

test("a logo fills its 16 px box", async () => {
  // OpenAI's ink is 24 x 23.79 units: 16 px wide, centred.
  const html = await mark("openai");
  assert.match(html, /^<span style="width:16px;height:16px[;"]/);
  assert.match(html, /<span style="position:absolute;left:0;top:0\.07px;width:16px;height:16px;/);
});

test("a coloured logo is the brand's own picture", async () => {
  const html = await mark("anthropic");
  assert.match(html, /<img[^>]+src="\/images\/providers\/claude-color\.svg"/);
});

test("providers without a logo show the cpu glyph", async () => {
  assert.equal(MODEL_FALLBACK_ICON, "cpu");
  for (const provider of [undefined, "openai-completions", "openai-responses", "vllm", "my-custom"]) {
    assert.equal(providerLogo(provider), null);
    assert.match(await mark(provider), /data-solar="cpu"/);
  }
});

test("local Ollama and Google use the logos already shipped", () => {
  assert.equal(providerLogo("ollama")?.url, "/images/providers/ollama.svg");
  assert.equal(providerLogo("google")?.url, "/images/providers/gemini-color.svg");
});

test("the model chip draws the mark, not the old speech bubble", () => {
  const chip = source("components/chat/top-bar/index.tsx");
  // The chip's mark: the model's provider, or for an OpenProgram model run
  // in a harness, that model's own provider.
  assert.match(chip, /<ModelMark provider=\{markProvider\} \/>/);
  assert.match(chip, /const markProvider = routed\?\.provider \?\? provider;/);
  assert.doesNotMatch(chip, /chat-round-dots/);
});

test("each provider row of the model menu starts with that provider's logo", () => {
  const menu = source("components/chat/top-bar/menus/agent-selector.tsx");
  assert.match(menu, /<ModelMark provider=\{id\}[^>]*\/>\s*\{name\}/);
  assert.match(menu, /id=\{group\.provider\}\s*name=\{providerName\(group\.provider\)\}/);
});
