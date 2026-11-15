import assert from 'node:assert/strict';
import test from 'node:test';
import {registerHooks} from 'node:module';
import {existsSync,readFileSync} from 'node:fs';
import {fileURLToPath} from 'node:url';
import ts from 'typescript';
import {act,createElement} from 'react';
import {renderToStaticMarkup} from 'react-dom/server';
import {createRoot} from 'react-dom/client';
import {parseHTML} from 'linkedom';
const base=new URL('../../',import.meta.url);
globalThis.__mdOpens=[];globalThis.__mdMath=[];
const mocks={
 '@/lib/i18n':'export const useTranslation=()=>({text:en=>en});',
 '@/lib/runtime-bridge/dom/markdown-render':'export const typesetMath=e=>globalThis.__mdMath.push(e.textContent);',
 '@/lib/tabs/center-tabs-store':'export const useCenterTabs={getState:()=>({openFileTab:(...a)=>globalThis.__mdOpens.push(["tab",...a]),openFilePreview:(...a)=>globalThis.__mdOpens.push(["preview",...a])})};',
 '@/lib/files/files-shared':'export const rawFileUrl=(id,path)=>"/raw?project="+encodeURIComponent(id)+"&path="+encodeURIComponent(path);export const absRawFileUrl=(path,sid)=>"/raw?session="+encodeURIComponent(sid)+"&path="+encodeURIComponent(path);',
};
registerHooks({resolve(s,c,next){
 if(Object.hasOwn(mocks,s))return {url:'data:text/javascript,'+encodeURIComponent(mocks[s]),shortCircuit:true};
 if(s.endsWith('.css'))return {url:'data:text/javascript,export default {}',shortCircuit:true};
 if(s.startsWith('@/')){const b=new URL(s.slice(2),base);for(const ext of ['.ts','.tsx'])if(existsSync(fileURLToPath(b)+ext))return {url:b.href+ext,shortCircuit:true};}
 if(s.startsWith('.')&&!/\.[a-z]+$/.test(s)){const b=new URL(s,c.parentURL);for(const ext of ['.ts','.tsx'])if(existsSync(fileURLToPath(b)+ext))return {url:b.href+ext,shortCircuit:true};}
 return next(s,c);
},load(url,c,next){if(url.endsWith('.tsx'))return {format:'module',source:ts.transpileModule(readFileSync(new URL(url),'utf8'),{compilerOptions:{jsx:ts.JsxEmit.ReactJSX,module:ts.ModuleKind.ESNext,target:ts.ScriptTarget.ES2022}}).outputText,shortCircuit:true};return next(url,c);}});
const {MarkdownPreview}=await import('../../components/files/preview/markdown-preview.tsx');
const {Markdown}=await import('../../lib/format-utils/markdown.tsx');
const props={projectId:'project & one',path:'docs/post.md',sessionId:'session-one'};
const render=(source,extra={})=>parseHTML(renderToStaticMarkup(createElement(MarkdownPreview,{...props,source,...extra}))).document.documentElement;
test('document URI policy rejects executable, encoded and unsupported schemes',()=>{
 for(const href of ['javascript:alert(1)','JaVaScRiPt:alert(1)','javascript&colon;alert(1)','java&#x09;script:alert(1)','vbscript:msgbox(1)','data:text/html,hello','file:///tmp/private','\\\\evil.test/x']){
  const doc=render(`[bad](<${href}>)`);assert.ok(!doc.querySelector('a[href]'),href);
 }
 for(const href of ['https://example.test/a','http://example.test/a','mailto:a@example.test','#section','other%20file.md'])assert.ok(render(`[ok](${href})`).querySelector('a[href]'),href);
 assert.equal(render('[cdn](//example.test/x)').querySelector('a').getAttribute('href'),'https://example.test/x');
});
test('math text never creates HTML and literal code never loses its delimiters',()=>{
 for(const source of ['$<img src=x onerror=bad>$','$$<img src=x onerror=bad>$$','\\(<img src=x onerror=bad>\\)','\\[<img src=x onerror=bad>\\]']){
  const doc=render(source);assert.ok(!doc.querySelector('img'),source);assert.ok(doc.textContent.includes(source),doc.textContent);
 }
 for(const code of ['$<img src=x onerror=bad>$','$$x$$','\\(a & b\\)','%%MATH0%% $&']){
  for(const source of ['`'+code+'`','```\n'+code+'\n```']){const doc=render(source);assert.ok(!doc.querySelector('img'));assert.equal(doc.querySelector('code').textContent.trimEnd(),code,source+' => '+doc.outerHTML);}
 }
});
test('math preserves replacement characters and source authored placeholder text',()=>{
 const text='%%MATH0%% $x & y$ and $$a $& b$$';assert.equal(render(text).querySelector('.md-rendered').textContent.trim(),text);
});
test('images use scoped URLs, retain SVG fragment and never fall back after rejection',()=>{
 const image=render('![icon](figure%20one.svg?session=evil#part%20one)').querySelector('img');assert.equal(image.getAttribute('src'),'/raw?project=project%20%26%20one&path=docs%2Ffigure%20one.svg#part%20one');
 const abs=render('![icon](figure.svg#part)',{abs:true,path:'/allowed/post.md'}).querySelector('img');assert.equal(abs.getAttribute('src'),'/raw?session=session-one&path=%2Fallowed%2Ffigure.svg#part');
 for(const src of ['../../../../secret.png','data:image/svg+xml,evil','data:image/png;base64,AAAA','javascript:evil','file:///tmp/x','mailto:a@b']){
  const doc=render(`![readable alt](<${src}>)`);assert.ok(!doc.querySelector('img[src]'),src);assert.ok(doc.textContent.includes('readable alt'));
 }
 assert.equal(render('![pic](https://example.test/x.png)').querySelector('img').getAttribute('src'),'https://example.test/x.png');
});
test('raw HTML stays literal and normal tables/lists/code still render',()=>{
 const doc=render('<img src=x onerror=bad>\n\n- item\n\n|a|b|\n|-|-|\n|1|2|\n\n`code`');assert.ok(!doc.querySelector('img'));assert.ok(doc.querySelector('li'));assert.ok(doc.querySelector('table'));assert.equal(doc.querySelector('code').textContent,'code');
});
test('untrusted opt-in does not change trusted Markdown behavior',()=>{
 const trusted=renderToStaticMarkup(createElement(Markdown,{source:'<b>raw</b>'}));assert.ok(trusted.includes('<b>raw</b>'));assert.ok(!render('<b>raw</b>').querySelector('b'));
});
test('mounted preview retains owner-scoped file opening and blocks rejected click fallback',async()=>{
 const {document,window}=parseHTML('<html><body></body></html>');Object.assign(globalThis,{document,window,IS_REACT_ACT_ENVIRONMENT:true});globalThis.__mdOpens=[];globalThis.__mdMath=[];
 const host=document.createElement('div');document.body.append(host);const root=createRoot(host);
 try{
 await act(()=>root.render(createElement(MarkdownPreview,{...props,source:'[file](other%20file.md) $x & y$'})));
 let a=host.querySelector('a');const click=new window.Event('click',{bubbles:true,cancelable:true});await act(()=>a.dispatchEvent(click));assert.equal(click.defaultPrevented,true);assert.deepEqual(globalThis.__mdOpens,[['tab',props.projectId,'docs/other file.md']]);assert.ok(globalThis.__mdMath.at(-1).includes('$x & y$'));
 a.setAttribute('href','javascript:bad');const bad=new window.Event('click',{bubbles:true,cancelable:true});await act(()=>a.dispatchEvent(bad));assert.equal(bad.defaultPrevented,true);assert.equal(globalThis.__mdOpens.length,1);
 await act(()=>root.render(createElement(MarkdownPreview,{...props,abs:true,path:'/allowed/post.md',source:'[file](other.md)'})));a=host.querySelector('a');await act(()=>a.dispatchEvent(new window.Event('click',{bubbles:true,cancelable:true})));assert.deepEqual(globalThis.__mdOpens.at(-1),['preview','session-one',{projectId:props.projectId,path:'/allowed/other.md',readOnly:true}]);
 }finally{await act(()=>root.unmount());host.remove();}
});

test('generated link and image attributes cannot create event handlers',()=>{
 const doc=render('[label](<https://example.test/"onmouseover="bad>)\n\n![alt" quote](<https://example.test/"onerror="bad>)');
 assert.ok(doc.querySelector('a'));assert.ok(doc.querySelector('img'));
 assert.ok(!doc.querySelector('[onmouseover]'));assert.ok(!doc.querySelector('[onerror]'));
 assert.equal(doc.querySelector('img').getAttribute('alt'),'alt" quote');
});

 test('token entities decode once across image resolution and file clicks',async()=>{
 const {document,window}=parseHTML('<html><body></body></html>');Object.assign(globalThis,{document,window,IS_REACT_ACT_ENVIRONMENT:true});globalThis.__mdOpens=[];
 const host=document.createElement('div');document.body.append(host);const root=createRoot(host);
 try {
 await act(()=>root.render(createElement(MarkdownPreview,{...props,source:'[File](note&amp;amp;part.md) ![Image](note&amp;amp;part.svg)'})));
 const a=host.querySelector('a');assert.equal(a.getAttribute('href'),'note&amp;part.md');
 await act(()=>a.dispatchEvent(new window.Event('click',{bubbles:true,cancelable:true})));
 assert.deepEqual(globalThis.__mdOpens,[['tab',props.projectId,'docs/note&amp;part.md']]);
 assert.equal(host.querySelector('img').getAttribute('src'),'/raw?project=project%20%26%20one&path=docs%2Fnote%26amp%3Bpart.svg');
 } finally {await act(()=>root.unmount());host.remove();}
});

test('standard named URL entities decode exactly once in renderer and click paths',async()=>{
 const {document,window}=parseHTML('<html><body></body></html>');Object.assign(globalThis,{document,window,IS_REACT_ACT_ENVIRONMENT:true});globalThis.__mdOpens=[];
 const host=document.createElement('div');document.body.append(host);const root=createRoot(host);
 try {
 for(const [source,expected] of [['note&copy;.md','note©.md'],['note&AMP;part.md','note&part.md'],['note&amp;amp;part.md','note&amp;part.md'],['note&notit;.md','note&notit;.md']]){
  await act(()=>root.render(createElement(MarkdownPreview,{...props,source:`[File](${source}) ![Image](${source})`})));
  const a=host.querySelector('a');assert.equal(a.getAttribute('href'),expected);
  await act(()=>a.dispatchEvent(new window.Event('click',{bubbles:true,cancelable:true})));
  assert.deepEqual(globalThis.__mdOpens.at(-1),['tab',props.projectId,'docs/'+expected]);
  assert.equal(host.querySelector('img').getAttribute('src'),'/raw?project=project%20%26%20one&path='+encodeURIComponent('docs/'+expected));
 }
 } finally {await act(()=>root.unmount());host.remove();}
});
test('image alternative text and titles retain standard Markdown entity semantics',()=>{
 const doc=render('[Link](other.md "&AMP; &copy;") ![**Alt** &copy;](image.png "&AMP; &copy;")');
 assert.equal(doc.querySelector('a').title,'& ©');
 assert.equal(doc.querySelector('img').title,'& ©');
 assert.equal(doc.querySelector('img').getAttribute('alt'),'Alt ©');
 const rejected=render('![**Alt** &copy;](data:text/html,evil)');
 assert.equal(rejected.querySelector('.md-rendered').textContent.trim(),'Alt ©');
});
test('Obsidian reading view: title, properties, callouts, highlights, tags and wikilinks',()=>{
 const doc=render('---\ntitle: "Post"\ntags: [a, b]\naliases:\n  - one\n---\n> [!warning]- Careful\n> body **bold**\n\n==hi== #topic [[Other Note#Some Heading|see]] ![[pic.png|200]] [[x<img src=y>]]\n\n`#notag ==no==`');
 assert.equal(doc.querySelector('h1').textContent,'post');
 assert.deepEqual([...doc.querySelectorAll('dt')].map(e=>e.textContent),['title','tags','aliases']);
 assert.deepEqual([...doc.querySelectorAll('dd')[1].querySelectorAll('span')].map(e=>e.textContent),['a','b']);
 assert.equal(doc.querySelectorAll('dd')[2].textContent,'one');
 const callout=doc.querySelector('details.callout');assert.equal(callout.getAttribute('data-callout'),'warning');assert.ok(!callout.hasAttribute('open'));
 assert.equal(callout.querySelector('summary').textContent,'Careful');assert.equal(callout.querySelector('strong').textContent,'bold');
 assert.equal(doc.querySelector('mark').textContent,'hi');assert.equal(doc.querySelector('.tag').textContent,'#topic');
 const link=doc.querySelector('a.internal-link');assert.equal(link.getAttribute('href'),'Other%20Note.md#some-heading');assert.equal(link.textContent,'see');
 const image=doc.querySelector('img');assert.equal(image.getAttribute('src'),'/raw?project=project%20%26%20one&path=docs%2Fpic.png');assert.equal(image.getAttribute('width'),'200');
 assert.equal(doc.querySelectorAll('img').length,1);
 assert.equal(doc.querySelector('code').textContent,'#notag ==no==');
});
