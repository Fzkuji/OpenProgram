import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

import { AVATAR_STYLES } from "../../components/avatar/style-options.ts";
import { randomAvatarVariants } from "../../components/avatar/variants.ts";

test("a regenerated batch stays on the requested DiceBear style", () => {
  const originalRandom = Math.random;
  Math.random = () => 0.25;
  try {
    const variants = randomAvatarVariants("thumbs", 16);
    assert.equal(variants.length, 16);
    assert.ok(variants.every((variant) => variant.style === "thumbs"));
    assert.ok(variants.every((variant) => variant.seed.length > 0));
    assert.equal(new Set(variants.map((variant) => variant.seed)).size, 16);

    const other = randomAvatarVariants("bottts", 8);
    assert.equal(other.length, 8);
    assert.ok(other.every((variant) => variant.style === "bottts"));
  } finally {
    Math.random = originalRandom;
  }
});

test("the picker's style list matches the runtime style registry", () => {
  const runtimeStyles = readFileSync(
    new URL("../../components/avatar/styles.ts", import.meta.url),
    "utf8",
  );
  const registryBlock = runtimeStyles.match(
    /export const STYLES = \{([\s\S]*?)\}\s+as const/,
  )?.[1];
  assert.ok(registryBlock);
  const pickerStyleIds = AVATAR_STYLES.map(({ id }) => id);
  const runtimeStyleIds = [...registryBlock.matchAll(/^\s+(\w+):/gm)].map(
    (match) => match[1],
  );
  assert.equal(new Set(pickerStyleIds).size, pickerStyleIds.length);
  assert.deepEqual(new Set(pickerStyleIds), new Set(runtimeStyleIds));
});

test("Thumbs omits its generated outer background instead of cropping artwork", () => {
  const avatar = readFileSync(
    new URL("../../components/avatar/avatar.tsx", import.meta.url),
    "utf8",
  );
  assert.match(avatar, /backgroundColor:\s*style === "thumbs" \? \["transparent"\]/);
});
