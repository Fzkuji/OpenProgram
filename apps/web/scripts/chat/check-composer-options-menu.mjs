import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const webRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../..");
const controls = fs.readFileSync(
  path.join(webRoot, "components/chat/composer/controls/controls-cluster.tsx"),
  "utf8",
);
const pieces = fs.readFileSync(
  path.join(webRoot, "components/chat/composer/controls/menu-pieces.tsx"),
  "utf8",
);
const css = fs.readFileSync(
  path.join(webRoot, "components/chat/composer/composer.module.css"),
  "utf8",
);

// ── One menu grammar ────────────────────────────────────────────────
// The options (+) menu is the shared radix dropdown on the shared menu
// tokens — no second menu library, no module CSS of its own.
assert.doesNotMatch(controls, /@base-ui-components/, "the options menu must not use Base UI");
assert.match(
  controls,
  /from "@\/components\/ui\/dropdown-menu"/,
  "the options menu must be built on the radix dropdown wrappers",
);
assert.match(
  controls,
  /<DropdownMenuContent[\s\S]*?className=\{cn\(MENU_PANEL,/,
  "the options panel must wear MENU_PANEL",
);
assert.match(
  controls,
  /<DropdownMenuSeparator className=\{MENU_SEPARATOR\} \/>/,
  "separators must be the shared full-bleed MENU_SEPARATOR",
);
assert.match(
  pieces,
  /<DropdownMenuItem[\s\S]*?className=\{cn\(itemCls\(false\), MENU_ITEM_STATES, className\)\}/,
  "options rows must be radix items on the shared itemCls + radix states",
);
assert.match(
  pieces,
  /<span className=\{CHECK_SLOT_PAD\} \/>/,
  "unselected rows must reserve the check column (grammar A)",
);
assert.doesNotMatch(
  css,
  /\.plusMenu/,
  "no .plusMenu* module rules may remain — the panel and rows come from menu-styles",
);

// ── Tools come from the Agent ──────────────────────────────────────
// The Agent configuration decides the tool set; the options menu has no
// Tools switch and no Access preset submenu.
assert.doesNotMatch(controls, /DropdownMenuSub\b|toggleTools|switchProfile|toolProfiles/,
  "the options menu must not offer a per-chat Tools switch or Access preset");
assert.match(controls, /<DropdownMenuContent[\s\S]*?z-\[200\]/, "The options menu keeps its z-index of 200");
assert.doesNotMatch(controls, /menuPosition/, "The options menu must not use composer-local fixed positioning");

console.log("composer options menu check passed");
