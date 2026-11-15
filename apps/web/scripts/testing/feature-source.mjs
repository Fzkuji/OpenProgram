import { readFileSync, readdirSync } from "node:fs";
import { createRequire } from "node:module";
const webRoot = new URL("../../", import.meta.url);
function read(path) { return readFileSync(new URL(path, webRoot), "utf8"); }
export function readDesktopBridgeSource() {
  const names = readdirSync(new URL("lib/desktop/bridge/", webRoot))
    .filter((name) => /^bridge-.*\.ts$/.test(name)).sort();
  return [read("lib/desktop/desktop-bridge.ts"), ...names.map((name) => read(`lib/desktop/bridge/${name}`))].join("\n");
}
export function readFileTreeSource() {
  return ["tree/file-tree.tsx", "tree/file-tree-actions.ts", "tree/file-tree-operation.ts", "tree/file-tree-query.ts"]
    .map((name) => read(`components/files/${name}`)).join("\n");
}
export function readDesktopMainSource() {
  return createRequire(import.meta.url)("../../../desktop/scripts/main-source.js").readMainSource();
}
/** The app-wide stylesheets a test may assert on, as one text: the design
 *  tokens (app/styles/design/), the fallback palette and the shell's base
 *  styles (app/styles/base/). Where a rule lives inside them can move. */
export function readGlobalStyles() {
  const dir = (path) => readdirSync(new URL(`app/styles/${path}/`, webRoot))
    .filter((name) => name.endsWith(".css")).sort().map((name) => read(`app/styles/${path}/${name}`));
  return [...dir("design"), read("app/styles/themes/fallback.css"), ...dir("base")].join("\n");
}

/** The center area's CSS modules — tab strip, built-in browser and built-in
 *  pages — read as one text, so checks do not depend on which file a rule
 *  lives in. */
export const CENTER_TAB_STYLE_FILES = [
  "components/center-tabs/strip/tab-strip.module.css",
  "components/center-tabs/browser/browser.module.css",
  "components/center-tabs/pages/pages.module.css",
];
export function readCenterTabStyles() {
  return CENTER_TAB_STYLE_FILES.map(read).join("\n");
}

/** The sidebar's Recents list — the list, its sections, rows, markers and
 *  menus under components/sidebar/sessions/ — read as one text. */
export function readSidebarSessionsSource() {
  return readdirSync(new URL("components/sidebar/sessions/", webRoot))
    .filter((name) => /\.tsx?$/.test(name)).sort()
    .map((name) => read(`components/sidebar/sessions/${name}`)).join("\n");
}

/** Entry files whose pieces live in files beside them: a check that reads the
 *  entry reads the pieces too, so it does not depend on which file a
 *  component moved to. */
const SPLIT_SOURCES = {
  "components/center-tabs/browser/web-tab-pane.tsx": ["components/center-tabs/browser/pane/"],
  "components/center-tabs/pages/builtin-tab-pane.tsx": ["components/center-tabs/pages/library/"],
  "components/chat/messages/list/message-list.tsx": ["components/chat/messages/list/rows/"],
  "components/right-sidebar/running/running-panel.tsx": [
    "components/right-sidebar/running/running-rows.tsx",
    "components/right-sidebar/running/running-format.ts",
  ],
};
export function readSplitSource(path) {
  const parts = (SPLIT_SOURCES[path] || []).flatMap((part) => part.endsWith("/")
    ? readdirSync(new URL(part, webRoot)).filter((name) => /\.tsx?$/.test(name)).sort().map((name) => part + name)
    : [part]);
  return [path, ...parts].map(read).join("\n");
}
