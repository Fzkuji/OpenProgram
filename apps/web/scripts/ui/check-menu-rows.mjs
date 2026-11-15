// Rule: every action menu builds its rows from the shared MenuRow and shows
// keys only as MenuKey badges (the notation itself is tested in
// tests/ui/menu-keys.test.mjs); the desktop overlay rows keep the same
// slots in the same order; every tab-menu row has its icon; and browser
// menus are owned by a per-pane id, never by the URL-bearing tab id.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

const root = new URL("../../", import.meta.url);
const read = (path) => readFileSync(new URL(path, root), "utf8");

const MENUS = [
  "components/sidebar/sessions/conv-menu.tsx",
  "components/sidebar/projects/project-menu.tsx",
  "components/chat/pane/view-controls.tsx",
  "components/center-tabs/strip/main-menu.tsx",
  "app/menu-overlay/main-menu/page.tsx",
  "components/center-tabs/tab-actions/tab-context-menu.tsx",
  "components/center-tabs/browser/toolbar/browser-controls.tsx",
  "components/center-tabs/pages/terminal-context-menu.tsx",
  "components/center-tabs/pip/web-tab-pip.tsx",
  "components/center-tabs/browser/toolbar/browser-control-bar.tsx",
  "components/center-tabs/pages/library/bookmarks-page.tsx",
  "components/center-tabs/review/review-tab-pane.tsx",
  "components/files/explorer/tree-context-menu.tsx",
  "components/files/file-management.tsx",
  "components/agents/agents-page.tsx",
  "components/right-sidebar/agents-view-menu.tsx",
];
for (const path of MENUS) {
  const source = read(path);
  assert.match(source, /<MenuRow\b/, `${path} builds its rows from MenuRow`);
  assert.doesNotMatch(source, /className=\{SHORTCUT\}/, `${path} shows keys as MenuKey badges`);
  assert.doesNotMatch(source, /["'>]⌘[A-Z,]/, `${path} writes no ⌘ by hand; formatMenuKeys picks the system notation`);
}

// Desktop overlay rows carry the same glyph and key fields and draw them in
// the same order; a check is never in the icon slot.
const bridge = read("lib/desktop/desktop-bridge-types.ts");
assert.match(bridge, /glyph\?: MenuGlyph;/);
assert.match(bridge, /keys\?: MenuKeys;/);
const overlay = read("app/menu-overlay/context-menu/page.tsx");
assert.match(overlay, /if \(isMenuGlyph\(item\.glyph\)\) return <MenuIcon glyph=\{item\.glyph\} \/>;/);
assert.equal(overlay.match(/<ItemKeys item=\{item\} \/>\s*<ItemCheck item=\{item\} \/>/g)?.length, 2, "flat and nested rows end with key then check");

// Every tab menu row has its icon.
const tabMenu = read("components/center-tabs/tab-actions/use-tab-menu.ts");
assert.equal(tabMenu.match(/glyph: "/g)?.length, 8, "every tab menu row has its icon");

// Browser menus are owned by a per-pane useId(), never by the tab id: a web
// tab id carries the page URL, and overlay action ids travel to the desktop
// shell and back.
for (const path of ["components/center-tabs/browser/browser-home-page.tsx", "components/center-tabs/browser/web-tab-pane.tsx"]) {
  assert.match(read(path), /const menuOwnerId = useId\(\)/, `${path} owns its menus by a per-pane id`);
}
for (const path of [
  "components/center-tabs/browser/browser-home-page.tsx",
  "components/center-tabs/browser/pane/desktop-web-tab-pane.tsx",
  "components/center-tabs/browser/pane/iframe-web-tab-pane.tsx",
]) {
  const owners = [...read(path).matchAll(/<(?:BrowserMenu|BookmarkBar)\b[^>]*?ownerId=\{([^}]+)\}/g)].map((match) => match[1]);
  assert.deepEqual(owners, ["menuOwnerId", "menuOwnerId"], `${path} passes menuOwnerId to its BrowserMenu and BookmarkBar`);
}
