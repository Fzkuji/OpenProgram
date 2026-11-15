/**
 * A web search the model's provider runs itself: the stream's tool_use /
 * tool_result pair with provider_executed, the persisted block a reload
 * reads back, text-block citations, and how the row is presented.
 */
import assert from "node:assert/strict";
import { existsSync } from "node:fs";
import { registerHooks } from "node:module";
import test from "node:test";
import { fileURLToPath } from "node:url";

registerHooks({
  resolve(specifier, context, nextResolve) {
    const base = specifier.startsWith("@/")
      ? new URL(`../../../${specifier.slice(2)}`, import.meta.url).href
      : specifier.startsWith(".") && !/\.[a-z]+$/.test(specifier)
        ? new URL(specifier, context.parentURL).href
        : null;
    if (base) {
      for (const suffix of [".ts", ".tsx", "/index.ts"]) {
        if (existsSync(fileURLToPath(base + suffix))) return { url: base + suffix, shortCircuit: true };
      }
    }
    return nextResolve(specifier, context);
  },
});

const values = new Map();
globalThis.window = { addEventListener: () => {}, dispatchEvent: () => {}, location: { pathname: "/chat" } };
globalThis.localStorage = {
  getItem: (key) => values.get(key) ?? null,
  setItem: (key, value) => values.set(key, String(value)),
  removeItem: (key) => values.delete(key),
};
globalThis.requestAnimationFrame = undefined;

const { useSessionStore } = await import("../../../lib/session-store/index.ts");
const { applyChatWsMessage } = await import("../../../lib/net/ws/chat-stream.ts");
const { citationSources, isProviderSearch, parseSearchSources } = await import("../../../lib/chat/transcript/web-sources.ts");
const { presentTool } = await import("../../../components/chat/messages/execution/tool-presentation.ts");
const { summarizeToolGroup } = await import("../../../components/chat/messages/execution/tool-group-summary.ts");

const en = (english) => english;
const response = (sid, data) => applyChatWsMessage({ type: "chat_response", data: { session_id: sid, ...data } });
const SOURCES = "Rust 1.80 released — https://blog.rust-lang.org/2024/07/25/Rust-1.80.0.html\nhttps://www.reddit.com/r/rust/";

// The server keeps provider_executed and citations on a reload (the
// provider_citations recording); the client names each source once.
test("a reloaded reply's citations name each source once", () => {
  const citations = [
    { url: "https://blog.rust-lang.org/x", title: "Rust blog", start: 0, end: 9 },
    { url: "https://blog.rust-lang.org/x", title: null, start: null, end: null },
    { url: "https://www.example.org/y", title: null, start: null, end: null },
  ];
  assert.deepEqual(citationSources(citations).map((s) => s.label), ["Rust blog", "example.org"]);
  assert.deepEqual(citationSources(undefined), []);
});

test("a provider-executed web_search reads as one Searched the web line; a function web_search keeps its row", () => {
  const input = JSON.stringify({ query: "rust 1.80" });
  const provider = presentTool("web_search", input, en, { providerExecuted: true });
  assert.deepEqual(provider, { icon: "global", tone: "web", title: "Searched the web: rust 1.80" });
  assert.equal(presentTool("web_search", "rust news", en, { providerExecuted: true }).title, "Searched the web: rust news");
  assert.equal(presentTool("web_search", "", en, { providerExecuted: true }).title, "Searched the web");

  const local = presentTool("web_search", input, en);
  assert.deepEqual(local, { icon: "global", tone: "web", title: "Searched the web", target: "rust 1.80" });
  assert.equal(isProviderSearch({ type: "tool", tool: "web_search" }), false);
  assert.equal(isProviderSearch({ type: "tool", tool: "bash", provider_executed: true }), false);
});

test("the group summary counts provider searches as web searches", () => {
  const blocks = [
    { type: "tool", tool: "web_search", tool_call_id: "p1", provider_executed: true, input: "{\"query\":\"a\"}", result: "completed" },
    { type: "tool", tool: "web_search", tool_call_id: "p2", provider_executed: true, input: "{\"query\":\"b\"}", result: "completed" },
  ];
  assert.equal(summarizeToolGroup(blocks, { text: en }).label, "Searched the web 2 times");
});

