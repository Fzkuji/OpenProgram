import assert from "node:assert/strict";
import test from "node:test";
import { registerHooks } from "node:module";
registerHooks({resolve(specifier, context, next) {
 if (specifier.startsWith(".") && !/\.[a-z]+$/.test(specifier)) return {url:new URL(specifier+".ts",context.parentURL).href,shortCircuit:true};
 return next(specifier,context);
}});
const {setNavigate}=await import("../../lib/nav/navigate.ts");
const route=await import("../../lib/tabs/navigation/route.ts");
const requested=[];
globalThis.window={location:{pathname:"/s/chat"},history:{state:null,pushState(_s,_t,path){window.location.pathname=path;},replaceState(state,_t,path){assert.equal(state,null,"Next must observe the replacement instead of bypassing its patched history API");window.location.pathname=path;}}};
setNavigate(path=>requested.push(path));
test("saved provider detail loads exported host once before restoring detail",()=>{
 route.navigateTabRoute("/settings/providers/ollama");
 route.navigateTabRoute("/settings/providers/ollama");
 assert.deepEqual(requested,["/settings/providers"]);
 window.location.pathname="/settings/providers";
 assert.equal(route.completeTabRouteNavigation("/settings/providers"),true);
 assert.equal(window.location.pathname,"/settings/providers/ollama");
 assert.equal(route.completeTabRouteNavigation("/settings/providers/ollama"),false);
});

test("same-host details stay shallow and encoded identifiers remain intact",()=>{
 requested.length=0;
 route.navigateTabRoute("/settings/providers/custom%20local");
 assert.deepEqual(requested,[]);
 assert.equal(window.location.pathname,"/settings/providers/custom%20local");
});
test("an unrelated committed route cancels pending restoration",()=>{
 window.location.pathname="/chat";
 route.navigateTabRoute("/skills/example");
 window.location.pathname="/settings/general";
 assert.equal(route.completeTabRouteNavigation("/settings/general"),false);
 window.location.pathname="/skills";
 assert.equal(route.completeTabRouteNavigation("/skills"),true);
 assert.equal(requested.at(-1),"/settings/general");
 window.location.pathname="/settings/general";
});

test("late provider host cannot reverse a newer chat activation",()=>{
 window.location.pathname="/s/chat";
 route.navigateTabRoute("/settings/providers/ollama");
 route.cancelTabRouteNavigation("/s/chat");
 window.location.pathname="/settings/providers";
 assert.equal(route.completeTabRouteNavigation("/settings/providers"),true);
 assert.equal(window.location.pathname,"/s/chat");
});

test("history returning to chat cancels the old provider host",()=>{
 window.location.pathname="/s/chat";
 route.navigateTabRoute("/settings/providers/ollama");
 route.navigateTabRoute("/s/chat");
 window.location.pathname="/settings/providers";
 assert.equal(route.completeTabRouteNavigation("/settings/providers"),true);
 assert.equal(window.location.pathname,"/s/chat");
});
test("multiple outstanding hosts retain the latest detail in either commit order",()=>{
 for(const order of [["/settings/providers","/skills"],["/skills","/settings/providers"]]) {
  window.location.pathname="/s/chat";
  route.navigateTabRoute("/settings/providers/ollama");
  route.navigateTabRoute("/skills/example");
  for(const host of order) {
   window.location.pathname=host;
   assert.equal(route.completeTabRouteNavigation(host),true);
  }
  if (window.location.pathname !== "/skills/example") {
   assert.equal(requested.at(-1),"/skills");
   window.location.pathname="/skills";
   assert.equal(route.completeTabRouteNavigation("/skills"),true);
  }
  assert.equal(window.location.pathname,"/skills/example");
 }
});

test("a new chat from a session URL moves the URL to /chat at once, without a router push",()=>{
 requested.length=0;
 window.location.pathname="/s/old";
 route.showNewChatRoute();
 assert.equal(window.location.pathname,"/chat");
 assert.deepEqual(requested,[],"a Next transition would leave /s/old in the address bar until it commits");
 route.showNewChatRoute();
 assert.equal(window.location.pathname,"/chat");
 window.location.pathname="/settings/general";
 route.showNewChatRoute();
 assert.deepEqual(requested,["/chat"],"a non-chat page still loads the chat page through the router");
});
