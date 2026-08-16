import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

import { AVATAR_STYLES } from "../components/avatar/style-options.ts";
import { randomAvatarVariants } from "../components/avatar/variants.ts";

test("a regenerated batch includes every shipped DiceBear style", () => {
  const originalRandom = Math.random;
  Math.random = () => 0.25;
  try {
    const variants = randomAvatarVariants(
      AVATAR_STYLES.map((style) => style.id),
      16,
    );
    assert.equal(variants.length, 16);
    assert.deepEqual(
      new Set(variants.map((variant) => variant.style)),
      new Set(AVATAR_STYLES.map((style) => style.id)),
    );
    assert.ok(variants.every((variant) => variant.seed.length > 0));
  } finally {
    Math.random = originalRandom;
  }
});

test("the picker saves both the style and seed of the chosen variant", () => {
  const picker = readFileSync(
    new URL("../components/avatar/AvatarPicker.tsx", import.meta.url),
    "utf8",
  );

  assert.match(picker, /function pickVariant\(variant: AvatarVariant\)/);
  assert.match(picker, /style: variant\.style/);
  assert.match(picker, /seed: variant\.seed/);
  assert.match(picker, /randomAvatarVariants\([\s\S]*AVATAR_STYLES\.map/);
  assert.doesNotMatch(picker, /each renders the same style/);
});

test("Agent and user settings share the picker and its style registry", () => {
  const settings = readFileSync(
    new URL("../components/settings/general-section.tsx", import.meta.url),
    "utf8",
  );
  const runtimeStyles = readFileSync(
    new URL("../components/avatar/styles.ts", import.meta.url),
    "utf8",
  );
  const registryBlock = runtimeStyles.match(
    /export const STYLES = \{([\s\S]*?)\}\s+as const/,
  )?.[1];

  assert.equal((settings.match(/<ProfileEditor/g) ?? []).length, 2);
  assert.match(settings, /function AgentSection\(\)[\s\S]*<ProfileEditor/);
  assert.match(settings, /function UserSection\(\)[\s\S]*<ProfileEditor/);
  assert.ok(registryBlock);
  for (const { id } of AVATAR_STYLES) {
    assert.match(registryBlock, new RegExp(`\\b${id},`));
  }
});
