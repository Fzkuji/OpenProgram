import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

const root = new URL("../", import.meta.url);
const source = (path) => readFileSync(new URL(path, root), "utf8");

const projectMenu = source("components/chat/top-bar/project-menu.tsx");
const projectsPage = source("components/projects/projects-page.tsx");
const projectsCss = source("components/projects/projects-page.module.css");
const sessionsList = source("components/sidebar/sessions-list.tsx");
const chatCss = source("app/styles/chat.css");

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
assert.match(projectMenu, /\{list\.map\(/);
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
