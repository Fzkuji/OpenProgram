import assert from "node:assert/strict";
import {readFileSync} from "node:fs";
import {registerHooks} from "node:module";
import test from "node:test";
import ts from "typescript";
const loaded=new Set();
registerHooks({resolve(s,c,next){if(s.startsWith('.')&&!/\.[a-z]+$/.test(s))return next(s+'.ts',c);return next(s,c);},load(url,c,next){
 const name=url.match(/@dicebear\/([^/]+)\//)?.[1];if(name&&name!=='core')loaded.add(name);
 if(url.endsWith('.tsx'))return {format:'module',shortCircuit:true,source:ts.transpileModule(readFileSync(new URL(url),'utf8'),{compilerOptions:{jsx:ts.JsxEmit.ReactJSX,module:ts.ModuleKind.ESNext,target:ts.ScriptTarget.ES2022}}).outputText};return next(url,c);
}});
const {Avatar}=await import('../../components/avatar/avatar.tsx');
const {createElement}=await import('react');
const {renderToStaticMarkup}=await import('react-dom/server');
test('Avatar public import loads only the default generator',()=>assert.deepEqual([...loaded],['shapes']));
test('letter and uploaded avatars retain their direct render paths',()=>{
 assert.match(renderToStaticMarkup(createElement(Avatar,{name:'User',config:{kind:'letter',letter:'U'}})),/>U<\/span>/);
 assert.match(renderToStaticMarkup(createElement(Avatar,{name:'User',config:{kind:'upload',file:'data:image/png;base64,AA'}})),/<img/);
});
const {STYLES,loadAvatarStyle,loadedAvatarStyle}=await import('../../components/avatar/styles.ts');
test('optional generators share a pending load and all advertised styles remain usable',async()=>{
 const first=loadAvatarStyle('bottts');
 assert.equal(loadAvatarStyle('bottts'),first);
 await first;
 assert.ok(loadedAvatarStyle('bottts'));
 for(const style of Object.keys(STYLES))assert.ok(await loadAvatarStyle(style));
});
test('a failed optional style load can be retried',async()=>{
 const original=STYLES.pixelArt;
 // Use a fresh module so this test has no warmed generator entries.
 const fresh=await import('../../components/avatar/styles.ts?retry');
 const loader=fresh.STYLES.pixelArt;
 fresh.STYLES.pixelArt=()=>Promise.reject(new Error('offline'));
 await assert.rejects(fresh.loadAvatarStyle('pixelArt'),/offline/);
 fresh.STYLES.pixelArt=loader;
 assert.ok(await fresh.loadAvatarStyle('pixelArt'));
 assert.ok(original);
});
