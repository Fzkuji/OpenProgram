import assert from "node:assert/strict";
import test from "node:test";
import { registerHooks } from "node:module";

registerHooks({
  resolve(specifier, context, nextResolve) {
    if (specifier.startsWith(".") && !/\.[a-z]+$/.test(specifier)) return nextResolve(`${specifier}.ts`, context);
    return nextResolve(specifier, context);
  },
});

const { BOT_SHAPES, botFit } = await import("../../components/avatar/bot/catalog.ts");
const { GLYPH_EYE, botGlyph } = await import("../../components/avatar/bot/glyph.ts");
const { loadBotRuntime } = await import("../../components/avatar/bot/runtime.ts");
const lib = (file) => new URL(`../../../../node_modules/open-bot-avatar/dist/${file}`, import.meta.url).href;
const { flattenPath, parsePath, winding } = await import(lib("path.js"));
const { EYE_H, EYE_W } = await import(lib("validate.js"));

test("the glyph's eye is the renderer's resting eye", () => {
  assert.deepEqual(GLYPH_EYE, { width: EYE_W, height: EYE_H });
});

test("every character's glyph is its body with both eyes cut through", async () => {
  const runtime = await loadBotRuntime();
  for (const shape of BOT_SHAPES) {
    const definition = runtime.definition(shape);
    const { path } = botGlyph(shape);
    assert.ok(path.startsWith(definition.body.path), shape);
    const glyph = flattenPath(parsePath(path));
    const body = flattenPath(parsePath(definition.body.path));
    const { x, y, spacing, scale } = definition.eyes;
    const w = GLYPH_EYE.width * scale * 0.4;
    const h = GLYPH_EYE.height * scale * 0.4;
    for (const cx of [x - spacing / 2, x + spacing / 2]) {
      for (const [dx, dy] of [[0, 0], [-w, -h], [w, h], [-w, h], [w, -h]]) {
        // Inside the body, and a hole in the glyph.
        assert.equal(winding(body, cx + dx, y + dy), 1, `${shape} body under the eye`);
        assert.equal(winding(glyph, cx + dx, y + dy), 0, `${shape} eye hole`);
      }
    }
    // Beside the eyes the body stays filled.
    assert.notEqual(winding(glyph, x, y + GLYPH_EYE.height * scale * 0.6 + 1), 0, `${shape} between the eyes`);
  }
});

test("the glyph's view box fits the body like an icon, as a live character is fitted", () => {
  for (const shape of BOT_SHAPES) {
    const [vx, vy, vw, vh] = botGlyph(shape).viewBox.split(" ").map(Number);
    const fit = botFit(shape);
    assert.ok(Math.abs(vw - 100 / fit.scale) < 0.01 && vw === vh, shape);
    assert.ok(Math.abs(vx + fit.left * vw) < 0.01 && Math.abs(vy + fit.top * vw) < 0.01, shape);
  }
  assert.deepEqual(botGlyph("hologram"), botGlyph("chat"));
});
