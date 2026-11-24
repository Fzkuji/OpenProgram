// An empty chat spells ALWAYS-ON in glyphs only when its Agent switches to an
// always-on one; opening a chat, a new chat or coming back plays nothing.
import assert from "node:assert/strict";
import { afterEach, test } from "node:test";

import { installLoader } from "../../_support/load.mjs";
import { installDom, render } from "../../_support/dom.mjs";

installLoader();
installDom();
Object.getPrototypeOf(document.createElement("canvas")).getContext = () => null;

const { createElement: h } = await import("react");
const { QueryClient, QueryClientProvider } = await import("@tanstack/react-query");
const { useSessionStore } = await import("../../../lib/session-store/index.ts");
const { AGENTS_KEY } = await import("../../../lib/agents/agents-query.ts");
const { WelcomeScreen } = await import("../../../components/chat/welcome/welcome-screen.tsx");

let seq = 0;
const agent = (id, enabled, extra = {}) => ({ id, name: id, always_on: { enabled }, ...extra });
const MAIN = agent("main", false, { default: true });
const WATCHER = agent("watcher", true);

let client;
let view;
afterEach(async () => { await view?.unmount(); view = undefined; });

async function show({ agents = [MAIN, WATCHER], picked, trial, active = `local_${++seq}`, session = null, draft = "", bound } = {}) {
  // Queries are never collected: a collection timer of five minutes would keep the process alive after the last test.
  client = new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: Infinity, gcTime: Infinity } } });
  client.setQueryData(AGENTS_KEY, agents);
  useSessionStore.setState({
    welcomeVisible: true,
    activeChatKey: active,
    currentSessionId: session,
    composerDrafts: draft ? { [active ?? "__new__"]: draft } : {},
    composerSettingsBySession: picked ? { [active]: { agentInvocation: { agentId: picked, ...(trial ? { trial } : {}) } } } : {},
    boundAgentBySession: bound ? { [session]: bound } : {},
    conversations: {},
  });
  view = await render(h(QueryClientProvider, { client }, h(WelcomeScreen)));
}
const intro = () => view.container.querySelector("canvas");
const pick = (agentId, extra = {}) => view.act(async () => {
  const key = useSessionStore.getState().activeChatKey;
  useSessionStore.setState({ composerSettingsBySession: { [key]: { agentInvocation: { agentId, ...extra } } } });
});
const buttons = () => [...view.container.querySelectorAll("button")];

test("switching the chat to an always-on Agent plays it, with nothing to click", async () => {
  await show({});
  assert.equal(intro(), null);
  await pick("watcher");
  assert.ok(intro());
  assert.equal(buttons().length, 0);
  assert.equal(view.container.textContent.trim(), "");
});

test("opening a chat already on an always-on Agent, new or bound, plays nothing", async () => {
  await show({ picked: "watcher" });
  assert.equal(intro(), null);
  await view.unmount();
  await show({ agents: [agent("watcher", true, { default: true })], active: null });
  assert.equal(intro(), null);
  await view.unmount();
  await show({ active: "sess1", session: "sess1", bound: "watcher" });
  assert.equal(intro(), null);
});

test("switching to a normal Agent plays nothing", async () => {
  await show({ picked: "watcher" });
  await pick("main");
  assert.equal(intro(), null);
});

test("switching to another chat on an always-on Agent plays nothing", async () => {
  await show({});
  await view.act(async () => {
    useSessionStore.setState({ activeChatKey: "local_other", composerSettingsBySession: { local_other: { agentInvocation: { agentId: "watcher" } } } });
  });
  assert.equal(intro(), null);
});

test("turning always-on off takes it away at once", async () => {
  await show({});
  await pick("watcher");
  assert.ok(intro());
  await view.act(async () => {
    client.setQueryData(AGENTS_KEY, [MAIN, agent("watcher", false)]);
    await new Promise((resolve) => setTimeout(resolve, 10)); // the query client tells its readers on a timer
  });
  assert.equal(intro(), null);
});

test("a trial is not an always-on Agent, whatever its saved settings say", async () => {
  await show({});
  await pick("watcher", { trial: true });
  assert.equal(intro(), null);
});
