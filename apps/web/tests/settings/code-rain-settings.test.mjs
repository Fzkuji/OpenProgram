// Appearance settings → Theme: the Code rain tile turns the rain on, which
// reveals the density slider (10–100, 40 to start) whose moves apply at once
// and clamp; Standard hides the slider again and Gradient is its own theme.
import assert from "node:assert/strict";
import { test } from "node:test";
import { installLoader } from "../_support/load.mjs";
import { installDom, render } from "../_support/dom.mjs";

// Radix primitives need DOM classes linkedom lacks; these stubs keep the
// roles, ids and ARIA state the real controls expose.
installLoader({
  replace: {
    "/components/ui/radio-group.tsx": [
      "import { createElement as h, createContext, useContext } from 'react';",
      "const Group = createContext(null);",
      "export const RadioGroup = ({ value, onValueChange, children }) => h(Group.Provider, { value: { value, onValueChange } }, h('div', { role: 'radiogroup' }, children));",
      "export const RadioGroupItem = ({ value, id }) => { const group = useContext(Group); return h('button', { type: 'button', role: 'radio', id, 'aria-checked': String(group.value === value), onClick: () => group.onValueChange(value) }); };",
    ].join("\n"),
    "/components/ui/slider.tsx": [
      "import { createElement as h } from 'react';",
      "export const Slider = ({ id, min, max, step, value, onValueChange, 'aria-valuetext': valueText }) =>",
      "  h('input', { type: 'range', id, min, max, step, value: value[0], 'aria-valuetext': valueText, onChange: (event) => onValueChange([Number(event.target.value)]) });",
    ].join("\n"),
    "/components/ui/switch.tsx": "import { createElement as h } from 'react'; export const Switch = ({ checked, onCheckedChange, id }) => h('button', { type: 'button', role: 'switch', id, 'aria-checked': String(Boolean(checked)), onClick: () => onCheckedChange?.(!checked) });",
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
const { WELCOME_BACKDROP_DENSITY_KEY } = await import("../../lib/prefs/appearance/welcome-backdrop-pref.ts");

const settle = () => new Promise((resolve) => setTimeout(resolve, 0));
const html = () => document.documentElement.dataset;
const radio = (container, id) => container.querySelector(`#${id}`);
const slider = (container) => container.querySelector("#welcome-code-rain-density");
const checked = (container, id) => radio(container, id).getAttribute("aria-checked");
const setSlider = (view, value) => view.act(async () => {
  const input = slider(view.container);
  Object.getOwnPropertyDescriptor(Object.getPrototypeOf(input), "value").set.call(input, String(value));
  input.dispatchEvent(new Event("input", { bubbles: true }));
  await settle();
});

test("the Theme tiles switch the code rain on and off and expose its density only while it is on", async () => {
  const view = await render(h(AppearanceSection));
  await view.act(settle);
  const { container } = view;

  assert.equal(checked(container, "theme-standard"), "true", "Standard is the default theme");
  assert.equal(checked(container, "welcome-code-rain"), "false");
  assert.equal(checked(container, "theme-gradient"), "false");
  assert.equal(slider(container), null, "no density control while the rain is off");
  assert.equal(html().codeRain, "off");

  await view.act(async () => { radio(container, "welcome-code-rain").click(); await settle(); });
  assert.equal(checked(container, "welcome-code-rain"), "true");
  assert.equal(html().codeRain, "on");
  const density = slider(container);
  assert.ok(density, "turning the rain on reveals the density slider");
  assert.equal(density.getAttribute("min"), "10");
  assert.equal(density.getAttribute("max"), "100");
  assert.equal(density.value, "40");
  assert.equal(density.getAttribute("aria-valuetext"), "40%");
  assert.match(container.textContent, /Code rain density/);

  await setSlider(view, 65);
  assert.equal(slider(container).value, "65");
  assert.equal(slider(container).getAttribute("aria-valuetext"), "65%");
  assert.equal(localStorage.getItem(WELCOME_BACKDROP_DENSITY_KEY), "65", "a move applies and is saved at once");
  await setSlider(view, 3);
  assert.equal(slider(container).value, "10", "the density never drops under 10%");
  await setSlider(view, 400);
  assert.equal(slider(container).value, "100", "nor rises over 100%");

  await view.act(async () => { radio(container, "theme-gradient").click(); await settle(); });
  assert.equal(checked(container, "theme-gradient"), "true");
  assert.equal(checked(container, "welcome-code-rain"), "false");
  assert.equal(slider(container), null, "the gradient theme has no density");
  assert.equal(html().backdrop, "gradient");
  assert.equal(html().codeRain, "off");

  await view.act(async () => { radio(container, "theme-standard").click(); await settle(); });
  assert.equal(checked(container, "theme-standard"), "true");
  assert.equal(html().backdrop, "standard");
  await view.unmount();
});
