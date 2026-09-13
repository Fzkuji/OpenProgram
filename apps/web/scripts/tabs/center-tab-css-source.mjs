// Test-only reader; components import their CSS module owners directly.
// Rules for each local class remain together and in their original order.
import { readFileSync } from "node:fs";

const modules = [
  "strip/strip.module.css",
  "strip/tab-items.module.css",
  "strip/tab-context-menu.module.css",
  "strip/split-view-picker.module.css",
  "browser/web-pane.module.css",
  "browser/browser-chrome.module.css",
  "browser/browser-controls.module.css",
  "panes/builtin-page.module.css",
  "panes/files-page.module.css",
  "panes/new-tab.module.css",
  "browser/browser-home.module.css",
  "browser/browser-glyph.module.css",
  "panes/terminal.module.css"
];

export function readCenterTabCss() {
  return modules.map((name) => readFileSync(
    new URL(`../../components/center-tabs/${name}`, import.meta.url), "utf8",
  )).join("\n");
}
