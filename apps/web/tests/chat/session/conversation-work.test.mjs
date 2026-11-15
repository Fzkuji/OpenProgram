import assert from "node:assert/strict";
import { registerHooks } from "node:module";
import test from "node:test";

const stub = (source) => `data:text/javascript,${encodeURIComponent(source)}`;
const requests = [];
registerHooks({
  resolve(specifier, context, nextResolve) {
    if (specifier === "@/lib/net/fetch-client") {
      return { url: stub(`export const jsonFetch = (url, init) => { globalThis.__workRequests.push([url, init?.method ?? "GET", init?.body ?? null]); return Promise.resolve({}); };`), shortCircuit: true };
    }
    if (specifier === "react") {
      return { url: stub("export const useCallback = (f) => f; export const useEffect = () => {}; export const useRef = (v) => ({ current: v }); export const useState = (v) => [v, () => {}];"), shortCircuit: true };
    }
    return nextResolve(specifier, context);
  },
});
globalThis.__workRequests = requests;

const work = await import("../../../lib/chat/conversation-work.ts");

test("the conversation's controls address its own endpoints", async () => {
  await work.wakeConversation("s 1");
  await work.setOpenWorkStatus("s 1", "3", "done");
  await work.removeOpenWork("s 1", "3");
  assert.deepEqual(requests, [
    ["/api/sessions/s%201/always-on/wake", "POST", null],
    ["/api/sessions/s%201/open-work/3", "PATCH", JSON.stringify({ status: "done" })],
    ["/api/sessions/s%201/open-work/3", "DELETE", null],
  ]);
});

test("report preferences are added and removed on the conversation's Agent", async () => {
  requests.length = 0;
  await work.addReportPreference("s 1", work.dontReportText("Docs build is slow"));
  await work.removeReportPreference("s 1", "p/1");
  assert.deepEqual(requests, [
    ["/api/sessions/s%201/always-on/preferences", "POST", JSON.stringify({ text: "Don't report: Docs build is slow" })],
    ["/api/sessions/s%201/always-on/preferences/p%2F1", "DELETE", null],
  ]);
});
