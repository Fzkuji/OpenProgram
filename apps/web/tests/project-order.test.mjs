import assert from "node:assert/strict";
import test from "node:test";
import { projectGroups, moveProject } from "../lib/project-groups.ts";
const projects = [
 { id: "d", name: "Default", path: "", is_default: true },
 { id: "a", name: "A", path: "", is_default: false, session_ids: ["a1"] },
 { id: "b", name: "B", path: "", is_default: false, session_ids: ["b1"] },
];
const items = [{id:"d1"},{id:"a1"},{id:"b1"}];
test("project ordering preserves membership and hidden projects", () => {
 const order = moveProject(["d","a","b"], "b", "d", "before");
 assert.deepEqual(order, ["b","d","a"]);
 assert.deepEqual(moveProject(order,"b","a","after"), ["d","a","b"]);
 assert.deepEqual(projectGroups(projects,items,order).map(g=>[g.key,g.items[0].id]), [["b","b1"],["d","d1"],["a","a1"]]);
 assert.deepEqual(projectGroups(projects,[items[1]],order).map(g=>g.key), ["a"]);
 assert.deepEqual(projectGroups(projects,items,["deleted","b"]).map(g=>g.key), ["b","d","a"]);
 assert.deepEqual(moveProject(order,"missing","a","after"),order);
 assert.deepEqual(moveProject(order,"b","b","before"),order);
});
