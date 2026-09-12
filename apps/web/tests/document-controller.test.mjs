import assert from "node:assert/strict";
import test from "node:test";
import { existsSync } from "node:fs";
import { registerHooks } from "node:module";
import { fileURLToPath } from "node:url";
const root = new URL("../", import.meta.url);
registerHooks({ resolve(specifier, context, nextResolve) {
  const base = specifier.startsWith("@/") ? new URL(specifier.slice(2), root)
    : specifier.startsWith(".") ? new URL(specifier, context.parentURL) : null;
  if (base) for (const suffix of [".ts", ".tsx", "/index.ts"]) {
    const url = `${base.href}${suffix}`;
    if (existsSync(fileURLToPath(url))) return { url, shortCircuit: true };
  }
  return nextResolve(specifier, context);
} });
const { DocumentController } = await import("../lib/state/document-controller.ts");
const { setDraftStoreAdapterForTests } = await import("../lib/state/file-drafts.ts");
const { MemoryDraftStore } = await import("../lib/state/file-draft-store.ts");
setDraftStoreAdapterForTests(new MemoryDraftStore());
const a="a".repeat(64), b="b".repeat(64), c="c".repeat(64);
const committed = revision => new Response(JSON.stringify({ok:true,status:"committed",revision}));
class Records {
  values=new Map(); fail=false;
  async get(key) { return structuredClone(this.values.get(key) ?? null); }
  async put(record) {
    if(this.fail) throw new Error("storage unavailable");
    const old=this.values.get(record.key);
    assert.equal(record.storageVersion,old?.storageVersion??0);
    const version=(old?.storageVersion??0)+1;
    this.values.set(record.key,structuredClone({...record,storageVersion:version}));return version;
  }
  async delete(key,version) {
    if(this.fail) throw new Error("storage unavailable");
    assert.equal(version,this.values.get(key)?.storageVersion??0);
    this.values.delete(key);
  }
}
function controller(fetchImpl,store=new Records(),path="notes.txt") {
  return new DocumentController({projectId:"p",path,fetchImpl,draftStore:store,debounceMs:60000,maxDebounceMs:60000});
}

test("real committed envelope publishes the captured bytes and removes the draft",async()=>{
  const calls=[];const store=new Records();
  const value=controller(async(_url,init)=>{calls.push(init);return committed(b)},store);
  await value.hydrate({bytes:"old",revision:a});value.update("new");await value.flush();
  assert.equal(calls.length,1);assert.equal(await calls[0].body.text(),"new");
  assert.equal(calls[0].headers["x-baseline-revision"],a);
  assert.equal(value.getState().draft,null);assert.equal(store.values.size,0);await value.close();
});

test("an edit while a request is pending publishes against the confirmed revision",async()=>{
  let release,started;const hold=new Promise(r=>release=r), start=new Promise(r=>started=r);const calls=[];
  const value=controller(async(_url,init)=>{calls.push(init);if(calls.length===1){started();await hold;}return committed(calls.length===1?b:c)});
  await value.hydrate({bytes:"old",revision:a});value.update("A");const saving=value.flush();await start;
  value.update("A+B");release();await saving;
  assert.deepEqual(await Promise.all(calls.map(call=>call.body.text())),["A","A+B"]);
  assert.deepEqual(calls.map(call=>call.headers["x-baseline-revision"]),[a,b]);await value.close();
});

test("a failed storage transaction blocks PUT and retry persists before sending",async()=>{
  const store=new Records();let calls=0;const value=controller(async()=>{calls++;return committed(b)},store);
  await value.hydrate({bytes:"old",revision:a});store.fail=true;value.update("draft");
  await assert.rejects(value.flush(),/storage unavailable/);assert.equal(calls,0);
  assert.equal(await value.currentDraft().text(),"draft");store.fail=false;await value.flush();assert.equal(calls,1);await value.close();
});

test("conflict blocks close and retains the draft",async()=>{
  const value=controller(async()=>new Response("conflict",{status:409}));
  await value.hydrate({bytes:"old",revision:a});value.update("draft");await assert.rejects(value.close(),/changed on disk/);
  assert.equal(value.getState().status,"conflict");assert.equal(await value.currentDraft().text(),"draft");
});

test("an uncertain restore survives reconstruction and retries POST with its original identity",async()=>{
  const store=new Records();const posts=[];
  const first=controller(async(url,init)=>{
    if(url.includes("history/content"))return new Response("historic");
    posts.push(init);throw new Error("reply lost");
  },store,"restore.txt");
  await first.hydrate({bytes:"old",revision:a});await assert.rejects(first.restore("v1","before"),/reply lost/);
  assert.equal(posts.length,2);const original=JSON.parse(posts[0].body);
  const calls=[];const second=controller(async(url,init)=>{calls.push({url,init});return committed(b)},store,"restore.txt");
  await second.hydrate({bytes:"historic",revision:b});await second.flush();
  assert.equal(calls.length,1);assert.equal(calls[0].init.method,"POST");
  assert.deepEqual(JSON.parse(calls[0].init.body),original);
  assert.equal(store.values.size,0);assert.equal(await second.getState().snapshot.bytes.text(),"historic");await second.close();
});

test("raw binary content is not exposed as editable UTF-8 text",async()=>{
  const value=controller(async()=>new Response(new Uint8Array([0xff,0x00,0xfe]),{headers:{"x-document-revision":a}}),new Records(),"binary.txt");
  const snapshot=await value.load();assert.equal(snapshot.binary,true);await value.close();
});

test("discard waits for an in-flight write before reading the disk version",async()=>{
  let release,started;const hold=new Promise(r=>release=r),start=new Promise(r=>started=r);
  let disk="old";
  const value=controller(async(_url,init)=>{
    if(init?.method==="PUT"){started();await hold;disk="new";return committed(b);}
    return new Response(disk,{headers:{"x-document-revision":disk==="old"?a:b}});
  },new Records(),"discard.txt");
  await value.hydrate({bytes:"old",revision:a});value.update("new");const saving=value.flush();await start;
  const discarding=value.discardDraft();release();await Promise.all([saving,discarding]);
  assert.equal(await value.getState().snapshot.bytes.text(),"new");await value.close();
});

test("rich editor dirty notification requests an export and stages durable bytes",async()=>{
  const records=new Records(); let saves=0;
  const value=controller(async(_url,init)=>committed(b),records,"office.docx");
  await value.hydrate({bytes:"old",revision:a});
  const editor={getState:()=>({dirty:false}),setReadonly(){},flushPendingSaves:async()=>{},destroy:async()=>{},
    save:async()=>{saves++;await value.stageOfficeExport(new Blob(["office draft"]));return new File(["office draft"],"office.docx");}};
  value.attachRichEditor(editor); value.markRichEditorDirty(true,editor);
  await value.flush();
  assert.equal(saves,1); assert.equal(await value.getState().snapshot.bytes.text(),"office draft");
  await value.close();
});

test("rich close freezes input before final export and reopens it on failure",async()=>{
  const records=new Records(); let frozen=[]; let value;
  value=controller(async()=>new Response("offline",{status:503}),records,"office.xlsx");
  await value.hydrate({bytes:"old",revision:a});
  const editor={setInputEnabled:(enabled)=>frozen.push(enabled),setReadonly(){},flushPendingSaves:async()=>{},destroy:async()=>{},
    save:async()=>{await value.stageOfficeExport(new Blob(["draft"]));return new File(["draft"],"office.xlsx");}};
  value.attachRichEditor(editor); value.markRichEditorDirty(true,editor);
  await assert.rejects(value.close());
  assert.deepEqual(frozen,[false,true]); assert.equal(await value.currentDraft().text(),"draft");
});
