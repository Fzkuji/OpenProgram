// The code rain behind an empty chat: off by default, density kept between
// 10% and 100% (40% to start), exclusive with the gradient theme, saved on
// the device and written onto <html> for the CSS. The backdrop draws nothing
// while the theme is off; with reduced motion it draws one still frame and
// never starts the animation loop.
import assert from "node:assert/strict";
import { afterEach, test } from "node:test";

import { installLoader } from "../../_support/load.mjs";
import { installDom, render, resizeObservers, setRect } from "../../_support/dom.mjs";

installLoader();
let reducedMotion = false;
installDom({ media: (query) => reducedMotion && query.includes("prefers-reduced-motion") });

// linkedom's canvas has no drawing context; this one records what is drawn.
const drawn = [];
Object.getPrototypeOf(document.createElement("canvas")).getContext = () =>
  new Proxy({}, {
    get: (target, key) => (key in target ? target[key] : (...args) => { drawn.push([key, ...args]); }),
    set: (target, key, value) => { target[key] = value; return true; },
  });
let framesRequested = 0;
globalThis.requestAnimationFrame = window.requestAnimationFrame = () => { framesRequested++; return 1; };

const { createElement: h } = await import("react");
const {
  CODE_RAIN_DENSITY, GRADIENT_THEME_STORAGE_KEY, WELCOME_BACKDROP_DENSITY_KEY, WELCOME_BACKDROP_STORAGE_KEY,
  useWelcomeBackdropPref,
} = await import("../../../lib/prefs/appearance/welcome-backdrop-pref.ts");
const { WelcomeBackdrop } = await import("../../../components/chat/welcome/welcome-backdrop.tsx");

let pref;
function Probe() { pref = useWelcomeBackdropPref(); return null; }
const html = () => document.documentElement.dataset;
const settle = () => new Promise((resolve) => setTimeout(resolve, 0));

afterEach(() => { drawn.length = 0; framesRequested = 0; });

test("the rain is off by default at 40% density and both are written to <html> and storage", async () => {
  const view = await render(h(Probe));
  assert.deepEqual(CODE_RAIN_DENSITY, { min: 10, max: 100, default: 40 });
  assert.equal(pref.enabled, false);
  assert.equal(pref.density, 40);
  assert.equal(pref.theme, "standard");
  assert.equal(html().codeRain, "off");
  assert.equal(html().backdrop, "standard");

  await view.act(async () => { pref.setEnabled(true); });
  assert.equal(pref.theme, "code-rain");
  assert.equal(html().codeRain, "on");
  assert.equal(localStorage.getItem(WELCOME_BACKDROP_STORAGE_KEY), "on");
  assert.equal(localStorage.getItem(WELCOME_BACKDROP_DENSITY_KEY), "40");
  await view.unmount();
});

test("density is rounded and kept between 10 and 100; a value that is not a number falls back to 40", async () => {
  const view = await render(h(Probe));
  const expect = async (input, output) => {
    await view.act(async () => { pref.setDensity(input); });
    assert.equal(pref.density, output, `setDensity(${input})`);
    assert.equal(localStorage.getItem(WELCOME_BACKDROP_DENSITY_KEY), String(output));
  };
  await expect(65, 65);
  await expect(3, 10);
  await expect(250, 100);
  await expect(42.4, 42);
  await expect(Number.NaN, 40);
  await view.unmount();
});

test("code rain and the gradient theme are exclusive, and a stored pair is read back clamped", async () => {
  const view = await render(h(Probe));
  await view.act(async () => { pref.setTheme("gradient"); });
  assert.equal(pref.theme, "gradient");
  assert.equal(pref.enabled, false);
  assert.equal(html().backdrop, "gradient");
  assert.equal(html().codeRain, "off");

  await view.act(async () => { pref.setEnabled(true); });
  assert.equal(pref.theme, "code-rain", "turning the rain on leaves the gradient");
  assert.equal(localStorage.getItem(GRADIENT_THEME_STORAGE_KEY), "off");
  await view.unmount();

  // Another window left both on and an oversized density: the rain wins and the density clamps.
  localStorage.setItem(WELCOME_BACKDROP_STORAGE_KEY, "on");
  localStorage.setItem(GRADIENT_THEME_STORAGE_KEY, "on");
  localStorage.setItem(WELCOME_BACKDROP_DENSITY_KEY, "500");
  const reread = await render(h(Probe));
  assert.equal(pref.theme, "code-rain");
  assert.equal(pref.density, 100);
  await reread.unmount();
});

test("the backdrop renders no canvas while the rain is off", async () => {
  const probe = await render(h(Probe));
  await probe.act(async () => { pref.setEnabled(false); });
  await probe.unmount();
  framesRequested = 0;
  const view = await render(h(WelcomeBackdrop));
  await view.act(settle);
  assert.equal(view.container.querySelector("canvas"), null);
  assert.equal(framesRequested, 0);
  await view.unmount();
});

test("with the rain on it animates; with reduced motion it draws one still frame and no animation loop", async () => {
  localStorage.setItem(WELCOME_BACKDROP_STORAGE_KEY, "on");
  const animated = await render(h(WelcomeBackdrop));
  await animated.act(settle);
  const canvas = animated.container.querySelector("canvas");
  assert.ok(canvas, "the rain draws on a canvas");
  assert.equal(canvas.getAttribute("aria-hidden"), "true");
  assert.ok(framesRequested >= 1, "the animation loop starts");
  await animated.unmount();

  reducedMotion = true;
  framesRequested = 0;
  drawn.length = 0;
  const still = await render(h(WelcomeBackdrop));
  await still.act(settle);
  const stillCanvas = still.container.querySelector("canvas");
  setRect(stillCanvas, { width: 400, height: 300 });
  await still.act(async () => { resizeObservers.trigger(stillCanvas); });
  assert.equal(framesRequested, 0, "reduced motion never requests an animation frame");
  assert.ok(drawn.some(([op]) => op === "fillText"), "one still frame of glyphs is drawn");
  await still.unmount();
  reducedMotion = false;
});
