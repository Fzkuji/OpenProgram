import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

// A sentence inside a menu (the environment menu's "Fixed for this
// conversation", the project menu's notes and errors, the model picker's
// empty state) once carried hand-written px-[8px] / text-[11px|12px], so it
// sat 2px right of the section label above it. They all use MENU_NOTE now.
const root = join(dirname(fileURLToPath(import.meta.url)), "../..");
const read = (path) => readFileSync(join(root, path), "utf8");

test("menu notes use MENU_NOTE, never hand-written insets or sizes", () => {
  for (const file of [
    "components/chat/composer/environment-row/chips/environment-chip.tsx",
    "components/chat/top-bar/menus/project-menu.tsx",
    "components/chat/top-bar/chips/working-dir-chips.tsx",
    "components/chat/top-bar/menus/agent-selector.tsx",
  ]) {
    const source = read(file);
    assert.match(source, /MENU_NOTE/, file);
    assert.doesNotMatch(source, /className="[^"]*px-\[8px\][^"]*text-\[1[12]px\]/, file);
  }
});
