import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const read = (path) => readFileSync(new URL(`../../../app/styles/${path}`, import.meta.url), "utf8");
const token = (css, name) => css.match(new RegExp(`${name}:\\s*(#[0-9a-f]{6})\\b`, "i"))?.[1];

const value = read("chat/messages/bubbles.css").match(/--bot-said-bg:\s*([^;]+);/)?.[1] ?? "";
const recipe = value.match(/^light-dark\(oklch\(from var\(--theme-accent\) ([\d.]+) calc\(c \* ([\d.]+)\) h\), oklch\(from var\(--theme-accent\) ([\d.]+) calc\(c \* ([\d.]+)\) h\)\)$/);
const mode = { light: recipe?.slice(1, 3).map(Number), dark: recipe?.slice(3, 5).map(Number) };

const fromHex = (hex) => [1, 3, 5].map((start) => parseInt(hex.slice(start, start + 2), 16));
const toLinear = (channel) => (channel / 255 <= 0.04045 ? channel / 255 / 12.92 : ((channel / 255 + 0.055) / 1.055) ** 2.4);
const toByte = (linear) => {
  const clamped = Math.min(1, Math.max(0, linear));
  return Math.round(255 * (clamped <= 0.0031308 ? 12.92 * clamped : 1.055 * clamped ** (1 / 2.4) - 0.055));
};

function toOklab([red, green, blue]) {
  const [r, g, b] = [red, green, blue].map(toLinear);
  const [l, m, s] = [
    0.4122214708 * r + 0.5363325363 * g + 0.0514459929 * b,
    0.2119034982 * r + 0.6806995451 * g + 0.1073969566 * b,
    0.0883024619 * r + 0.2817188376 * g + 0.6299787005 * b,
  ].map(Math.cbrt);
  return [0.2104542553 * l + 0.793617785 * m - 0.0040720468 * s, 1.9779984951 * l - 2.428592205 * m + 0.4505937099 * s, 0.0259040371 * l + 0.7827717662 * m - 0.808675766 * s];
}

function fromOklab([lightness, a, b]) {
  const [l, m, s] = [lightness + 0.3963377774 * a + 0.2158037573 * b, lightness - 0.1055613458 * a - 0.0638541728 * b, lightness - 0.0894841775 * a - 1.291485548 * b].map((x) => x ** 3);
  return [4.0767416621 * l - 3.3077115913 * m + 0.2309699292 * s, -1.2684380046 * l + 2.6097574011 * m - 0.3413193965 * s, -0.0041960863 * l - 0.7034186147 * m + 1.707614701 * s].map(toByte);
}

function bubble(accentHex, [lightness, chromaScale]) {
  const [, a, b] = toOklab(fromHex(accentHex));
  return fromOklab([lightness, a * chromaScale, b * chromaScale]);
}

function contrast(first, second) {
  const luminance = (rgb) => {
    const [r, g, b] = rgb.map(toLinear);
    return 0.2126 * r + 0.7152 * g + 0.0722 * b;
  };
  const [high, low] = [luminance(first), luminance(second)].sort((x, y) => y - x);
  return (high + 0.05) / (low + 0.05);
}

test("the always-on said bubble takes its colour from the theme accent, one recipe per mode", () => {
  assert.ok(recipe, `unexpected --bot-said-bg: ${value}`);
  assert.doesNotMatch(value, /#[0-9a-f]{3,8}/i);
});

test("the blue and white themes keep the bubble colour they had when it was fixed", () => {
  const near = (actual, expectedHex, label) => actual.forEach((channel, index) => {
    assert.ok(Math.abs(channel - fromHex(expectedHex)[index]) <= 3, `${label}: ${actual} is not ${expectedHex}`);
  });
  near(bubble(token(read("themes/light.css"), "--accent-orange"), mode.light), "#dbe9fe", "light");
  near(bubble(token(read("themes/dark.css"), "--accent-orange"), mode.dark), "#24406f", "dark");
});

test("every theme with a fixed accent gets its own hue and readable text on the bubble", () => {
  const seen = new Set();
  for (const [theme, side] of [["light", "light"], ["dark", "dark"], ["beige-light", "light"], ["beige-dark", "dark"], ["aurora-light", "light"], ["aurora", "dark"]]) {
    const css = read(`themes/${theme}.css`);
    const colour = bubble(token(css, "--accent-orange"), mode[side]);
    seen.add(colour.join());
    const ratio = contrast(colour, fromHex(token(css, "--text-primary")));
    assert.ok(ratio >= 4.5, `${theme}: text contrast ${ratio.toFixed(2)}`);
  }
  assert.equal(seen.size, 6);
});
