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

test('hidden projects retain ownership and renamed projects filter by ID',async()=>{
  const {filterProjectItems}=await import('../lib/project-groups.ts');
  const projects=[{id:'home',name:'Home',path:'/home',is_default:true},{id:'p',name:'Renamed',path:'/p',is_default:false,hidden:true,session_ids:['a']}];
  const items=[{id:'a',project:'Old name'},{id:'other'}];
  assert.deepEqual(projectGroups(projects,items).map(p=>[p.key,p.items.map(i=>i.id)]),[['home',['other']]]);
  assert.deepEqual(filterProjectItems(projects,items,'p'),[items[0]]);
  assert.deepEqual(filterProjectItems(projects,items,'home'),[items[1]]);
});


test('removing and restoring a project preserves its manual position through other drags',()=>{
  const projects=['a','b','c'].map(id=>({id,name:id,path:'/'+id,is_default:false,hidden:id==='a'}));
  const order=projectGroups(projects,[],['a','b','c'],{sort:'manual',includeEmpty:true,includeHidden:true}).map(p=>p.key);
  const moved=moveProject(order,'c','b','before');
  assert.deepEqual(moved,['a','c','b']);
  assert.deepEqual(projectGroups(projects.map(p=>({...p,hidden:false})),[],moved,{sort:'manual',includeEmpty:true}).map(p=>p.key),['a','c','b']);
});

 test("sidebar omits empty and archived-only projects, including pins, without mutating registry", () => {
 const registry=[{id:"p",name:"P",path:"/p",is_default:false,session_ids:["s"]},{id:"empty",name:"Empty",path:"/empty",is_default:false}];
 assert.deepEqual(projectGroups(registry,[{id:"s",archived:true}],[],{pinned:["p","empty"]}),[]);
 assert.deepEqual(projectGroups(registry,[{id:"s",archived:false}]).map(g=>g.key),["p"]);
 assert.equal(registry.length,2);
 assert.deepEqual(projectGroups(registry,[],[],{includeEmpty:true}).map(g=>g.key).sort(),["empty","p"]);
 });
