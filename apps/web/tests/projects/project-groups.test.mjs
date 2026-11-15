import assert from "node:assert/strict";
import { test } from "node:test";

import { projectGroups } from "../../lib/projects/project-groups.ts";

const projects = [
  { id: "default", name: "Home", path: "/home/tester", is_default: true, session_ids: [] },
  { id: "zeta", name: "Zeta", path: "/tmp/zeta", is_default: false, session_ids: ["shared"] },
  { id: "alpha", name: "Alpha", path: "/tmp/alpha", is_default: false, session_ids: ["alpha-chat", "shared"] },
  { id: "empty", name: "Empty", path: "/tmp/empty", is_default: false, session_ids: [] },
];
const sessions = [
  { id: "unclaimed", title: "Fallback" },
  { id: "alpha-chat", title: "Alpha chat" },
  { id: "shared", title: "First registry claim wins" },
];
const shape = (groups) => groups.map((group) => [group.key, group.items.map((item) => item.id)]);

test("unclaimed chats go to the default project, the first claim wins, and empty groups stay hidden", () => {
  assert.deepEqual(shape(projectGroups(projects, sessions)), [
    ["default", ["unclaimed"]],
    ["alpha", ["alpha-chat"]],
    ["zeta", ["shared"]],
  ]);
});

test("a filtered list keeps only the groups that still have chats", () => {
  assert.deepEqual(projectGroups(projects, [sessions[1]]).map((group) => group.key), ["alpha"]);
  assert.deepEqual(projectGroups(projects, []), []);
});

test("grouping does not reorder the registry it was given", () => {
  projectGroups(projects, sessions);
  assert.deepEqual(projects.map((project) => project.id), ["default", "zeta", "alpha", "empty"]);
});
