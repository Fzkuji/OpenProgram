// An empty chat with an always-on Agent offers three things to say; a click
// only fills the box, typing hides them, and any other chat stays empty.
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
const { setLocale } = await import("../../../lib/i18n/index.ts");
const { WelcomeScreen } = await import("../../../components/chat/welcome/welcome-screen.tsx");

const KEY = "local_draft";
const agent = (id, enabled, extra = {}) => ({ id, name: id, always_on: { enabled }, ...extra });
const MAIN = agent("main", false, { default: true });
const WATCHER = agent("watcher", true);

let client;
let view;
afterEach(async () => { await view?.unmount(); view = undefined; setLocale("en"); });

async function show({ agents = [MAIN, WATCHER], picked, trial, active = KEY, session = null, draft = "", bound } = {}) {
  client = new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: Infinity } } });
  client.setQueryData(AGENTS_KEY, agents);
  useSessionStore.setState({
    welcomeVisible: true,
    activeChatKey: active,
    currentSessionId: session,
    composerDrafts: draft ? { [active ?? "__new__"]: draft } : {},
    composerSettingsBySession: picked ? { [KEY]: { agentInvocation: { agentId: picked, ...(trial ? { trial } : {}) } } } : {},
    boundAgentBySession: bound ? { [session]: bound } : {},
    conversations: {},
  });
  view = await render(h(QueryClientProvider, { client }, h(WelcomeScreen)));
}
const buttons = () => [...view.container.querySelectorAll("button")];

test("a draft picking an always-on Agent offers three starters and a click only fills the box", async () => {
  await show({ picked: "watcher" });
  const focusBefore = useSessionStore.getState().composerFocusTick;
  assert.equal(buttons().length, 3);
  assert.match(view.container.textContent, /watcher keeps running/);
  const text = buttons()[1].textContent;
  await view.act(() => buttons()[1].click());
  assert.equal(useSessionStore.getState().composerDrafts[KEY], text);
  assert.equal(useSessionStore.getState().composerFocusTick, focusBefore + 1);
  assert.equal(buttons().length, 0, "starters leave once the box has text");
});

test("text already in the box keeps the starters away", async () => {
  await show({ picked: "watcher", draft: "my own words" });
  assert.equal(buttons().length, 0);
});

test("a normal Agent's draft and a chat on a normal default Agent stay empty", async () => {
  await show({ picked: "main" });
  assert.equal(buttons().length, 0);
  await view.unmount();
  await show({});
  assert.equal(buttons().length, 0);
});

test("an always-on default Agent offers them with no pick, also before a draft key exists", async () => {
  const agents = [agent("watcher", true, { default: true })];
  await show({ agents });
  assert.equal(buttons().length, 3);
  await view.unmount();
  await show({ agents, active: null });
  assert.equal(buttons().length, 3);
  const text = buttons()[0].textContent;
  await view.act(() => buttons()[0].click());
  assert.equal(useSessionStore.getState().composerDrafts.__new__, text);
});

test("an empty conversation already bound to an always-on Agent offers them", async () => {
  await show({ active: "sess1", session: "sess1", bound: "watcher" });
  assert.equal(buttons().length, 3);
});

test("turning always-on off takes the starters away at once", async () => {
  await show({ picked: "watcher" });
  assert.equal(buttons().length, 3);
  await view.act(async () => {
    client.setQueryData(AGENTS_KEY, [MAIN, agent("watcher", false)]);
    await new Promise((resolve) => setTimeout(resolve, 10)); // the query client tells its readers on a timer
  });
  assert.equal(buttons().length, 0);
});

test("a trial is not an always-on Agent, whatever its saved settings say", async () => {
  await show({ picked: "watcher", trial: true });
  assert.equal(buttons().length, 0);
});

test("the starters follow the language setting", async () => {
  await show({ picked: "watcher" });
  assert.match(buttons()[2].textContent, /keeping track/);
  await view.act(() => setLocale("zh"));
  assert.match(buttons()[2].textContent, /替我留意/);
  assert.match(view.container.textContent, /watcher会一直在后台运行/);
});
