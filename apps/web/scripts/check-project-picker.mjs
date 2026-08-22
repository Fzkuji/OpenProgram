import assert from "node:assert/strict";

import { readChatCss } from "./_chat-css.mjs";
import { readFileSync } from "node:fs";

const root = new URL("../", import.meta.url);
const source = (path) => readFileSync(new URL(path, root), "utf8");

const projectMenu = source("components/chat/top-bar/project-menu.tsx");
const workingDirs = source("components/chat/top-bar/working-dir-chips.tsx");
const fileTree = source("components/files/file-tree.tsx");
const explorerHeader = source("components/files/explorer-header.tsx");
const explorerSearch = source("components/files/explorer-search.ts");
const fileTreeCss = source("components/files/files-panel.module.css");
const projectsPage = source("components/projects/projects-page.tsx");
const projectsCss = source("components/projects/projects-page.module.css");
const sessionsList = source("components/sidebar/sessions-list.tsx");
const fnFormSubmit = source(
  "components/chat/composer/modes/fn-form/use-fn-form-submit.ts",
);
const workflowSource = source(
  "../../openprogram/programs/workflow/auto_workflow.py",
);
const chatCss = readChatCss(root);

assert.doesNotMatch(projectMenu, /project-caret/);
assert.doesNotMatch(chatCss, /project-caret/);
assert.doesNotMatch(projectMenu, /\bisDefault\b/);
assert.doesNotMatch(projectMenu, /\bXIcon\b/);
assert.doesNotMatch(projectMenu, /\bremoveProject\b/);
assert.doesNotMatch(projectMenu, /remove_project/);
assert.doesNotMatch(projectMenu, /Remove from list|从列表移除/);
assert.match(projectMenu, /<PopoverTrigger asChild>[\s\S]*id="projectBadge"/);
assert.match(projectMenu, /<Check\b/);
assert.match(projectMenu, /Open folder…/);
assert.match(projectMenu, /const created = await wsRequest/);
assert.match(projectMenu, /created\?\.ok && created\.project\?\.id/);
assert.match(projectMenu, /setPendingProject\(activeChatKey, created\.project\.id\)/);
assert.match(workingDirs, /pendingProjectsByChat\[activeChatKey\]/);
assert.match(workingDirs, /pendingProjectId \?\? currentProjectId/);
assert.match(
  fnFormSubmit,
  /if \(pendingProjectId\) body\.project_id = pendingProjectId/,
  "a direct workflow call must bind the selected Project before execution",
);
assert.match(
  workflowSource,
  /@agentic_function\([\s\S]*?input=\{[\s\S]*?"task"[\s\S]*?def auto_workflow\(task: str\)/,
  "auto_workflow must expose only its task parameter",
);
assert.match(explorerHeader, /className=\{styles\.treeRootPath\}/);
assert.match(explorerHeader, /styles\.treeToolbar\b/);
assert.match(fileTree, /baseOf\(projectRoot\)/);
assert.match(explorerHeader, /aria-expanded=\{searchOpen\}/);
assert.match(explorerHeader, /aria-hidden=\{!searchOpen\}/);
assert.match(explorerHeader, /searchRef\.current\?\.focus\(\)/);
assert.match(explorerHeader, /event\.key === "Escape"\) closeSearch\(\)/);
assert.match(fileTreeCss, /\.treeHeader\s*\{[^}]*flex-direction:\s*column/s);
assert.match(fileTreeCss, /\.treeRootPath\s*\{/);
assert.match(fileTreeCss, /\.treeToolbar\s*\{/);
assert.match(fileTreeCss, /\.treeSearchRow\s*\{/);
assert.match(explorerSearch, /export const EXPLORER_BASE_PAD = 16/);
assert.match(fileTree, /const TREE_BASE_PAD = EXPLORER_BASE_PAD/);
assert.match(fileTree, /const TREE_LABEL_OFFSET = 44/);
assert.match(explorerSearch, /export const EXPLORER_INDENT = 27/);
assert.match(fileTree, /const INDENT = EXPLORER_INDENT/);
assert.match(fileTree, /paddingLeft: TREE_BASE_PAD \+ depth \* INDENT/);
assert.match(fileTree, /TREE_BASE_PAD \+ 8 \+ depth \* INDENT/);
assert.doesNotMatch(fileTree, /\bROW_PAD\b|\bFILE_PAD\b/);
assert.doesNotMatch(fileTree, /ChevronRight|chevronSlot|styles\.chevron/);
assert.match(fileTree, /<FolderOpen size=\{15\} className=\{styles\.treeIconFolder\}/);
assert.match(fileTreeCss, /\.treeHeader\s*\{[^}]*padding:\s*6px 8px/s);
assert.match(fileTreeCss, /\.treeRootPath\s*\{[^}]*height:\s*36px[^}]*gap:\s*10px/s);
assert.match(
  fileTreeCss,
  /\.treeRow\s*\{[^}]*grid-template-columns:\s*17px minmax\(0, 1fr\)/s,
);
assert.match(fileTreeCss, /\.treeKids > \.treeNode::before/);
assert.match(
  fileTreeCss,
  /\.treeKids > \.treeNode::before\s*\{[^}]*z-index:\s*1/s,
  "tree connector rails must paint above selected and hover row backgrounds",
);
assert.match(fileTreeCss, /\.treeKids > \.treeNode:last-child::before/);
assert.match(fileTreeCss, /\.treeKids > \.treeNode > \.treeRow::before/);
assert.match(
  fileTreeCss,
  /\.treeKids > \.treeNode > \.treeRow::before\s*\{[^}]*width:\s*20px/s,
);
assert.match(fileTreeCss, /\.treeName,[\s\S]*\.treePath\s*\{[^}]*margin-left:\s*6px/);
assert.match(
  projectMenu,
  /\{list\.map\(\(p\) => \{/,
  "missing-directory projects stay visible in the draft picker",
);
assert.match(
  projectMenu,
  /p\.path_missing \? locateFolder\(p\.id\) : switchTo\(p\.id\)/,
  "missing draft-picker items locate the folder instead of selecting it",
);
assert.match(projectsPage, /const locateProject = useCallback/);
assert.match(projectsPage, /Locate folder…/);
assert.doesNotMatch(projectMenu, /filter\([^\n]*session_count/);

// Main directory freezes on the first turn: an active session (one with a
// session_id) must not render the switching list, and the only path that
// changes its directory is the relocate repair.
assert.match(projectMenu, /const frozen = sessionId !== null/);
assert.match(projectMenu, /if \(frozen\) \{/);
assert.match(projectMenu, /"relocate_project"/);
assert.match(projectMenu, /Locate folder…/);
// Missing-directory warning uses the lucide icon, never an emoji glyph.
assert.match(projectMenu, /<AlertTriangle\b/);
assert.doesNotMatch(projectMenu, /[⚠❗🚨📁]/u);
assert.match(projectMenu, /path_missing/);
assert.match(chatCss, /\.project-badge-missing\b/);

assert.doesNotMatch(projectsPage, /\bremoveProject\b/);
assert.doesNotMatch(projectsPage, /remove_project/);
assert.doesNotMatch(projectsPage, /Remove from list|从列表移除/);
assert.doesNotMatch(projectsPage, /styles\.removeBtn/);
assert.doesNotMatch(projectsCss, /\.removeBtn\b/);
assert.match(projectsPage, /\{filtered\.map\(/);
assert.match(projectsPage, /<ProjectConfigSection\b/);
assert.match(projectsPage, /"list_project_sessions"/);

assert.match(
  sessionsList,
  /import\s*\{\s*projectGroups\s*\}\s*from\s*"@\/lib\/project-groups"/,
);
assert.match(sessionsList, /projectGroups\(projects, visible\)/);

const { projectGroups } = await import("../lib/project-groups.ts");

const projects = [
  {
    id: "default",
    name: "Home",
    path: "/home/tester",
    is_default: true,
    session_ids: [],
  },
  {
    id: "zeta",
    name: "Zeta",
    path: "/tmp/zeta",
    is_default: false,
    session_ids: ["shared"],
  },
  {
    id: "alpha",
    name: "Alpha",
    path: "/tmp/alpha",
    is_default: false,
    session_ids: ["alpha-chat", "shared"],
  },
  {
    id: "empty",
    name: "Empty",
    path: "/tmp/empty",
    is_default: false,
    session_ids: [],
  },
];
const sessions = [
  { id: "unclaimed", title: "Fallback" },
  { id: "alpha-chat", title: "Alpha chat" },
  { id: "shared", title: "First registry claim wins" },
];

assert.deepEqual(
  projectGroups(projects, sessions).map((group) => [
    group.key,
    group.items.map((item) => item.id),
  ]),
  [
    ["default", ["unclaimed"]],
    ["alpha", ["alpha-chat"]],
    ["zeta", ["shared"]],
  ],
  "empty project groups must stay hidden even without a narrowing filter",
);
assert.deepEqual(
  projectGroups(projects, [sessions[1]]).map((group) => group.key),
  ["alpha"],
  "filtered project groups must contain only matching non-empty groups",
);
assert.deepEqual(projectGroups(projects, []), []);
assert.deepEqual(
  projects.map((project) => project.id),
  ["default", "zeta", "alpha", "empty"],
  "grouping must not reorder the project registry input",
);

console.log("project-picker checks passed");
