import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

const root = new URL("../", import.meta.url);
const header = readFileSync(new URL("components/files/explorer-header.tsx", root), "utf8");
const files = readFileSync(new URL("components/files/file-tree.tsx", root), "utf8");
const programs = readFileSync(new URL("components/programs/programs-page.tsx", root), "utf8");
const css = readFileSync(new URL("components/files/files-panel.module.css", root), "utf8");
const rightSidebar = readFileSync(new URL("components/right-sidebar/right-sidebar.tsx", root), "utf8");

for (const consumer of [files, programs]) {
  assert.match(consumer, /ExplorerHeader/);
  assert.match(consumer, /ExplorerMatchText/);
}
assert.match(header, /setTimeout\([\s\S]*1500/);
assert.match(header, /Filter/);
assert.match(header, /Highlight/);
assert.match(header, /Fuzzy/);
assert.match(header, /Previous match/);
assert.match(header, /Next match/);
assert.match(header, /copyText/);
assert.match(header, /showRootPath = true/);
assert.match(header, /showRootPath \? \(/);
assert.match(files, /rootPath=\{projectRoot\}/);
assert.doesNotMatch(files, /showRootPath=\{false\}/);
assert.match(programs, /showRootPath=\{false\}/);
assert.doesNotMatch(header, /<code\s+className=\{styles\.treeRootFullPath\}/);
assert.match(css, /\.treeNameMatch/);
assert.match(css, /\.treeKids/);
assert.doesNotMatch(
  css.match(/\.treeRootFullPath\s*\{[^}]*\}/s)?.[0] ?? "",
  /font-family/,
);
assert.match(css, /\.treeBody\s*\{[^}]*padding:\s*8px 0/s);
assert.match(css, /\.treeRow\s*\{[^}]*height:\s*30px/s);
assert.match(css, /\.treeKids\s*>\s*\.treeNode:last-child::before\s*\{[^}]*height:\s*15px/s);
assert.match(css, /\.treeName,\s*\n\.treePath\s*\{[^}]*margin-left:\s*6px/s);
assert.match(rightSidebar, /minWidth:\s*240/);
assert.match(rightSidebar, /defaultWidth:\s*288/);
assert.match(rightSidebar, /FolderOpenIcon/);
assert.match(rightSidebar, /filesIconRef/);
assert.match(rightSidebar, /<FileTree projectId=\{treeProjectId\}\s*\/>/);
assert.doesNotMatch(rightSidebar, /headerExtra=\{/);
assert.doesNotMatch(rightSidebar, /view !== VIEW_FILES/);
assert.doesNotMatch(programs, /react-arborist|react-use-measure/);
assert.doesNotMatch(files, /const filtered = useMemo/);

console.log("shared explorer checks passed");
