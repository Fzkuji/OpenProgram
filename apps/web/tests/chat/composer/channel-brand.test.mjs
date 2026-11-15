// Chat channels show as their brand logos: one brand map, a logo beside
// the sidebar title instead of a "[WeChat]" prefix, a chip that names the
// account only, and a menu with one row per app opening its accounts.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { describe, test } from "node:test";
import { installLoader, STUBS, webRoot } from "../../_support/load.mjs";
import { installDom, render } from "../../_support/dom.mjs";

// A Radix-shaped dropdown that renders everything inline, keeping each
// part's className and data attributes so the structure can be read.
const DROPDOWN = `import { createElement as h } from "react";
  const part = (tag, kind) => ({ children, asChild, onSelect, sideOffset, alignOffset, ...rest }) =>
    h(tag, { ...rest, "data-part": kind, onClick: onSelect }, children);
  export const Root = ({ children }) => children;
  export const Trigger = ({ children }) => children;
  export const Portal = ({ children }) => children;
  export const Content = part("div", "content");
  export const Item = part("div", "item");
  export const Separator = part("hr", "separator");
  export const Sub = part("div", "sub");
  export const SubTrigger = part("div", "sub-trigger");
  export const SubContent = part("div", "sub-content");`;

installLoader({
  stubs: { "@/lib/i18n": STUBS.i18n },
  replace: {
    "/components/chat/messages/attachments/user-attachments.tsx": "export const parseAttachments = (text) => ({ text, attachments: [] });",
    "/lib/runtime-bridge/session/session-load.ts": "export const sendRuntimeCommand = () => true;",
    "/components/chat/top-bar/menus/channel-choices.ts": [
      "import { channelBrand as brand } from '@/lib/channels/channel-brands';",
      "export const channelBrand = brand;",
      "export const useChannelChoices = () => globalThis.channelChoices;",
    ].join("\n"),
    "/@radix-ui/react-dropdown-menu/": DROPDOWN,
  },
});
installDom();

const { createElement: h } = await import("react");
const { labelFor } = await import("../../../components/sidebar/sessions/helpers.ts");
const { CHANNEL_BRANDS, channelBrand, channelBrandInfo, channelChipText } = await import("../../../lib/channels/channel-brands.ts");
const { PLATFORM_LABEL } = await import("../../../components/settings/channels/types.ts");
const { ChannelLogo } = await import("../../../components/channels/channel-logo.tsx");
const { ChannelMenuRows } = await import("../../../components/chat/top-bar/menus/channel-menu.tsx");

describe("brand map", () => {
  test("names, brand colours and logos for the four apps", () => {
    assert.deepEqual(Object.keys(CHANNEL_BRANDS).sort(), ["discord", "slack", "telegram", "wechat"]);
    assert.equal(channelBrand("wechat"), "WeChat");
    assert.equal(channelBrand("WeChat"), "WeChat");
    assert.equal(channelBrand("matrix"), "matrix");
    assert.equal(channelBrand(null), "");
    assert.equal(channelBrandInfo("wechat").color, "#07C160");
    assert.equal(channelBrandInfo("telegram").color, "#26A5E4");
    assert.equal(channelBrandInfo("discord").color, "#5865F2");
    assert.equal(channelBrandInfo("slack").color, null);
    assert.equal(channelBrandInfo("matrix"), null);
  });

  test("Settings reads its platform names from the same map", () => {
    assert.deepEqual(PLATFORM_LABEL, { wechat: "WeChat", telegram: "Telegram", discord: "Discord", slack: "Slack" });
  });

  test("every logo ships, and Slack's mark carries its four colours", () => {
    for (const brand of Object.values(CHANNEL_BRANDS)) {
      const svg = readFileSync(new URL(`public${brand.logo}`, webRoot), "utf8");
      assert.match(svg, /^<svg[^>]*viewBox="0 0 24 24"/);
    }
    const slack = readFileSync(new URL("public/images/channels/slack.svg", webRoot), "utf8");
    for (const color of ["#E01E5A", "#36C5F0", "#2EB67D", "#ECB22E"]) assert.ok(slack.includes(`fill="${color}"`), color);
  });
});

describe("ChannelLogo", () => {
  test("outside the composer a one-colour mark is the whole logo masked in its brand colour, 14 px by default", async () => {
    const view = await render(h(ChannelLogo, { channel: "wechat" }));
    const logo = view.container.querySelector("[data-channel-logo]");
    assert.equal(logo.hasAttribute("data-brand-mark"), false);
    const style = logo.getAttribute("style");
    assert.match(style, /width:14px;height:14px/);
    assert.match(style, /background-color:\s*#07C160/i);
    assert.match(style, /wechat\.svg"\) center \/ contain no-repeat/);
    await view.unmount();
  });

  test("in the composer (fit=fill) the mark's ink fills its 16 px box", async () => {
    const view = await render(h(ChannelLogo, { channel: "wechat", size: 16, fit: "fill" }));
    const logo = view.container.querySelector("[data-channel-logo]");
    assert.equal(logo.getAttribute("data-brand-mark"), "fill");
    assert.match(logo.getAttribute("style"), /width:16px;height:16px/);
    const ink = logo.firstElementChild;
    assert.equal(ink.tagName, "SPAN");
    assert.match(ink.getAttribute("style"), /background-color:\s*#07C160/i);
    assert.match(ink.getAttribute("style"), /wechat\.svg/);
    await view.unmount();
  });

  test("Slack is drawn as its own picture; an unknown channel gets the chat glyph", async () => {
    const slack = await render(h(ChannelLogo, { channel: "slack", size: 16 }));
    assert.equal(slack.container.querySelector("img").getAttribute("src"), "/images/channels/slack.svg");
    await slack.unmount();
    const other = await render(h(ChannelLogo, { channel: "matrix" }));
    assert.equal(other.container.querySelector("[data-channel-logo]"), null);
    assert.ok(other.container.querySelector("svg"));
    await other.unmount();
  });
});

describe("labelFor", () => {
  test("a channel conversation's label is its title, with no brand prefix", () => {
    assert.equal(labelFor({ title: "Weekend plan", channel: "wechat" }, "Untitled"), "Weekend plan");
    assert.equal(labelFor({ title: "New conversation", preview: "hi there", channel: "wechat" }, "Untitled"), "hi there");
    assert.equal(labelFor({ title: "", channel: "telegram" }, "Untitled"), "Untitled");
  });
});

describe("channel chip text", () => {
  test("bound shows the account only; the tooltip names the brand", () => {
    assert.deepEqual(channelChipText("wechat", "default"), { label: "default", tip: "WeChat · default" });
    assert.deepEqual(channelChipText("telegram", null), { label: "Telegram", tip: "Telegram" });
    assert.deepEqual(channelChipText(null, null), { label: null, tip: null });
  });
});

describe("channel menu", () => {
  const groups = [
    { plat: "wechat", accounts: [
      { channel: "wechat", account_id: "default", enabled: true },
      { channel: "wechat", account_id: "baba", name: "Baba", enabled: true },
    ] },
    { plat: "telegram", accounts: [{ channel: "telegram", account_id: "bot1", enabled: true }] },
  ];

  async function menu(current) {
    const picks = [];
    const view = await render(h(ChannelMenuRows, {
      loaded: true, groups, current, onPick: (c, a) => picks.push([c, a]), onManage() {},
    }));
    return { view, picks, root: view.container };
  }

  test("one row per app, opening that app's accounts; the bound app shows its account", async () => {
    const { view, root } = await menu({ channel: "wechat", account_id: "baba" });
    const apps = [...root.querySelectorAll("[data-part=sub-trigger]")];
    assert.deepEqual(apps.map((row) => row.dataset.channelApp), ["wechat", "telegram"]);
    assert.ok(apps[0].querySelector("[data-channel-logo=wechat]"));
    assert.match(apps[0].textContent, /^WeChatbaba$/);
    assert.equal(apps[1].textContent, "Telegram");
    const subs = [...root.querySelectorAll("[data-part=sub-content]")];
    assert.deepEqual([...subs[0].querySelectorAll("[data-channel-account]")].map((r) => r.dataset.channelAccount), ["default", "baba"]);
    // The bound account carries the check; the alias tag stays beside it.
    const baba = subs[0].querySelector("[data-channel-account=baba]");
    assert.ok(baba.querySelector("svg"));
    assert.ok(baba.textContent.includes("Baba"));
    assert.equal(subs[0].querySelector("[data-channel-account=default] svg"), null);
    assert.match(root.textContent, /Disconnect/);
    await view.unmount();
  });

  test("picking an account binds it; Disconnect unbinds; unbound has no Disconnect", async () => {
    const bound = await menu({ channel: "wechat", account_id: "default" });
    bound.root.querySelector("[data-channel-account=bot1]").click();
    const disconnect = [...bound.root.querySelectorAll("[data-part=item]")].find((r) => r.textContent === "Disconnect");
    disconnect.click();
    assert.deepEqual(bound.picks, [["telegram", "bot1"], ["", ""]]);
    await bound.view.unmount();

    const free = await menu({ channel: null, account_id: null });
    assert.doesNotMatch(free.root.textContent, /Disconnect/);
    assert.match(free.root.textContent, /Manage channels/);
    await free.view.unmount();
  });
});
