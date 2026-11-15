// Rewind's requests: a check that finds nothing rewinds straight away, a
// confirmed plan is applied with the chosen mode's hash, the composer is
// refilled, and Undo takes the refill back out.
import assert from "node:assert/strict";
import { beforeEach, test } from "node:test";
import { installLoader, STUBS } from "../_support/load.mjs";

const state = globalThis.__rewind = { toasts: [], sent: [], store: null };
installLoader({ stubs: {
  "@/lib/i18n": STUBS.i18n,
  "@/lib/session-store": "const st=()=>globalThis.__rewind.store; export const useSessionStore=Object.assign(sel=>sel(st()),{getState:st});",
  "@/lib/format-utils/toast": "export const showToast=(m,o)=>globalThis.__rewind.toasts.push({m,o}); export const dismissToast=k=>globalThis.__rewind.dismissed.push(k);",
  "@/lib/files/files-shared": "export const absRawFileUrl=(p)=>'/raw'+p;",
  "@/lib/runtime-bridge/state": "export const runtimeState={};",
  "@/lib/runtime-bridge/session/session-load": "export const sendRuntimeCommand=p=>{globalThis.__rewind.sent.push(p);return true;};",
  "@/lib/chat/copy-conversation": "export const copyConversation=async()=>null;",
  // [[name@path]] stands for an attachment marker and <rec/> for a recording.
  "@/components/chat/messages/attachments/user-attachments": `export const parseAttachments=(t,raw)=>{
    const attachments=[];const recordings=[];
    let text=(t||'').replace(/<rec\\/>/g,m=>{recordings.push({id:'r',...(raw?{raw:m}:{})});return '';});
    text=text.replace(/\\[\\[(.*?)@(.*?)\\]\\]/g,(m,filename,path)=>{attachments.push({filename,path,...(raw?{raw:m}:{})});return '';});
    return {attachments,text:text.trim(),recordings};};`,
} });
const actions = await import("../../lib/chat/rewind/rewind-actions.ts");
const { forgetRefill, lastRefill, refillItemIds, useRewindStore } = await import("../../lib/chat/rewind/rewind-store.ts");

let answers;
let posts;
let files;
let failing = {};
const tick = () => new Promise(r => setTimeout(r, 0));

beforeEach(() => {
  answers = {};
  posts = [];
  files = {};
  lastRefill.clear();
  Object.assign(state, { toasts: [], dismissed: [], sent: [], store: {
    conversations: {},
    runningTasks: {}, wsStatus: "open", composerDrafts: {}, messageOrder: {}, rowsByView: {},
    focusComposer() {},
    setComposerInputFor(sid, text) { state.store.composerDrafts[sid] = text; },
  } });
  useRewindStore.setState({ flow: null, picker: null, marks: {}, uploading: {}, refillFiles: {}, retractRefills: {} });
  globalThis.fetch = async (url, init) => {
    if (url.startsWith("/raw")) {
      const file = files[url];
      if (file?.wait) await file.wait;
      return file ? { ok: true, blob: async () => new Blob(["x"], { type: "image/png" }) } : { ok: false, statusText: "gone" };
    }
    if (failing[url]) throw new Error("connection lost");
    posts.push({ url, body: JSON.parse(init.body) });
    const answer = answers[url] ?? { status: 200, data: {} };
    return { ok: answer.status < 400, status: answer.status, json: async () => answer.data };
  };
});

const reply = data => ({ status: 200, data });
const plan = extra => reply({ status: "ready", plan_hashes: { code_and_conversation: "cc", conversation: "c", code: "f" }, idempotency_key: "k", ...extra });

test("nothing to restore rewinds the conversation with no dialog", async () => {
  answers["/api/chat/rewind_plan"] = plan({ has_changes: false });
  answers["/api/chat/rewind"] = reply({ status: "committed", user_text: "hello", marks: [{ edge_id: "e", variant: "pending" }] });
  await actions.requestRewind("S", "u1", "hello");
  await tick();
  assert.equal(useRewindStore.getState().flow, null);
  assert.deepEqual(posts[1].body, { session_id: "S", msg_id: "u1", plan_hash: "c", idempotency_key: "k", mode: "conversation" });
  assert.equal(state.store.composerDrafts.S, "hello");
  assert.deepEqual(state.sent, [{ action: "load_session", session_id: "S" }]);
  assert.equal(useRewindStore.getState().marks.S[0].edge_id, "e");
});

test("the chosen mode's hash is applied and code only leaves the composer alone", async () => {
  answers["/api/chat/rewind_plan"] = plan({ has_changes: true, summary: { files: 1 } });
  answers["/api/chat/rewind"] = reply({ status: "committed" });
  await actions.requestRewind("S", "u1", "hello");
  const flow = useRewindStore.getState().flow;
  assert.equal(flow.visible, true);
  assert.equal(flow.mode, "code_and_conversation");
  actions.setRewindMode("code");
  await actions.confirmRewind();
  assert.equal(posts[1].body.plan_hash, "f");
  assert.equal(state.store.composerDrafts.S, undefined);
  assert.equal(state.toasts[0].m, "Code restored");
  assert.equal(useRewindStore.getState().flow, null);
});

test("a stale plan stays open, says why and refreshes the hash", async () => {
  answers["/api/chat/rewind_plan"] = plan({ has_changes: true, summary: { files: 1 } });
  answers["/api/chat/rewind"] = { status: 409, data: { error: "Files changed since you asked to rewind. Rewind again." } };
  await actions.requestRewind("S", "u1", "hello");
  answers["/api/chat/rewind_plan"] = plan({ has_changes: true, summary: { files: 2 }, plan_hashes: { code_and_conversation: "fresh" } });
  await actions.confirmRewind();
  await tick();
  const flow = useRewindStore.getState().flow;
  assert.match(flow.error, /changed since/);
  assert.equal(flow.phase, "ready");
  assert.equal(flow.plan.plan_hashes.code_and_conversation, "fresh");
  actions.cancelRewind();
});

async function rewindTo(msg, content) {
  answers["/api/chat/rewind_plan"] = plan({ has_changes: false });
  answers["/api/chat/rewind"] = reply({ status: "committed", user_text: content, marks: [{ edge_id: "e", variant: "pending" }] });
  await actions.requestRewind("S", msg, content);
  await tick();
}
const queued = () => useRewindStore.getState().refillFiles.S ?? [];
const undoPending = (data = {}) => {
  answers["/api/chat/rewind_undo"] = reply({ status: "committed", marks: [], ...data });
  return actions.switchRewindMark("S", { edge_id: "e", variant: "pending" });
};

test("a message's attachments go back to the composer, and one that cannot be read keeps its marker", async () => {
  files["/raw/a.png"] = {};
  await rewindTo("u1", "[[a.png@/a.png]] [[lost.pdf@/lost.pdf]]look");
  assert.deepEqual(queued()[0].files.map(f => f.name), ["a.png"]);
  assert.equal(state.store.composerDrafts.S, "[[lost.pdf@/lost.pdf]]\n\nlook");
});

test("a recording stays in the refilled text", async () => {
  await rewindTo("u1", "<rec/>watch this");
  assert.equal(state.store.composerDrafts.S, "<rec/>\n\nwatch this");
});

test("a second rewind replaces the first refill's text and attachments", async () => {
  files["/raw/a.png"] = {};
  files["/raw/b.png"] = {};
  await rewindTo("u5", "[[a.png@/a.png]]five");
  const first = queued()[0].refillId;
  useRewindStore.setState({ refillFiles: {} });
  await rewindTo("u2", "[[b.png@/b.png]]two");
  assert.equal(state.store.composerDrafts.S, "two");
  assert.deepEqual(useRewindStore.getState().retractRefills.S, [first]);
  assert.deepEqual(queued().map(g => g.files[0].name), ["b.png"]);
});

test("Undo retracts only what the refill added", async () => {
  files["/raw/a.png"] = {};
  await rewindTo("u1", "[[a.png@/a.png]]hi");
  const id = queued()[0].refillId;
  await undoPending();
  const retract = useRewindStore.getState().retractRefills.S;
  assert.deepEqual(retract, [id]);
  assert.deepEqual(queued(), []);
  const composer = [{ id: "mine", filename: "a.png" }, { id: "refilled", filename: "a.png", refillId: id }];
  assert.deepEqual(refillItemIds(composer, retract), ["refilled"]);
});

test("Undo during an attachment read cancels the refill", async () => {
  let release;
  files["/raw/a.png"] = { wait: new Promise(r => { release = r; }) };
  await rewindTo("u1", "[[a.png@/a.png]]hi");
  assert.equal(state.store.composerDrafts.S, undefined);
  await undoPending();
  release();
  await tick();
  await tick();
  assert.equal(state.store.composerDrafts.S, undefined);
  assert.deepEqual(queued(), []);
});

test("Undo after a reload removes the refill the server names", async () => {
  state.store.composerDrafts.S = "hello\n\nmine";
  await undoPending({ user_text: "hello" });
  assert.equal(state.store.composerDrafts.S, "mine");
});

test("Undo leaves a refill the user edited", async () => {
  await rewindTo("u1", "fix");
  state.store.composerDrafts.S = "fix the login bug";
  await undoPending({ user_text: "fix" });
  assert.equal(state.store.composerDrafts.S, "fix the login bug");
});

test("a sent refill is not taken for the next draft", async () => {
  await rewindTo("u1", "fix");
  forgetRefill("S");
  state.store.composerDrafts.S = "fix\n\nnotes";
  await rewindTo("u0", "other");
  assert.equal(state.store.composerDrafts.S, "other\n\nfix\n\nnotes");
});

test("a second request waits while an instant rewind applies", async () => {
  let release;
  answers["/api/chat/rewind_plan"] = plan({ has_changes: false });
  const realFetch = globalThis.fetch;
  globalThis.fetch = async (url, init) => {
    if (url === "/api/chat/rewind") await new Promise(r => { release = r; });
    return realFetch(url, init);
  };
  answers["/api/chat/rewind"] = reply({ status: "committed", user_text: "a" });
  const first = actions.requestRewind("S", "u1", "a");
  await tick(); await tick();
  await actions.requestRewind("S", "u2", "b");
  assert.equal(posts.filter(p => p.url === "/api/chat/rewind_plan").length, 1);
  release();
  await first;
  await tick();
  assert.equal(useRewindStore.getState().flow, null);
  assert.deepEqual(state.dismissed, ["rewinding-S"]);
});

test("a lost reply does not claim nothing changed and reloads the conversation", async () => {
  answers["/api/chat/rewind_plan"] = plan({ has_changes: true, summary: { files: 1 } });
  failing["/api/chat/rewind"] = true;
  await actions.requestRewind("S", "u1", "hello");
  await actions.confirmRewind();
  await tick();
  failing = {};
  const flow = useRewindStore.getState().flow;
  assert.match(flow.error, /may not have finished/);
  assert.deepEqual(state.sent, [{ action: "load_session", session_id: "S" }]);
  actions.cancelRewind();
});

test("a re-plan that loses the code option resets the mode", async () => {
  answers["/api/chat/rewind_plan"] = plan({ has_changes: true, summary: { files: 1 } });
  answers["/api/chat/rewind"] = { status: 409, data: { error: "stale_head", status: "aborted" } };
  await actions.requestRewind("S", "u1", "hello");
  answers["/api/chat/rewind_plan"] = { status: 200, data: { status: "unavailable", code_unavailable_reason: "no_code", plan_hashes: { conversation: "c2" } } };
  await actions.confirmRewind();
  await tick();
  const flow = useRewindStore.getState().flow;
  assert.equal(flow.mode, "conversation");
  actions.cancelRewind();
});

test("a re-plan that refuses closes the dialog", async () => {
  answers["/api/chat/rewind_plan"] = plan({ has_changes: true, summary: { files: 1 } });
  answers["/api/chat/rewind"] = { status: 409, data: { error: "stale_head", status: "aborted" } };
  await actions.requestRewind("S", "u1", "hello");
  answers["/api/chat/rewind_plan"] = { status: 200, data: { status: "error", error: "nope" } };
  await actions.confirmRewind();
  await tick();
  assert.equal(useRewindStore.getState().flow, null);
  assert.equal(state.toasts.at(-1).m, "Can't rewind to this message.");
});

test("a failed check still allows a conversation rewind", async () => {
  answers["/api/chat/rewind_plan"] = { status: 500, data: {} };
  await actions.requestRewind("S", "u1", "hello");
  const flow = useRewindStore.getState().flow;
  assert.equal(flow.preview.kind, "none");
  assert.equal(flow.mode, "conversation");
  answers["/api/chat/rewind_plan"] = plan({ has_changes: false });
  answers["/api/chat/rewind"] = reply({ status: "committed" });
  await actions.confirmRewind();
  assert.equal(posts.at(-1).body.plan_hash, "c");
});

test("an archived conversation does not rewind", async () => {
  state.store.conversations.S = { archived: true };
  await actions.requestRewind("S", "u1", "x");
  assert.equal(posts.length, 0);
  await actions.openRewindPicker("S");
  assert.match(state.toasts[0].m, /isn't available/);
  assert.equal(useRewindStore.getState().picker, null);
});

test("an error card's Rewind starts from an empty composer; the picker alone keeps the draft", async () => {
  answers["/api/chat/rewind_points"] = reply({ points: [] });
  state.store.composerDrafts.S = "draft";
  await actions.openRewindPicker("S");
  assert.equal(state.store.composerDrafts.S, "draft");
  await actions.openRewindPicker("S", { fromError: true });
  assert.equal(state.store.composerDrafts.S, "");
});

test("a running turn stops the rewind before any request", async () => {
  state.store.runningTasks.S = {};
  await actions.requestRewind("S", "u1", "x");
  assert.equal(posts.length, 0);
});

test("Undo keeps the user's draft and removes the refill", async () => {
  answers["/api/chat/rewind_plan"] = plan({ has_changes: false });
  answers["/api/chat/rewind"] = reply({ status: "committed", user_text: "hello" });
  state.store.composerDrafts.S = "mine";
  await actions.requestRewind("S", "u1", "hello");
  await tick();
  assert.equal(state.store.composerDrafts.S, "hello\n\nmine");
  answers["/api/chat/rewind_undo"] = reply({ status: "committed", marks: [] });
  await actions.switchRewindMark("S", { edge_id: "e", variant: "pending" });
  assert.deepEqual(posts.at(-1), { url: "/api/chat/rewind_undo", body: { session_id: "S", edge_id: "e" } });
  assert.equal(state.store.composerDrafts.S, "mine");
});

test("a refused Redo says why and a retired one is silent", async () => {
  answers["/api/chat/rewind_redo"] = { status: 409, data: { status: "refused", code: "gone" } };
  await actions.switchRewindMark("S", { edge_id: "e", variant: "forward" });
  assert.match(state.toasts[0].m, /redone/);
  state.toasts.length = 0;
  answers["/api/chat/rewind_redo"] = { status: 409, data: { status: "refused", code: "retired" } };
  await actions.switchRewindMark("S", { edge_id: "e", variant: "forward" });
  assert.equal(state.toasts.length, 0);
});

test("the picker lists the oldest message first", async () => {
  answers["/api/chat/rewind_points"] = reply({ points: [{ msg_id: "new" }, { msg_id: "old" }] });
  await actions.openRewindPicker("S");
  assert.deepEqual(useRewindStore.getState().picker.points.map(p => p.msg_id), ["old", "new"]);
});

test("Rewind and retry waits for an empty composer and leaves files alone", async () => {
  state.store.composerDrafts.S = "draft";
  await actions.rewindAndRetry("S", "u1", "bad");
  assert.equal(posts.length, 0);
  assert.match(state.toasts[0].m, /draft/);
  state.store.composerDrafts.S = "";
  answers["/api/chat/rewind_plan"] = plan({ has_changes: true });
  answers["/api/chat/rewind"] = reply({ status: "committed", user_text: "bad" });
  await actions.rewindAndRetry("S", "u1", "bad");
  assert.equal(posts[1].body.mode, "conversation");
  assert.equal(state.store.composerDrafts.S, "bad");
});
