// Every brand logo is drawn by BrandMark. In the composer and its popups
// (fit="fill") the logo's generated ink fills a 16 px box that grows to
// 18 px on hover; everywhere else (fit="contain") the whole logo is fitted
// into its box and does not move. No other component draws a logo file.
import assert from "node:assert/strict";
import { readFileSync, readdirSync } from "node:fs";
import { join, relative } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, test } from "node:test";
import { createElement as h } from "react";
import { installLoader, webRoot } from "../_support/load.mjs";
import { installDom, render } from "../_support/dom.mjs";

installLoader();
installDom();

const { generate, brandLogoPaths, OUTPUT } = await import("../../scripts/icons/generate-brand-ink-bounds.mjs");
const { INK_BOUNDS } = await import("../../components/brand-mark/ink-bounds.generated.ts");
const { fitInk, wholeViewBox } = await import("../../components/brand-mark/fit.ts");
const { BrandMark, cssUrl, inkBoundsFor } = await import("../../components/brand-mark/brand-mark.tsx");
const { BOT_EXTENTS, BOT_SHAPES, botMarkFit } = await import("../../components/avatar/bot/catalog.ts");

const near = (actual, expected, label) => assert.ok(Math.abs(actual - expected) < 0.011, `${label}: ${actual} != ${expected}`);

describe("generated ink bounds", () => {
  test("every logo file has an entry, and nothing else does", () => {
    const paths = brandLogoPaths();
    assert.ok(paths.length > 60, `found ${paths.length} logos`);
    assert.deepEqual(Object.keys(INK_BOUNDS), paths);
  });

  test("the committed file is what the generator writes now", async () => {
    assert.equal(readFileSync(OUTPUT, "utf8"), await generate(), "run: npm run generate:brand-ink-bounds --workspace apps/web");
  });

  test("a logo the generator has not measured counts its whole view box", () => {
    assert.deepEqual(inkBoundsFor("/images/providers/not-shipped.svg"), wholeViewBox());
  });
});

describe("the fit rule", () => {
  test("square ink fills the box", () => {
    const fit = fitInk(wholeViewBox(), 16);
    near(fit.width, 16, "width");
    near(fit.height, 16, "height");
    near(fit.left, 0, "left");
    near(fit.top, 0, "top");
  });

  test("wide ink fills the width; its centre sits on the box's centre", () => {
    const ink = { x: 0, y: 2.19, width: 24, height: 19.62, viewBox: [0, 0, 24, 24] };
    const fit = fitInk(ink, 16);
    near(ink.width * fit.scale, 16, "ink width");
    near(ink.height * fit.scale, 13.08, "ink height");
    near(fit.left + (ink.x + ink.width / 2) * fit.scale, 8, "centre x");
    near(fit.top + (ink.y + ink.height / 2) * fit.scale, 8, "centre y");
  });

  test("tall ink fills the height", () => {
    const ink = { x: 7, y: 0, width: 10, height: 24, viewBox: [0, 0, 24, 24] };
    const fit = fitInk(ink, 16);
    near(ink.height * fit.scale, 16, "ink height");
    near(ink.width * fit.scale, 6.67, "ink width");
    near(fit.left + 12 * fit.scale, 8, "centre x");
  });

  test("off-centre ink and odd view boxes are centred by their ink", () => {
    const low = fitInk({ x: 0, y: 12, width: 24, height: 12, viewBox: [0, 0, 24, 24] }, 16);
    near(low.top + 18 * low.scale, 8, "centre y");
    const ink = INK_BOUNDS["/images/providers/chutes.svg"];
    const fit = fitInk(ink, 16);
    near(fit.left + (ink.x + ink.width / 2) * fit.scale, 8, "chutes centre x");
    near(fit.top + (ink.y + ink.height / 2) * fit.scale, 8, "chutes centre y");
    near(Math.max(ink.width, ink.height) * fit.scale, 16, "chutes longer side");
  });

  test("every shipped logo stays inside its 16 x 16 box and touches it", () => {
    for (const [path, ink] of Object.entries(INK_BOUNDS)) {
      const fit = fitInk(ink, 16);
      assert.ok(ink.width * fit.scale <= 16.01, path);
      assert.ok(ink.height * fit.scale <= 16.01, path);
      near(Math.max(ink.width, ink.height) * fit.scale, 16, path);
    }
  });

  test("a character in the Agent chip and menu follows the same rule", () => {
    for (const shape of BOT_SHAPES) {
      const [x0, y0, x1, y1] = BOT_EXTENTS[shape];
      const fit = botMarkFit(shape);
      const width = ((x1 - x0) / 100) * fit.scale * 16;
      const height = ((y1 - y0) / 100) * fit.scale * 16;
      assert.ok(width <= 16.01 && height <= 16.01, shape);
      near(Math.max(width, height), 16, shape);
      near((fit.left + (fit.scale * (x0 + x1)) / 200) * 16, 8, `${shape} centre x`);
      near((fit.top + (fit.scale * (y0 + y1)) / 200) * 16, 8, `${shape} centre y`);
    }
  });
});

describe("BrandMark", () => {
  async function draw(props) {
    props = { fit: "fill", ...props };
    const view = await render(h(BrandMark, props));
    const box = view.container.querySelector("[data-brand-mark]");
    const result = { box, inner: box.firstElementChild, html: view.container.innerHTML };
    await view.unmount();
    return result;
  }

  test("fill: a one-colour logo is a mask in the given colour, placed by its ink", async () => {
    const url = "/images/channels/wechat.svg";
    const { box, inner } = await draw({ logo: { url, mono: true }, color: "#07C160", attributes: { "data-channel-logo": "wechat" } });
    assert.equal(box.getAttribute("data-channel-logo"), "wechat");
    assert.match(box.getAttribute("style"), /width:16px;height:16px/);
    const style = inner.getAttribute("style");
    const fit = fitInk(INK_BOUNDS[url], 16);
    const px = (value) => (value === 0 ? "0" : `${value}px`);
    assert.match(style, new RegExp(`left:${px(fit.left)};top:${px(fit.top)};width:${px(fit.width)};height:${px(fit.height)}`));
    assert.match(style, /background-color:#07C160/i);
    assert.match(style, /mask:url\("\/images\/channels\/wechat\.svg"\) 0 0 \/ 100% 100% no-repeat/);
    assert.match(style, /pointer-events:none/);
  });

  test("fill: the text colour is the default fill; a coloured logo is its own picture", async () => {
    assert.match((await draw({ logo: { url: "/images/providers/openai.svg", mono: true } })).inner.getAttribute("style"), /background-color:currentColor/);
    const { inner } = await draw({ logo: { url: "/images/providers/claude-color.svg", mono: false }, size: 36 });
    assert.equal(inner.tagName, "IMG");
    assert.equal(inner.getAttribute("src"), "/images/providers/claude-color.svg");
    assert.match(inner.getAttribute("style"), /width:36px;height:36px/);
  });

  test("a url cannot end the CSS value it is quoted in", () => {
    assert.equal(cssUrl('/a"b\\c.svg'), 'url("/a\\22 b\\5c c.svg")');
    assert.equal(cssUrl("/images/providers/openai.svg"), 'url("/images/providers/openai.svg")');
  });

  test("fill: it grows to exactly 18 px on its control's hover", () => {
    const text = readFileSync(new URL("components/brand-mark/brand-mark.tsx", webRoot), "utf8");
    assert.match(text, /followHostHover\(/);
    assert.match(text, /variants=\{HOVER_POP_FILL\}/);
    assert.doesNotMatch(text, /variants=\{HOVER_POP\}/);
    assert.match(text, /useReducedMotion/);
  });

  test("contain (the default): the whole logo in its box, still on hover", async () => {
    const view = await render(h(BrandMark, { logo: { url: "/images/channels/wechat.svg", mono: true }, color: "#07C160", size: 14, attributes: { "data-channel-logo": "wechat" } }));
    const box = view.container.querySelector("[data-channel-logo]");
    assert.equal(box.hasAttribute("data-brand-mark"), false);
    assert.equal(box.children.length, 0);
    const style = box.getAttribute("style");
    assert.match(style, /width:14px;height:14px/);
    assert.match(style, /background-color:#07C160/i);
    assert.match(style, /mask:url\("\/images\/channels\/wechat\.svg"\) center \/ contain no-repeat/);
    assert.match(box.getAttribute("class"), /\bblock shrink-0\b/);
    await view.unmount();
    const picture = await render(h(BrandMark, { logo: { url: "/images/providers/claude-color.svg", mono: false }, size: 16, lazy: true }));
    const img = picture.container.querySelector("img");
    assert.equal(img.getAttribute("width"), "16");
    assert.equal(img.getAttribute("loading"), "lazy");
    assert.equal(img.getAttribute("style"), null);
    await picture.unmount();
  });
});

describe("one drawer", () => {
  const LOGO = /images\/(providers|channels)|providerLogo|channelBrandInfo|CHANNEL_BRANDS|brand\.logo|BrandLogo|ProviderLogo|logo\.url/;
  const DRAWS = /\bmask\s*:|WebkitMask|maskImage|<img\b|<Image\b|createElement\(\s*["']img["']|backgroundImage|background:\s*url/;
  const root = fileURLToPath(webRoot);

  function sources(directory) {
    return readdirSync(directory, { withFileTypes: true }).flatMap((entry) => {
      const path = join(directory, entry.name);
      if (entry.isDirectory()) return entry.name === "node_modules" ? [] : sources(path);
      return /\.(tsx?|css)$/.test(entry.name) ? [path] : [];
    });
  }

  test("no component outside components/brand-mark draws a logo file", () => {
    const offenders = ["app", "components", "lib"]
      .flatMap((folder) => sources(join(root, folder)))
      .map((path) => relative(root, path).split("\\").join("/"))
      .filter((path) => !path.startsWith("components/brand-mark/"))
      .filter((path) => {
        const text = readFileSync(join(root, path), "utf8");
        return LOGO.test(text) && DRAWS.test(text);
      });
    assert.deepEqual(offenders, []);
  });

  // Every logo element in the app. Inside the composer and its popups a
  // logo fills a 16 px box (fit="fill"); everywhere else it keeps the size
  // it had before the composer's fill rule and is fitted whole (contain).
  const FILL = new Map([
    ["components/chat/composer/environment-row/chips/channel-chip.tsx", ['<ChannelLogo channel={channel} size={16} fit="fill" />']],
    ["components/chat/top-bar/menus/channel-menu.tsx", ['<ChannelLogo channel={g.plat} size={16} fit="fill" />']],
  ]);
  const CONTAIN = new Map([
    ["components/sidebar/sessions/conv-item.tsx", ["<ChannelLogo channel={conv.channel} size={14} />"]],
    ["components/command-palette.tsx", ["<ChannelLogo channel={conv.channel} size={16} />"]],
    ["components/chats/chats-page.tsx", ["<ChannelLogo channel={conv.channel} size={16} />"]],
    ["components/settings/providers/routing-overview.tsx", ["<ProviderIcon id={g.provider} size={16} />"]],
    ["components/settings/providers/detail.tsx", ["<ProviderIcon id={provider.id} size={32} />"]],
    ["components/settings/providers/status/provider-card.tsx", ["<ProviderIcon id={companyIcon(group)} size={20} />"]],
    ["components/settings/providers/status/provider-rows.tsx", ["<ProviderIcon id={p.id} size={20} />", "<ProviderIcon id={companyIcon(group)} size={20} />"]],
    ["components/settings/providers/add/add-provider-dialog.tsx", ["<ProviderIcon id={choice.id} size={28} />", "<ProviderIcon id={step.choice.id} size={28} />"]],
    ["components/settings/third-party-harnesses/harness-models.tsx", ["<ProviderIcon id={ICON[row.id] ?? row.id} size={20} />"]],
    ["components/settings/third-party-harnesses/index.tsx", ["<ProviderIcon id={ICON[a.agent] ?? a.agent} size={20} />", "<ProviderIcon id={ICON[s.agent] ?? s.agent} size={18} />"]],
    ["components/settings/providers/add/pick.tsx", ["<ProviderIcon id={icon} size={20} />", "<ProviderIcon id={p.id} size={20} />", '<ProviderIcon id={c.id} size={40} plate="brand" />', '<ProviderIcon id={row.id} size={40} plate="brand" />']],
  ]);
  const DRAWERS = new Set([
    "components/brand-mark/brand-mark.tsx",
    "components/channels/channel-logo.tsx",
    "components/chat/top-bar/chips/model-mark.tsx",
    "components/settings/providers/icons/provider-icon.tsx",
  ]);

  test("logos fill their box only in the composer; elsewhere they keep their size", () => {
    const found = new Map();
    for (const path of ["app", "components"].flatMap((folder) => sources(join(root, folder)))) {
      const file = relative(root, path).split("\\").join("/");
      if (DRAWERS.has(file)) continue;
      const text = readFileSync(path, "utf8");
      const elements = [...text.matchAll(/<(ChannelLogo|BrandMark|ProviderIcon)\b[^>]*>/g)].map((match) => match[0]);
      if (elements.length) found.set(file, elements);
      assert.doesNotMatch(text, /<BrandMark\b/, `${file}: draw logos through ChannelLogo, ModelMark or ProviderIcon`);
    }
    const sorted = (map) => Object.fromEntries([...map].sort(([a], [b]) => a.localeCompare(b)));
    assert.deepEqual(sorted(found), sorted(new Map([...FILL, ...CONTAIN])));
    const model = readFileSync(join(root, "components/chat/top-bar/chips/model-mark.tsx"), "utf8");
    assert.match(model, /<BrandMark [^>]*fit="fill"/);
    assert.match(model, /<SolarIcon [^>]*fit="fill"/);
    const provider = readFileSync(join(root, "components/settings/providers/icons/provider-icon.tsx"), "utf8");
    assert.doesNotMatch(provider, /fit="fill"/);
    const chip = readFileSync(join(root, "components/chat/composer/environment-row/chips/channel-chip.tsx"), "utf8");
    assert.match(chip, /<SolarIcon name="chat-round-dots" size=\{16\} fit="fill"/);
    const agentMenu = readFileSync(join(root, "components/chat/top-bar/menus/agent-config-menu.tsx"), "utf8");
    assert.equal(agentMenu.match(/<AgentAvatar [^>]*size=\{16\} fit="mark"/g)?.length, 2);
  });
});
