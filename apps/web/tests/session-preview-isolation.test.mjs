import assert from 'node:assert/strict';
import test, {after} from 'node:test';
import {mkdtemp, rm} from 'node:fs/promises';
import {dirname, join} from 'node:path';
import {fileURLToPath, pathToFileURL} from 'node:url';
import {build} from 'esbuild';
const root = dirname(fileURLToPath(new URL('../package.json', import.meta.url)));
const dir = await mkdtemp(join(root, '.preview-isolation-'));
after(() => rm(dir, {recursive:true, force:true}));
await build({absWorkingDir:root, stdin:{contents:`
export {useCenterTabs} from './lib/state/center-tabs-store';
export {useWebTabPip,pipHostMode,collapseWebTabToPip} from './lib/state/web-tab-pip-store';
export {applyFollowPreview} from './lib/state/browser-resource-projection';
export {resetBrowserResources} from './lib/state/session-resources';
export {browserPageInventory} from './lib/desktop-bridge';
export {revealExistingWebTab} from './lib/state/web-page-management';
`,resolveDir:root},bundle:true,format:'esm',platform:'node',packages:'external',tsconfig:join(root,'tsconfig.json'),outfile:join(dir,'probe.mjs')});
const {useCenterTabs:c,useWebTabPip:p,pipHostMode,applyFollowPreview,resetBrowserResources,browserPageInventory,revealExistingWebTab,collapseWebTabToPip}=await import(pathToFileURL(join(dir,'probe.mjs')));
const page = {id:'w:private-a',kind:'web',url:'https://a.example',title:'A page',agentOpened:true,agentSessionId:'A'};
function reset(){
 resetBrowserResources(); p.getState().end();
 c.setState({tabs:[{id:'s:A',kind:'session',sessionId:'A',title:'A'}, {...page}],activeId:'s:A',groups:[],splitWebTabId:null});
 p.getState().show(page.id,'s:A');
}
const visible=()=>pipHostMode(p.getState().tabId,p.getState().ownerTabId,c.getState());
test('same tab conversation navigation synchronously hides the previous preview and back restores it',()=>{
 reset(); assert.equal(visible(),'chat'); c.getState().openSessionTab('B','B');
 assert.equal(visible(),null, 'old Page must disappear before projection effects');
 applyFollowPreview('B',c.getState().tabs); assert.equal(visible(),null);
 c.getState().navigateSessionHistory(-1); applyFollowPreview('A',c.getState().tabs);
 assert.equal(p.getState().tabId,page.id); assert.equal(visible(),'chat');
 c.getState().navigateSessionHistory(1); applyFollowPreview('B',c.getState().tabs); assert.equal(visible(),null);
});
test('each conversation restores its own selected Page in the same reusable tab',()=>{
 reset(); c.getState().openSessionTab('B','B');
 c.setState({tabs:[...c.getState().tabs,{...page,id:'w:private-b',agentSessionId:'B'}]});
 p.getState().show('w:private-b','s:A');
 c.getState().navigateSessionHistory(-1); applyFollowPreview('A',c.getState().tabs);
 assert.equal(p.getState().tabId,page.id); assert.equal(visible(),'chat');
 c.getState().navigateSessionHistory(1); applyFollowPreview('B',c.getState().tabs);
 assert.equal(p.getState().tabId,'w:private-b'); assert.equal(visible(),'chat');
});
test('private inventory is scoped by requesting conversation, public opening exposes the exact Page',async()=>{
 reset(); const inspected=[];
 const bridge={windowId:'main',webTab:{inspect:async id=>{inspected.push(id);return {target_id:'target:'+id,url:page.url,title:page.title};}}};
 assert.equal((await browserPageInventory(bridge,'B')).pages.length,0);
 assert.deepEqual(inspected,[], 'private Page must not be inspected for another session');
 assert.equal((await browserPageInventory(bridge,'A')).pages[0].tab_id,page.id);
 assert.equal((await browserPageInventory(bridge)).pages.length,0);
 revealExistingWebTab(page.id,c.getState());
 assert.equal((await browserPageInventory(bridge,'B')).pages[0].tab_id,page.id);
 assert.equal(c.getState().tabs.filter(t=>t.kind==='web').length,1);
});
test('inventory rechecks public access after asynchronous native inspection',async()=>{
 reset(); revealExistingWebTab(page.id,c.getState()); let finish;
 const pending=browserPageInventory({windowId:'main',webTab:{inspect:()=>new Promise(resolve=>{finish=resolve;})}},'B');
 c.getState().setWebTabPinned(page.id,false); finish({target_id:'target-a',url:page.url,title:page.title});
 assert.equal((await pending).pages.length,0);
});
test('collapse returns to the recorded conversation after its tab has navigated elsewhere',()=>{
 reset(); c.getState().openSessionTab('B','B'); revealExistingWebTab(page.id,c.getState());
 assert.equal(collapseWebTabToPip(page.id),true);
 assert.equal(c.getState().tabs.find(t=>t.id===c.getState().activeId).sessionId,'A'); assert.equal(visible(),'chat');
});

test('inventory filters completed Pages again when other native inspections finish later',async()=>{
 reset(); revealExistingWebTab(page.id,c.getState());
 c.setState({tabs:[...c.getState().tabs,{id:'w:slow',kind:'web',title:'Slow',url:'https://slow.example'}]});
 let finish;
 const pending=browserPageInventory({windowId:'main',webTab:{inspect:id=>id===page.id
   ? Promise.resolve({target_id:'target-a',url:page.url,title:page.title})
   : new Promise(resolve=>{finish=resolve;})}},'B');
 await Promise.resolve();
 c.getState().setWebTabPinned(page.id,false);
 finish({target_id:'target-slow',url:'https://slow.example',title:'Slow'});
 assert.deepEqual((await pending).pages.map(item=>item.tab_id),['w:slow']);
});
