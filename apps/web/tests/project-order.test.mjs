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

test("activity ordering and pins follow new chats without changing membership", () => {
 const timed = [{id:"a1",updated_at:10},{id:"b1",updated_at:20},{id:"d1",created_at:5}];
 const keys = (rows,options) => projectGroups(projects,rows,[],options).map(g=>g.key);
 assert.deepEqual(keys(timed,{sort:"recency"}),["b","a","d"]);
 assert.deepEqual(keys([...timed,{id:"new",updated_at:30}],{sort:"recency"}),["d","b","a"]);
 assert.deepEqual(keys(timed,{sort:"oldest"}),["d","a","b"]);
 assert.deepEqual(keys(timed,{sort:"recency",pinned:["d"]}),["d","b","a"]);
 assert.deepEqual(keys([timed[0],timed[2]],{sort:"recency",activityItems:[...timed,{id:"new",updated_at:30}]}),["d","a"]);
});

test("manual ordering includes empty projects when computing drag positions", () => {
 const full = projectGroups(projects,[{id:"d1"},{id:"b1"}],["d","a","b"],{sort:"manual",includeEmpty:true}).map(g=>g.key);
 assert.deepEqual(full,["d","a","b"]);
 assert.deepEqual(moveProject(full,"d","b","after"),["a","b","d"]);
});
