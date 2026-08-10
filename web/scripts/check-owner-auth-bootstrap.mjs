import assert from "node:assert/strict";

let auth;
try {
  auth = await import("../lib/net/owner-auth-bootstrap.ts");
} catch (error) {
  assert.fail(`owner auth bootstrap module is missing: ${error}`);
}

const { createOwnerAuthBootstrapCoordinator } = auth;
assert.equal(
  typeof createOwnerAuthBootstrapCoordinator,
  "function",
  "the frontend must expose one coordinator that owns fragment removal and bootstrap ordering",
);

const TOKEN = "A".repeat(43);

function browserState(hash) {
  const events = [];
  const location = {
    hash,
    pathname: "/chat",
    search: "?profile=worker",
  };
  const history = {
    state: { navigation: "kept" },
    replaceState(state, title, url) {
      events.push({ type: "replace", state, title, url });
      location.hash = "";
    },
  };
  return { events, history, location };
}

// A future implementation that stores the owner token in Web Storage must
// fail this executable check. The bootstrap contract keeps it only in memory.
Object.defineProperty(globalThis, "localStorage", {
  configurable: true,
  get() {
    throw new Error("owner bootstrap must not read localStorage");
  },
});
Object.defineProperty(globalThis, "sessionStorage", {
  configurable: true,
  get() {
    throw new Error("owner bootstrap must not read sessionStorage");
  },
});

{
  const browser = browserState(`#token=${TOKEN}`);
  let releaseResponse;
  const response = new Promise((resolve) => {
    releaseResponse = resolve;
  });

  const coordinator = createOwnerAuthBootstrapCoordinator({
    location: browser.location,
    history: browser.history,
    fetch: async (url, init) => {
      browser.events.push({ type: "fetch", url, init, hash: browser.location.hash });
      return response;
    },
  });

  assert.deepEqual(
    browser.events,
    [{
      type: "replace",
      state: { navigation: "kept" },
      title: "",
      url: "/chat?profile=worker",
    }],
    "the token fragment must be removed synchronously when the coordinator is created",
  );

  const first = coordinator.wait();
  const second = coordinator.wait();
  assert.strictEqual(second, first, "concurrent consumers must share one bootstrap request");
  assert.equal(browser.events[1].type, "fetch");
  assert.equal(browser.events[1].hash, "", "the fragment must be gone before bootstrap fetch");
  assert.equal(browser.events[1].url, "/api/auth/bootstrap");
  assert.equal(browser.events[1].url.includes(TOKEN), false, "the token must not enter a URL");
  assert.deepEqual(browser.events[1].init, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ token: TOKEN }),
    credentials: "same-origin",
    cache: "no-store",
    redirect: "error",
    referrerPolicy: "no-referrer",
  });

  let applicationReleased = false;
  void first.then(() => {
    applicationReleased = true;
    browser.events.push({ type: "ready" });
  });
  await Promise.resolve();
  assert.equal(
    applicationReleased,
    false,
    "the application must remain gated while the bootstrap request is pending",
  );

  releaseResponse({ status: 204 });
  await first;
  await Promise.resolve();
  assert.equal(applicationReleased, true);
  assert.deepEqual(browser.events.map((event) => event.type), ["replace", "fetch", "ready"]);
}

{
  const browser = browserState("#settings");
  let fetches = 0;
  const coordinator = createOwnerAuthBootstrapCoordinator({
    location: browser.location,
    history: browser.history,
    fetch: async () => {
      fetches += 1;
      return { status: 204 };
    },
  });
  await coordinator.wait();
  assert.equal(fetches, 0, "an existing HttpOnly cookie needs no new bootstrap request");
  assert.equal(browser.location.hash, "#settings", "unrelated fragments must be preserved");
  assert.deepEqual(browser.events, []);
}

for (const fragment of [
  `#token=${TOKEN}&extra=1`,
  `#token=${TOKEN}&token=${TOKEN}`,
  "#token=short",
]) {
  const browser = browserState(fragment);
  let fetches = 0;
  const coordinator = createOwnerAuthBootstrapCoordinator({
    location: browser.location,
    history: browser.history,
    fetch: async () => {
      fetches += 1;
      return { status: 204 };
    },
  });
  assert.equal(browser.location.hash, "", "even a malformed token fragment must be removed");
  await assert.rejects(coordinator.wait(), /Invalid owner authentication URL/);
  assert.equal(fetches, 0, "a malformed fragment must never reach the bootstrap endpoint");
}

{
  const browser = browserState(`#token=${TOKEN}`);
  const coordinator = createOwnerAuthBootstrapCoordinator({
    location: browser.location,
    history: browser.history,
    fetch: async () => ({ status: 401 }),
  });
  await assert.rejects(
    coordinator.wait(),
    (error) => {
      assert.match(String(error), /Owner authentication failed/);
      assert.equal(String(error).includes(TOKEN), false, "errors must not reproduce the token");
      return true;
    },
  );
}

delete globalThis.localStorage;
delete globalThis.sessionStorage;

console.log("check-owner-auth-bootstrap: ok");
