// A settings read brings the stored effort and web search of the branch a
// pane shows, so a write made elsewhere reaches the pane without a reload.
import assert from 'node:assert/strict';
import test from 'node:test';
import { dirname, join } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { build } from 'esbuild';

import { scratchDir } from "../scratch-dir.mjs";

const root = dirname(fileURLToPath(new URL('../../package.json', import.meta.url)));
const dir = scratchDir(root, "stored-controls-read");
await build({absWorkingDir:root,stdin:{contents:`
export {loadSessionAgentSettings,agentSettingsChanged} from './lib/runtime-bridge/providers';
export {persistBranchSettings,pendingBranchSettings} from './lib/agents/branch-settings';
export {useSessionStore} from './lib/session-store';
export {storedControlsPatch} from './lib/session-store/branch-settings';
`,resolveDir:root},bundle:true,format:'esm',platform:'node',packages:'external',tsconfig:join(root,'tsconfig.json'),outfile:join(dir,'test.mjs')});
const {loadSessionAgentSettings,agentSettingsChanged,persistBranchSettings,pendingBranchSettings,useSessionStore,storedControlsPatch}=await import(pathToFileURL(join(dir,'test.mjs')));

// The focused read also refreshes the legacy badges, which look for DOM nodes.
globalThis.document??={getElementById:()=>null,querySelector:()=>null,querySelectorAll:()=>[]};
const model={provider:'p',model:'m'};
/** One conversation the server has, shown on branch ``a`` with the given controls. */
function shown(id,settings){
 useSessionStore.setState(s=>({conversations:{...s.conversations,[id]:{id,title:id}}}));
 useSessionStore.getState().setComposerSettings({active_branch_id:'a',...settings},id);
}
const controls=id=>{const s=useSessionStore.getState().composerSettingsBySession[id]??{};return [s.thinking,s.webSearch];};
function answer(body,calls=[]){
 globalThis.fetch=async(url,init)=>{calls.push([String(url),init?.method??'GET']);return {ok:true,status:200,json:async()=>body,text:async()=>JSON.stringify(body)};};
 return calls;
}

test('only a stored control that differs from the shown one is patched',()=>{
 assert.deepEqual(storedControlsPatch({thinking:'low',webSearch:true},{thinking_effort:'high',web_search:true}),{thinking:'high'});
 assert.deepEqual(storedControlsPatch({thinking:'high',webSearch:true},{thinking_effort:'high',web_search:false}),{webSearch:false});
 assert.deepEqual(storedControlsPatch({thinking:'',webSearch:true},{thinking_effort:null,web_search:null}),{});
 assert.deepEqual(storedControlsPatch({thinking:'high',webSearch:false},{thinking_effort:null,web_search:null}),{thinking:'',webSearch:true});
});
test('a pane that is not focused takes effort and web search written elsewhere from its own read',async()=>{
 shown('s1',{thinking:'',webSearch:false});
 const calls=answer({branch_id:'a',active_branch_id:'a',chat:model,exec:model,settings:{thinking_effort:'high',web_search:true}});
 await loadSessionAgentSettings('s1');
 assert.match(calls[0][0],/session_id=s1&branch_id=a/);
 assert.deepEqual(controls('s1'),['high',true]);
 assert.equal(useSessionStore.getState().agentSettingsBySession.s1.chat.model,'m');
});
test('the focused conversation takes them from the read the settings frame starts',async()=>{
 shown('s2',{thinking:'low',webSearch:true});
 useSessionStore.setState({currentSessionId:'s2'});
 answer({branch_id:'a',active_branch_id:'a',chat:model,exec:model,settings:{thinking_effort:'max',web_search:false}});
 await agentSettingsChanged();
 assert.deepEqual(controls('s2'),['max',false]);
 useSessionStore.setState({currentSessionId:null});
});
test('a read that answers for another branch changes nothing',async()=>{
 shown('s3',{thinking:'low',webSearch:true});
 answer({branch_id:'b',active_branch_id:'b',chat:model,exec:model,settings:{thinking_effort:'high',web_search:false}});
 await loadSessionAgentSettings('s3');
 assert.deepEqual(controls('s3'),['low',true]);
});
test('a draft the server does not have keeps its own controls',async()=>{
 useSessionStore.getState().setComposerSettings({thinking:'high',webSearch:false},'draft');
 answer({branch_id:null,active_branch_id:null,chat:model,exec:model,settings:{thinking_effort:null,web_search:true}});
 await loadSessionAgentSettings('draft');
 assert.deepEqual(controls('draft'),['high',false]);
});
test('a read that lands while the pane is still saving its own change does not put the old value back',async()=>{
 shown('s4',{thinking:'low',webSearch:true});
 let release;const posted=new Promise(resolve=>{release=resolve;});
 globalThis.fetch=async(url,init)=>{
  if((init?.method??'GET')==='POST'){await posted;const body={branch_id:'a',active_branch_id:'a',settings:{thinking_effort:'high'}};return {ok:true,status:200,json:async()=>body,text:async()=>JSON.stringify(body)};}
  const body={branch_id:'a',active_branch_id:'a',chat:model,exec:model,settings:{thinking_effort:'low',web_search:true}};
  return {ok:true,status:200,json:async()=>body,text:async()=>JSON.stringify(body)};
 };
 useSessionStore.getState().setComposerSettings({thinking:'high'},'s4');
 persistBranchSettings('s4',{thinking:'high'});
 await loadSessionAgentSettings('s4');
 assert.deepEqual(controls('s4'),['high',true]);
 release();await pendingBranchSettings('s4');
});
