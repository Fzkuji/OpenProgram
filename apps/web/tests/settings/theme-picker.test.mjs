// Appearance settings' theme picker: the Mode tiles preview the chosen colour
// style and the Style tiles preview the chosen mode, choosing one applies the
// theme, the accent row offers presets, a custom colour well and Follow theme
// back to the style's own accent, and Custom CSS can be switched on, seeded from the
// template and cleared.
import assert from "node:assert/strict";
import { test } from "node:test";
import { installLoader } from "../_support/load.mjs";
import { installDom, render } from "../_support/dom.mjs";

// Radix primitives need DOM classes linkedom lacks; these stubs keep the
// roles and ARIA state the real controls expose.
installLoader({
  replace: {
    "/components/ui/radio-group.tsx": [
      "import { createElement as h, createContext, useContext } from 'react';",
      "const Group = createContext(null);",
      "export const RadioGroup = ({ value, onValueChange, children, className }) => h(Group.Provider, { value: { value, onValueChange } }, h('div', { role: 'radiogroup' }, children));",
      "export const RadioGroupItem = ({ value, id }) => { const group = useContext(Group); return h('button', { type: 'button', role: 'radio', id, 'aria-checked': String(group.value === value), onClick: () => group.onValueChange(value) }); };",
    ].join("\n"),
    "/components/ui/switch.tsx": "import { createElement as h } from 'react'; export const Switch = ({ checked, onCheckedChange, disabled, size, className, ...rest }) => h('button', { type: 'button', role: 'switch', 'aria-checked': String(Boolean(checked)), disabled, onClick: () => onCheckedChange?.(!checked), ...rest });",
    "/components/ui/slider.tsx": "export const Slider = () => null;",
    "/components/ui/select.tsx": "export const SimpleSelect = () => null;",
    "/components/avatar/index.tsx": "export const Avatar = () => null; export const AvatarPicker = () => null;",
    "/components/settings/sections/desktop-attention-section.tsx": "export const DesktopAttentionSection = () => null;",
    "/lib/desktop/desktop-bridge.ts": "export const desktopBridge = () => null;",
  },
});
installDom();
document.cookie = ""; // font-pref reads document.cookie, which linkedom leaves undefined.

const { createElement: h } = await import("react");
const { AppearanceSection } = await import("../../components/settings/sections/appearance-section.tsx");
const { CUSTOM_CSS_TEMPLATE } = await import("../../lib/prefs/appearance/theme-config.ts");

const settle = () => new Promise((resolve) => setTimeout(resolve, 0));
const root = () => document.documentElement;
const previews = (container, name) =>
  [...container.querySelectorAll(`[id^="${name}-"]`)].map((radio) => [
    radio.id.slice(name.length + 1),
    radio.closest("label").querySelector("[data-theme]").getAttribute("data-theme"),
  ]);
const radio = (container, id) => container.querySelector(`#${id}`);
const byLabel = (container, label) => container.querySelector(`[aria-label="${label}"]`);
const buttonNamed = (container, text) => [...container.querySelectorAll("button")].find((b) => b.textContent === text);

test("the theme picker previews, applies and resets the appearance", async () => {
  localStorage.setItem("agentic_theme_style", "beige");
  localStorage.setItem("agentic_theme_mode", "dark");
  const view = await render(h(AppearanceSection));
  await view.act(settle);
  const { container } = view;

  // Mode tiles show the current style; Style tiles show the current mode.
  assert.deepEqual(previews(container, "theme-mode"), [["auto", "beige-dark"], ["dark", "beige-dark"], ["light", "beige-light"]]);
  assert.deepEqual(previews(container, "theme-style"), [["beige", "beige-dark"], ["neutral", "dark"], ["aurora", "aurora"], ["vivid", "vivid"]]);
  assert.equal(radio(container, "theme-mode-dark").getAttribute("aria-checked"), "true");

  await view.act(async () => { radio(container, "theme-mode-light").click(); await settle(); });
  assert.equal(root().getAttribute("data-theme"), "beige-light");
  assert.deepEqual(previews(container, "theme-style"), [["beige", "beige-light"], ["neutral", "light"], ["aurora", "aurora-light"], ["vivid", "vivid-light"]]);

  await view.act(async () => { radio(container, "theme-style-aurora").click(); await settle(); });
  assert.equal(root().getAttribute("data-theme"), "aurora-light");
  assert.deepEqual(previews(container, "theme-mode"), [["auto", "aurora"], ["dark", "aurora"], ["light", "aurora-light"]]);

  // Accent: presets, a native colour well, and Follow theme back to the
  // style's own accent. The row says whether the accent follows the style
  // or overrides it.
  const accent = container.querySelector('[role="group"][aria-label="Accent color"]');
  const well = byLabel(accent, "Custom accent color");
  assert.equal(well.getAttribute("type"), "color");
  const reset = buttonNamed(accent, "Follow theme");
  assert.equal(reset.disabled, true, "nothing to reset before a custom accent");
  assert.match(container.textContent, /Follows the Aurora style; picking a colour overrides it\./);
  const preset = accent.querySelector("button[aria-pressed]");
  await view.act(async () => { preset.click(); await settle(); });
  assert.equal(preset.getAttribute("aria-pressed"), "true");
  assert.equal(reset.disabled, false);
  assert.match(container.textContent, /Your colour #[0-9a-f]{6} overrides the Aurora style's own accent; Follow theme drops it\./);
  await view.act(async () => { reset.click(); await settle(); });
  assert.equal(reset.disabled, true);
  assert.equal(localStorage.getItem("agentic_theme_accent"), "");

  // Custom CSS: Insert template seeds the editor and turns the overlay on; Clear empties it.
  const editor = byLabel(container, "Custom CSS");
  assert.equal(editor.getAttribute("placeholder"), CUSTOM_CSS_TEMPLATE);
  assert.equal(container.querySelector("#custom-css-enabled").getAttribute("aria-checked"), "false");
  await view.act(async () => { buttonNamed(container, "Insert template").click(); await settle(); });
  assert.equal(editor.value, CUSTOM_CSS_TEMPLATE);
  assert.equal(container.querySelector("#custom-css-enabled").getAttribute("aria-checked"), "true");
  await view.act(async () => { buttonNamed(container, "Clear").click(); await settle(); });
  assert.equal(editor.value, "");

  // There is no "Custom" colour style to pick any more.
  assert.doesNotMatch(container.textContent, /pick the "Custom" color style/);
  await view.unmount();
});
