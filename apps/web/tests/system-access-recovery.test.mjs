import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';
import { transformSync } from 'esbuild';
import { systemAccessAction } from '../lib/system-access-action.ts';
import { systemAccessRequired } from '../lib/system-access-result.ts';
const source = transformSync(readFileSync(new URL('../components/chat/messages/system-access-recovery.tsx',import.meta.url),'utf8'),{loader:'tsx',format:'cjs',jsx:'automatic'}).code;
const output={status:'infeasible',reason_code:'system_access_required',system_access:[{id:'screen_recording',status:'not_granted'}]};
function harness(autoOpen, responses) {
 let index=0, dirty=true, mounted=true, calls=[], resumed=0, view;
 const slots=[], effects=[], timers=new Set(), listeners=new Map();
 const react={
  useState(initial){const i=index++;if(!(i in slots))slots[i]=typeof initial==='function'?initial():initial;return [slots[i],v=>{const next=typeof v==='function'?v(slots[i]):v;if(next!==slots[i]){slots[i]=next;dirty=true;}}];},
  useRef(initial){const i=index++;return slots[i]??(slots[i]={current:initial});},
  useEffect(fn,deps){const i=index++;const old=slots[i];if(!old || deps.some((v,j)=>!Object.is(v,old.deps[j]))){slots[i]={deps,cleanup:old?.cleanup};effects.push(()=>{slots[i].cleanup?.();slots[i].cleanup=fn();});}}
 };
 const events={addEventListener:(k,v)=>listeners.set(k,v),removeEventListener:(k,v)=>{if(listeners.get(k)===v)listeners.delete(k);}};
 const document={visibilityState:'visible',...events};
 const window={location:{hostname:'localhost'},...events,setInterval:fn=>{timers.add(fn);return fn;},clearInterval:fn=>timers.delete(fn)};
 let text=(en)=>en;
 const module={exports:{}};
 vm.runInNewContext(source,{module,exports:module.exports,AbortController,document,window,
  fetch:async(url,options={})=>{calls.push([url,options.method||'GET']);const next=responses.shift();if(!next)throw new Error('Unexpected request');return typeof next==='function'?next():next;},
  require:name=>name==='react'?react:name==='react/jsx-runtime'?{jsx:(type,props)=>({type,props}),jsxs:(type,props)=>({type,props})}:name.includes('i18n')?{useTranslation:()=>({text})}:name.includes('system-access-action')?{systemAccessAction}:{systemAccessRequired}
 });
 const Component=module.exports.SystemAccessRecovery;
 async function flush(){for(let n=0;n<50;n++){if(dirty&&mounted){dirty=false;index=0;view=Component({output,autoOpen,onContinue:()=>resumed++});while(effects.length)effects.shift()();}await new Promise(resolve=>setImmediate(resolve));if(!dirty&&!effects.length)return;}throw new Error('Recovery did not settle');}
 return {flush,language:async()=>{text=(en)=>en;dirty=true;await flush();},poll:async()=>{for(const fn of timers)fn();await flush();},unmount:()=>{mounted=false;for(const slot of slots)slot?.cleanup?.();},get calls(){return calls;},get resumed(){return resumed;},get view(){return view;},get timers(){return timers.size;}};
}
const grant=()=>({ok:true,json:async()=>({capabilities:[{id:'screen_recording',status:'granted'}]})});
test('actual recovery retries a failed final check after a later successful poll exactly once',async()=>{
 const h=harness(true,[grant(),{ok:false,status:503},grant(),grant(),grant()]);
 await h.flush();assert.equal(h.resumed,0);
 await h.poll();assert.equal(h.resumed,1);
 await h.poll();assert.equal(h.resumed,1);
 assert.equal(h.calls.some(([,method])=>method==='POST'),false);
 h.unmount();assert.equal(h.timers,0);
});
test('historical mounted recovery never resumes and renders no Continue action',async()=>{
 const h=harness(false,[grant()]);await h.flush();assert.equal(h.resumed,0);assert.equal(h.timers,0);
 const rendered=JSON.stringify(h.view);assert.equal(rendered.includes('Continue task'),false);assert.equal(rendered.includes('section'),false);
 h.unmount();
});
test('unmount during the final check does not dispatch a retry',async()=>{
 let release;const pending=new Promise(resolve=>release=resolve);
 const h=harness(true,[grant(),()=>pending]);await h.flush();h.unmount();release(grant());await h.flush();assert.equal(h.resumed,0);assert.equal(h.timers,0);
});

test('changing language during final check does not consume continuation',async()=>{
 let release;const pending=new Promise(resolve=>release=resolve);
 const h=harness(true,[grant(),()=>pending,grant(),grant()]);await h.flush();
 await h.language();release(grant());await h.flush();assert.equal(h.resumed,1);h.unmount();
});
