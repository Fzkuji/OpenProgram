// Rule: the Settings tabs are a fixed data contract (ids, order, and each tab at
// /settings/<id>), and Settings home is General: /settings redirects there and
// no client code navigates to the bare /settings path.
import assert from "node:assert/strict";
import { readFileSync, readdirSync, statSync } from "node:fs";
import { join, relative } from "node:path";
import { fileURLToPath } from "node:url";

const root = fileURLToPath(new URL("../../", import.meta.url));
const read = (path) => readFileSync(join(root, path), "utf8");

const layout = read("components/settings/settings-tabs-layout.tsx");
const tabsBlock = layout.match(/const tabs = \[([\s\S]*?)\n  \];/)?.[1] ?? "";
const tabs = [...tabsBlock.matchAll(/id: "([^"]+)"[^\n]*?href: "([^"]+)"/g)].map((match) => [match[1], match[2]]);
assert.deepEqual(
  tabs.map(([id]) => id),
  ["general", "appearance", "providers", "memory", "search", "browser", "voice", "environments", "channels", "import", "usage", "system", "versions"],
  "Settings tab ids and order",
);
for (const [id, href] of tabs) assert.equal(href, `/settings/${id}`, `the ${id} tab lives at /settings/${id}`);
assert.match(layout, /return "providers";\s*return "general";/, "an unmatched settings path falls back to General");

assert.match(read("app/(shell)/settings/page.tsx"), /redirect\("\/settings\/general"\)/);

// Desktop often skips the server redirect, so a client entry must name the tab.
const bare = /(?:push|replace|pushPath)\(\s*["']\/settings["']\s*\)|href=["']\/settings["']/;
function walk(dir) {
  for (const name of readdirSync(dir)) {
    const path = join(dir, name);
    if (statSync(path).isDirectory()) walk(path);
    else if (/\.tsx?$/.test(name)) assert.doesNotMatch(readFileSync(path, "utf8"), bare, `${relative(root, path)} navigates to bare /settings`);
  }
}
walk(join(root, "components"));
walk(join(root, "app"));
walk(join(root, "lib"));

console.log("settings tabs checks passed");
