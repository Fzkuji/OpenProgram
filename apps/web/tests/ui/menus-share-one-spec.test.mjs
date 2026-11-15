import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

// Hand-copied menu frames drifted from the shared spec and rendered at
// 7/8 size on the 14px root; every menu now takes the shared classes.
const root = join(dirname(fileURLToPath(import.meta.url)), "../..");
const read = (path) => readFileSync(join(root, path), "utf8");

test("footer and tab menus use the shared classes, not their own copies", () => {
  for (const [tsx, css] of [
    ["components/user-menu-footer.tsx", "components/user-menu-footer.module.css"],
    ["components/center-tabs/tab-actions/tab-context-menu.tsx", "components/center-tabs/strip/tab-strip.module.css"],
  ]) {
    const source = read(tsx);
    assert.match(source, /MENU_PANEL/, tsx);
    assert.match(source, /itemCls\(false\)/, tsx);
    const styles = read(css);
    assert.doesNotMatch(styles, /--glass-surface[\s\S]{0,400}border-radius: 1rem/, css);
    assert.doesNotMatch(styles, /padding: 0\.375rem 0\.5rem/, css);
    // An unlayered background on the row class would hide itemCls's hover fill.
    for (const row of [".item {", ".tabMenuItem {"]) {
      const at = styles.indexOf(row);
      if (at !== -1) assert.doesNotMatch(styles.slice(at, styles.indexOf("}", at)), /background/, `${css} ${row}`);
    }
  }
});
