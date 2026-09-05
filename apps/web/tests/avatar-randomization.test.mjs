import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

import { AVATAR_STYLES } from "../components/avatar/style-options.ts";
import { randomAvatarVariants } from "../components/avatar/variants.ts";

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

test("the picker saves the current style and the chosen seed", () => {
  const picker = readFileSync(
    new URL("../components/avatar/AvatarPicker.tsx", import.meta.url),
    "utf8",
  );

  assert.match(picker, /function pickVariant\(variant: AvatarVariant\)/);
  assert.match(picker, /kind: "dicebear"/);
  assert.match(picker, /function pickVariant\(variant: AvatarVariant\) \{[\s\S]*style,\s*\n\s*seed: variant\.seed/);
  assert.match(picker, /randomAvatarVariants\(\s*style\s*,\s*16\s*\)/);
  assert.doesNotMatch(
    picker,
    /function pickVariant\(variant: AvatarVariant\) \{[\s\S]*style: variant\.style/,
  );
  assert.doesNotMatch(picker, /each batch includes every avatar style/);
  assert.doesNotMatch(picker, /AVATAR_STYLES\.map\(\(style\) => style\.id\)/);
});

test("regenerate refreshes seeds without changing style", () => {
  const picker = readFileSync(
    new URL("../components/avatar/AvatarPicker.tsx", import.meta.url),
    "utf8",
  );

  assert.match(picker, /function regenerate\(\)/);
  assert.match(picker, /setVariants\(randomAvatarVariants\(style, 16\)\)/);
  assert.match(
    picker,
    /visibleVariants = variants\.map\(\(variant\) => \(\{[\s\S]*style,/,
  );
});

test("letter initial and color live inside the picker", () => {
  const picker = readFileSync(
    new URL("../components/avatar/AvatarPicker.tsx", import.meta.url),
    "utf8",
  );
  const settings = readFileSync(
    new URL("../components/settings/general-section.tsx", import.meta.url),
    "utf8",
  );

  assert.match(picker, /onLetterTextChange/);
  assert.match(picker, /onLetterBgChange/);
  assert.match(picker, /t\("general.agent.initial"\)/);
  assert.match(picker, /t\("general.agent.color"\)/);
  assert.match(picker, /source === "letter"/);
  assert.doesNotMatch(settings, /isLetterMode/);
  assert.doesNotMatch(settings, /t\("general.agent.initial"\)/);
  assert.doesNotMatch(settings, /t\("general.agent.color"\)/);
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
  assert.match(settings, /t\("general.avatar"\)/);
  assert.match(settings, /<AvatarPicker/);
  assert.doesNotMatch(settings, /styles\.avatarBlock/);
  assert.ok(registryBlock);
  const pickerStyleIds = AVATAR_STYLES.map(({ id }) => id);
  const runtimeStyleIds = [...registryBlock.matchAll(/^\s+(\w+),$/gm)].map(
    (match) => match[1],
  );
  assert.equal(new Set(pickerStyleIds).size, pickerStyleIds.length);
  assert.deepEqual(new Set(pickerStyleIds), new Set(runtimeStyleIds));
});

test("Thumbs omits its generated outer background instead of cropping artwork", () => {
  const avatar = readFileSync(
    new URL("../components/avatar/Avatar.tsx", import.meta.url),
    "utf8",
  );
  const chatCss = readFileSync(
    new URL("../app/styles/chat/bubbles.css", import.meta.url),
    "utf8",
  );
  const footerCss = readFileSync(
    new URL("../components/user-menu-footer.module.css", import.meta.url),
    "utf8",
  );

  assert.match(avatar, /backgroundColor:\s*style === "thumbs" \? \["transparent"\]/);
  assert.match(chatCss, /\.message-avatar\.user-avatar\s*\{[^}]*background:\s*transparent/s);
  assert.match(footerCss, /\.avatar\s*\{[^}]*background:\s*transparent/s);
  assert.doesNotMatch(chatCss, /\.user-avatar[^}]*transform:/s);
  assert.doesNotMatch(footerCss, /\.avatar[^}]*transform:/s);
});
