import assert from "node:assert/strict";
import { existsSync } from "node:fs";
import { registerHooks } from "node:module";
import test from "node:test";
import { fileURLToPath } from "node:url";

import { parseHTML } from "linkedom";

const webRoot = new URL("../", import.meta.url);
registerHooks({
  resolve(specifier, context, nextResolve) {
    if (specifier.endsWith(".module.css")) {
      return {
        url: "data:text/javascript,export default {}",
        shortCircuit: true,
      };
    }
    const resolveBase = specifier.startsWith("@/")
      ? new URL(specifier.slice(2), webRoot).href
      : specifier.startsWith(".") && !/\.[a-z]+$/i.test(specifier)
        ? new URL(specifier, context.parentURL).href
        : null;
    if (resolveBase) {
      for (const suffix of [".ts", ".tsx", "/index.ts", "/index.tsx"]) {
        const url = `${resolveBase}${suffix}`;
        if (existsSync(fileURLToPath(url))) {
          return { url, shortCircuit: true };
        }
      }
    }
    return nextResolve(specifier, context);
  },
});

const parsed = parseHTML(
  '<!doctype html><html><body><div id="root"></div></body></html>',
);
globalThis.window = parsed.window;
globalThis.document = parsed.document;
globalThis.Event = parsed.window.Event;
globalThis.CustomEvent = parsed.window.CustomEvent;
globalThis.localStorage = {
  getItem() { return null; },
  setItem() {},
  removeItem() {},
};
globalThis.WebSocket = { OPEN: 1 };
globalThis.IS_REACT_ACT_ENVIRONMENT = true;
Object.defineProperty(window, "location", {
  value: { pathname: "/chat" },
  configurable: true,
});
window.matchMedia = () => ({
  matches: false,
  addEventListener() {},
  removeEventListener() {},
});
globalThis.history = {
  pushState() {},
  replaceState() {},
};

const httpCalls = [];
let nextHttpResponse = { ok: true, status: 200, payload: {} };
globalThis.fetch = async (url, init = {}) => {
  httpCalls.push({ url: String(url), init });
  const response = nextHttpResponse;
  return {
    ok: response.ok,
    status: response.status,
    async json() { return response.payload; },
  };
};

const wsFrames = [];
const { runtimeState, setSocket } = await import(
  "../lib/runtime-bridge/state.ts"
);
setSocket({
  readyState: WebSocket.OPEN,
  send(payload) { wsFrames.push(JSON.parse(payload)); },
});

const guiAgent = {
  name: "gui_agent",
  description: "GUI agent",
  params_detail: [
    { name: "task", type: "str", required: true },
    {
      name: "surface",
      type: "str",
      required: false,
      options: ["desktop", "browser"],
    },
    {
      name: "max_steps",
      type: "int | None",
      required: false,
      hidden: true,
      advanced: true,
    },
    {
      name: "max_seconds",
      type: "float | None",
      required: false,
      hidden: true,
      advanced: true,
    },
    {
      name: "backend",
      type: "str",
      required: false,
      hidden: true,
      advanced: true,
      options: ["local", "browser"],
    },
    { name: "allow_general", type: "bool", required: false, hidden: true },
    { name: "runtime", type: "Runtime", required: false, hidden: true },
  ],
};
runtimeState.availableFunctions = [guiAgent];
const { useFunctions } = await import("../lib/state/functions-store.ts");
useFunctions.getState().setFunctions([guiAgent]);

const React = await import("react");
const { act, createElement } = React;
const { createRoot } = await import("react-dom/client");
const { useChatSubmit } = await import(
  "../components/chat/composer/submit/use-chat-submit.ts"
);
const { useFunctionDispatch } = await import(
  "../components/chat/composer/modes/fn-form/use-function-dispatch.ts"
);
const {
  normalizeFunctionArguments,
  parseFunctionInvocation,
  userFunctionParams,
} = await import("../lib/function-invocation.ts");
const pendingUserText = await import("../lib/pending-user-text.ts");
const { useSessionStore } = await import("../lib/session-store/index.ts");
const {
  draftChannelChoiceHost,
  setDraftChannelChoice,
} = await import("../lib/runtime-bridge/draft-channel-choice.ts");

async function submitThroughComposer(input, sessionKey, overrides = {}) {
  let hook;
  const clearedDrafts = [];
  const activatedSessions = [];
  const dispatchFrames = [];
  const bound = overrides.bound ? sessionKey : null;
  function Probe() {
    const dispatchFunction = useFunctionDispatch({
      currentSessionId: bound,
      activeChatKey: sessionKey,
      background: bound !== null,
      isRunning: overrides.isRunning ?? false,
      noEnabledModels: overrides.noEnabledModels ?? false,
      promptNeedModel() {},
      send(payload) { dispatchFrames.push(payload); return true; },
      setCurrentConv(sid) { activatedSessions.push(sid); },
    });
    hook = useChatSubmit({
      bound,
      input,
      activeChatKey: sessionKey,
      currentSessionId: bound,
      isRunning: overrides.isRunning ?? false,
      noEnabledModels: overrides.noEnabledModels ?? false,
      promptNeedModel() {},
      send(payload) { dispatchFrames.push(payload); return true; },
      setComposerInputFor(owner, value) { clearedDrafts.push({ owner, value }); },
      setHistoryIndex() {},
      slash: {
        runCommand() { return false; },
        close() {},
      },
      pendingImages: overrides.pendingImages ?? [],
      pendingDocs: overrides.pendingDocs ?? [],
      clearAttachmentsAfterSubmit() {},
      thinking: "medium",
      toolsEnabled: true,
      toolsProfile: "__agent__",
      webSearchEnabled: false,
      fastEnabled: false,
      fastSupported: false,
      runningMessageMode: "queue",
      dispatchFunction,
    });
    return null;
  }

  const host = document.createElement("div");
  document.body.appendChild(host);
  const root = createRoot(host);
  await act(async () => { root.render(createElement(Probe)); });
  await act(async () => { await hook.submit(); });
  await act(async () => {
    await Promise.resolve();
    await Promise.resolve();
  });
  await act(async () => { root.unmount(); });
  host.remove();
  return { activatedSessions, clearedDrafts, dispatchFrames };
}

function resetObservations() {
  httpCalls.length = 0;
  wsFrames.length = 0;
  nextHttpResponse = { ok: true, status: 200, payload: {} };
}

function releaseChatReservation(sessionKey) {
  pendingUserText.clearPendingUserText(sessionKey);
  pendingUserText.clearPendingFirstAck(sessionKey);
  useSessionStore.getState().setRunningTaskFor(sessionKey, null);
}

const exact =
  'gui_agent(task="Verify title", surface="browser", max_steps=3, max_seconds=90)';

test("an exact registered function expression dispatches as a function", async () => {
  const sessionKey = "local_fn_exact";
  resetObservations();
  await submitThroughComposer(exact, sessionKey);

  assert.equal(wsFrames.filter((p) => p.action === "chat").length, 0);
  assert.equal(pendingUserText.getPendingUserText(sessionKey), undefined);
  assert.equal(httpCalls.length, 1);
  assert.equal(httpCalls[0].url, "/api/function/gui_agent");
  assert.deepEqual(JSON.parse(httpCalls[0].init.body), {
    kwargs: {
      task: "Verify title",
      surface: "browser",
      max_steps: 3,
      max_seconds: 90,
    },
    session_id: sessionKey,
  });
  releaseChatReservation(sessionKey);
});

test("explanatory text remains a chat message", async () => {
  const sessionKey = "local_fn_explanation";
  resetObservations();
  const input = `请解释 ${exact}`;
  await submitThroughComposer(input, sessionKey);

  assert.equal(httpCalls.length, 0);
  assert.deepEqual(
    wsFrames.filter((p) => p.action === "chat").map((p) => p.text),
    [input],
  );
  releaseChatReservation(sessionKey);
});

test("a backtick-wrapped expression remains a chat message", async () => {
  const sessionKey = "local_fn_code";
  resetObservations();
  const input = `\`${exact}\``;
  await submitThroughComposer(input, sessionKey);

  assert.equal(httpCalls.length, 0);
  assert.deepEqual(
    wsFrames.filter((p) => p.action === "chat").map((p) => p.text),
    [input],
  );
  releaseChatReservation(sessionKey);
});

test("an expression followed by explanation remains a chat message", async () => {
  const sessionKey = "local_fn_suffix";
  resetObservations();
  const input = `${exact} 请解释返回值`;
  await submitThroughComposer(input, sessionKey);

  assert.equal(httpCalls.length, 0);
  assert.deepEqual(
    wsFrames.filter((p) => p.action === "chat").map((p) => p.text),
    [input],
  );
  releaseChatReservation(sessionKey);
});

test("malformed call examples inside questions remain chat messages", async () => {
  for (const [index, input] of [
    'gui_agent(task=什么) 是什么意思？',
    'gui_agent(task="x", task="y") 为什么重复？',
  ].entries()) {
    const sessionKey = `local_fn_question_${index}`;
    resetObservations();
    await submitThroughComposer(input, sessionKey);
    assert.equal(httpCalls.length, 0);
    assert.deepEqual(
      wsFrames.filter((p) => p.action === "chat").map((p) => p.text),
      [input],
    );
    releaseChatReservation(sessionKey);
  }
});

test("a direct function expression never becomes a queued chat message", async () => {
  const sessionKey = "local_fn_running";
  resetObservations();
  const { clearedDrafts } = await submitThroughComposer(exact, sessionKey, {
    isRunning: true,
  });

  assert.equal(httpCalls.length, 0);
  assert.equal(wsFrames.filter((p) => p.action === "chat").length, 0);
  assert.deepEqual(clearedDrafts, []);
});

test("attachments do not downgrade a direct function call to chat", async () => {
  const sessionKey = "local_fn_attachment";
  resetObservations();
  const { clearedDrafts } = await submitThroughComposer(exact, sessionKey, {
    pendingImages: [{ loading: false }],
  });

  assert.equal(httpCalls.length, 0);
  assert.equal(wsFrames.filter((p) => p.action === "chat").length, 0);
  assert.deepEqual(clearedDrafts, []);
});

test("a bound function call does not mark or navigate the focused session", async () => {
  const focusedSession = "focused-A";
  const boundSession = "bound-B";
  const previousRuntimeSession = runtimeState.currentSessionId;
  const store = useSessionStore.getState();
  store.setRunningTaskFor(focusedSession, null);
  store.setRunningTaskFor(boundSession, null);
  runtimeState.currentSessionId = focusedSession;
  resetObservations();
  nextHttpResponse = {
    ok: true,
    status: 200,
    payload: { session_id: boundSession },
  };

  const { activatedSessions } = await submitThroughComposer(
    exact,
    boundSession,
    { bound: true },
  );

  assert.equal(useSessionStore.getState().runningTasks[focusedSession], undefined);
  assert.equal(
    useSessionStore.getState().runningTasks[boundSession]?.func_name,
    "gui_agent",
  );
  assert.deepEqual(activatedSessions, []);
  releaseChatReservation(boundSession);
  runtimeState.currentSessionId = previousRuntimeSession;
});

test("a failed bound function call rolls back only its own session", async () => {
  const focusedSession = "focused-failure-A";
  const boundSession = "bound-failure-B";
  const previousRuntimeSession = runtimeState.currentSessionId;
  const store = useSessionStore.getState();
  store.setRunningTaskFor(focusedSession, null);
  store.setRunningTaskFor(boundSession, null);
  runtimeState.currentSessionId = focusedSession;
  resetObservations();
  nextHttpResponse = {
    ok: false,
    status: 500,
    payload: { error: "dispatch failed" },
  };
  const originalConsoleError = console.error;
  console.error = () => {};
  try {
    await submitThroughComposer(exact, boundSession, { bound: true });
  } finally {
    console.error = originalConsoleError;
    runtimeState.currentSessionId = previousRuntimeSession;
  }

  assert.equal(useSessionStore.getState().runningTasks[focusedSession], undefined);
  assert.equal(useSessionStore.getState().runningTasks[boundSession], undefined);
});

test("a successful function response binds channel and project before navigation", async () => {
  const draftSession = "draft-function-success";
  const createdSession = "created-function-success";
  resetObservations();
  useSessionStore.getState().setPendingProject(draftSession, "project-1");
  setDraftChannelChoice(draftChannelChoiceHost, draftSession, {
    channel: "slack",
    account_id: "team-1",
  });
  nextHttpResponse = {
    ok: true,
    status: 200,
    payload: { session_id: createdSession },
  };

  const { activatedSessions, dispatchFrames } = await submitThroughComposer(
    exact,
    draftSession,
  );

  assert.deepEqual(JSON.parse(httpCalls[0].init.body), {
    kwargs: {
      task: "Verify title",
      surface: "browser",
      max_steps: 3,
      max_seconds: 90,
    },
    project_id: "project-1",
    session_id: draftSession,
  });
  assert.deepEqual(dispatchFrames, [
    {
      action: "set_conversation_channel",
      session_id: createdSession,
      channel: "slack",
      account_id: "team-1",
    },
    {
      action: "set_session_project",
      session_id: createdSession,
      project_id: "project-1",
    },
  ]);
  assert.deepEqual(activatedSessions, [createdSession]);
  assert.equal(
    useSessionStore.getState().pendingProjectsByChat[draftSession],
    undefined,
  );
  releaseChatReservation(draftSession);
});

test("a failed focused function call clears its placeholder and running state", async () => {
  const sessionKey = "focused-function-failure";
  const previousRuntimeSession = runtimeState.currentSessionId;
  const previousActive = useSessionStore.getState().activeChatKey;
  const previousCurrent = useSessionStore.getState().currentSessionId;
  runtimeState.currentSessionId = sessionKey;
  useSessionStore.setState({
    activeChatKey: sessionKey,
    currentSessionId: sessionKey,
  });
  resetObservations();
  nextHttpResponse = {
    ok: false,
    status: 503,
    payload: { error: "temporarily unavailable" },
  };
  const originalConsoleError = console.error;
  console.error = () => {};
  try {
    await submitThroughComposer(exact, sessionKey);
  } finally {
    console.error = originalConsoleError;
    runtimeState.currentSessionId = previousRuntimeSession;
    useSessionStore.setState({
      activeChatKey: previousActive,
      currentSessionId: previousCurrent,
    });
  }

  assert.equal(useSessionStore.getState().runningTasks[sessionKey], undefined);
  assert.equal(
    (useSessionStore.getState().messageOrder[sessionKey] || []).some(
      (id) => id.startsWith("__optimistic_fn__:"),
    ),
    false,
  );
});

test("invalid registered expressions open the shared form instead of chat", async () => {
  const sessionKey = "local_fn_invalid";
  resetObservations();
  await submitThroughComposer(
    'gui_agent(surface="browser", max_steps=3)',
    sessionKey,
  );

  const store = useSessionStore.getState();
  assert.equal(httpCalls.length, 0);
  assert.equal(wsFrames.filter((p) => p.action === "chat").length, 0);
  assert.equal(store.fnFormFunction?.name, "gui_agent");
  assert.deepEqual(store.fnFormPrefill, {
    surface: "browser",
    max_steps: "3",
  });
  store.closeFnForm();
});

test("parser accepts only whole registered calls with named literals", () => {
  const valid = parseFunctionInvocation(
    "gui_agent(task='a,b=c\\nline', surface=\"browser\", backend=\"local\")",
    [guiAgent],
  );
  assert.equal(valid.kind, "valid");
  assert.deepEqual(valid.kwargs, {
    task: "a,b=c\nline",
    surface: "browser",
    backend: "local",
  });

  for (const input of [
    `please run ${exact}`,
    `\`${exact}\``,
    `\`\`\`\n${exact}\n\`\`\``,
    `${exact} and explain it`,
    'unknown_agent(task="x")',
  ]) {
    assert.equal(parseFunctionInvocation(input, [guiAgent]).kind, "none", input);
  }
});

test("parser rejects invalid schema values and executable syntax", () => {
  for (const input of [
    "gui_agent()",
    'gui_agent(task="x", task="y")',
    'gui_agent(task="x", missing=1)',
    'gui_agent(task="x", surface="window")',
    'gui_agent(task="x", surface="")',
    'gui_agent(task=do_bad())',
    'gui_agent(task="x", runtime="secret")',
    'gui_agent(task="x", surface=None)',
  ]) {
    assert.equal(parseFunctionInvocation(input, [guiAgent]).kind, "invalid", input);
  }

  const nullableAdvanced = parseFunctionInvocation(
    'gui_agent(task="x", max_steps=None)',
    [guiAgent],
  );
  assert.equal(nullableAdvanced.kind, "valid");
  assert.equal(nullableAdvanced.kwargs.max_steps, null);
});

test("form values and text expressions normalize to identical typed kwargs", () => {
  const fromForm = normalizeFunctionArguments(guiAgent, {
    task: "Verify title",
    surface: "browser",
    max_steps: "3",
    max_seconds: "90",
  });
  const fromText = parseFunctionInvocation(exact, [guiAgent]);
  assert.equal(fromForm.ok, true);
  assert.equal(fromText.kind, "valid");
  assert.deepEqual(fromForm.kwargs, fromText.kwargs);

  const invalidInteger = normalizeFunctionArguments(guiAgent, {
    task: "x",
    max_steps: "3x",
  });
  assert.equal(invalidInteger.ok, false);

  const integerFloatSpelling = normalizeFunctionArguments(guiAgent, {
    task: "x",
    max_steps: "3.0",
  });
  const integerExponentSpelling = normalizeFunctionArguments(guiAgent, {
    task: "x",
    max_steps: "3e0",
  });
  assert.deepEqual(integerFloatSpelling, { ok: true, kwargs: { task: "x", max_steps: 3 } });
  assert.deepEqual(integerExponentSpelling, { ok: true, kwargs: { task: "x", max_steps: 3 } });
});

test("Optional and primitive union annotations validate each branch", () => {
  const fn = {
    name: "typed",
    params_detail: [
      { name: "count", type: "Optional[int]", required: false },
      { name: "value", type: "str | int", required: false },
    ],
  };
  assert.equal(normalizeFunctionArguments(fn, { count: "abc" }).ok, false);
  assert.deepEqual(normalizeFunctionArguments(fn, { count: null }), {
    ok: true,
    kwargs: { count: null },
  });
  assert.deepEqual(normalizeFunctionArguments(fn, { value: "abc" }), {
    ok: true,
    kwargs: { value: "abc" },
  });
  const complex = {
    name: "complex",
    params_detail: [
      { name: "arguments", type: "dict | None", required: false },
    ],
  };
  assert.equal(
    normalizeFunctionArguments(complex, { arguments: "abc" }).ok,
    false,
  );
  assert.equal(
    normalizeFunctionArguments(complex, { arguments: "" }).ok,
    false,
  );
});

test("hidden advanced parameters are user-visible but internal parameters are not", () => {
  assert.deepEqual(
    userFunctionParams(guiAgent).map((param) => param.name),
    ["task", "surface", "max_steps", "max_seconds", "backend"],
  );
});
