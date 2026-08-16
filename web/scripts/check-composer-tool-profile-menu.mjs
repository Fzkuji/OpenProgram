import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const webRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const controls = fs.readFileSync(
  path.join(webRoot, "components/chat/composer/controls/controls-cluster.tsx"),
  "utf8",
);
const css = fs.readFileSync(
  path.join(webRoot, "components/chat/composer/composer.module.css"),
  "utf8",
);

assert.match(
  controls,
  /<Menu\.SubmenuRoot\s+open=\{profileMenuOpen\}\s+onOpenChange=\{/,
  "Tool Profile must use Base UI's controlled submenu state",
);
assert.match(
  controls,
  /switchProfile\("__agent__"\)[\s\S]*Use Agent configuration/,
  "The submenu must default to the current Agent's persistent configuration",
);
const composer = fs.readFileSync(
  path.join(webRoot, "components/chat/composer/index.tsx"),
  "utf8",
);
assert.match(composer, /useState\("__agent__"\)/);
assert.doesNotMatch(
  composer,
  /api\/tool-profiles\/activate/,
  "A session preset must not mutate the global active profile",
);
assert.match(
  controls,
  /<Menu\.SubmenuTrigger[\s\S]*?openOnHover=\{false\}/,
  "Tool Profile must open by click, not hover",
);
assert.match(
  controls,
  /reason\s*!==\s*["']sibling-open["']/,
  "Tool Profile must ignore Base UI's pointer-hover close reason",
);
assert.doesNotMatch(
  controls,
  /<Menu\.Item[\s\S]{0,500}<Menu\.SubmenuTrigger/,
  "Tool Profile must not nest one menuitem inside another",
);
assert.match(
  controls,
  /role="none"[\s\S]{0,200}<Menu\.Item[\s\S]{0,1000}<\/Menu\.Item>\s*<Menu\.SubmenuRoot[\s\S]{0,1000}<Menu\.SubmenuTrigger/,
  "Tools and its profile gear must be sibling keyboard actions",
);
assert.doesNotMatch(
  controls,
  /e\.detail\s*!==\s*0/,
  "Keyboard activation must not be discarded",
);
assert.match(
  controls,
  /toggleTools\(\);\s*setProfileMenuOpen\(false\)/,
  "Activating Tools must close an open profile menu",
);
assert.match(
  controls,
  /className=\{styles\.plusMenuSplitRow\}\s+role="none"\s+data-tools-active=\{toolsEnabled \|\| undefined\}/,
  "The visual row must expose whether its trailing check is present",
);
assert.match(
  css,
  /\.plusMenuSplitRow\s*\{\s*position:\s*relative;/,
  "The visual row must remain the gear's positioning context",
);
assert.match(
  css,
  /\.plusMenuSplitRow:hover\s+\.plusMenuItem\s*\{\s*background:\s*var\(--bg-hover\);/,
  "Hovering the gear must preserve the original full-row hover background",
);
assert.match(
  css,
  /\.plusMenuGear\s*\{[\s\S]*?position:\s*absolute;[\s\S]*?right:\s*4px;/,
  "The inactive gear must keep its original right-edge position",
);
assert.match(
  css,
  /\.plusMenuGear\s*\{[\s\S]*?width:\s*22px;[\s\S]*?height:\s*22px;/,
  "The gear must keep its original 22px button size",
);
assert.match(
  controls,
  /<Settings\s+size=\{14\}\s*\/>/,
  "The gear must keep its original 14px icon size",
);
assert.match(
  css,
  /\.plusMenuSplitRow\[data-tools-active\]\s+\.plusMenuGear\s*\{\s*right:\s*24px;/,
  "When checked, the gear must sit immediately before the trailing check",
);
assert.match(
  controls,
  /if\s*\(!o\)\s*setProfileMenuOpen\(false\)/,
  "Closing the parent menu must close Tool Profile",
);
assert.match(
  controls,
  /side="right"[\s\S]*?style=\{\{\s*zIndex:\s*201\s*\}\}/,
  "Tool Profile must render above the parent menu",
);
assert.match(
  controls,
  /<Menu\.Portal>[\s\S]*?<Menu\.Positioner\s+side="right"/,
  "Tool Profile must render through Base UI's portal",
);
assert.doesNotMatch(
  controls,
  /menuPosition|profileMenuBackdrop/,
  "Tool Profile must not use composer-local fixed positioning",
);
assert.doesNotMatch(
  css,
  /\.profileMenuBackdrop\s*\{/,
  "Tool Profile must not create a composer-local stacking context",
);

console.log("composer tool-profile menu check passed");
