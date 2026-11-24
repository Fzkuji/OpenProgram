// The owner token arrives once in the URL fragment. The bootstrap removes it
// from the address bar before anything else, exchanges it for an HttpOnly
// cookie with one POST, keeps it only in memory (never Web Storage, never a
// URL), and holds the application until the exchange succeeds.
import assert from "node:assert/strict";
import { after, before, describe, test } from "node:test";

import { createOwnerAuthBootstrapCoordinator } from "../../lib/net/auth/owner-auth-bootstrap.ts";

const TOKEN = "A".repeat(43);

function browserState(hash) {
  const events = [];
  const location = { hash, pathname: "/chat", search: "?profile=worker" };
  const history = {
    state: { navigation: "kept" },
    replaceState(state, title, url) {
      events.push({ type: "replace", state, title, url });
      location.hash = "";
    },
  };
  return { events, history, location };
}

function coordinatorFor(browser, fetch) {
  return createOwnerAuthBootstrapCoordinator({ location: browser.location, history: browser.history, fetch });
}

describe("owner auth bootstrap", () => {
  // Any read of Web Storage fails the test: the token lives only in memory.
  before(() => {
    for (const name of ["localStorage", "sessionStorage"]) {
      Object.defineProperty(globalThis, name, {
        configurable: true,
        get() { throw new Error(`owner bootstrap must not read ${name}`); },
      });
    }
  });
  after(() => {
    delete globalThis.localStorage;
    delete globalThis.sessionStorage;
  });

  test("removes the token fragment at once, then POSTs it in the body and gates the app until the reply", async () => {
    const browser = browserState(`#token=${TOKEN}`);
    let releaseResponse;
    const response = new Promise((resolve) => { releaseResponse = resolve; });
    const coordinator = coordinatorFor(browser, async (url, init) => {
      browser.events.push({ type: "fetch", url, init, hash: browser.location.hash });
      return response;
    });

    assert.deepEqual(browser.events, [{ type: "replace", state: { navigation: "kept" }, title: "", url: "/chat?profile=worker" }]);

    const first = coordinator.wait();
    assert.strictEqual(coordinator.wait(), first, "concurrent consumers share one request");
    const [, request] = browser.events;
    assert.equal(request.hash, "", "the fragment is gone before the request");
    assert.equal(request.url, "/api/auth/bootstrap");
    assert.deepEqual(request.init, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ token: TOKEN }),
      credentials: "same-origin",
      cache: "no-store",
      redirect: "error",
      referrerPolicy: "no-referrer",
    });

    let released = false;
    void first.then(() => { released = true; browser.events.push({ type: "ready" }); });
    await Promise.resolve();
    assert.equal(released, false, "the application waits while the request is pending");

    releaseResponse({ status: 204 });
    await first;
    await Promise.resolve();
    assert.deepEqual(browser.events.map((event) => event.type), ["replace", "fetch", "ready"]);
  });

  test("without a token fragment it sends nothing and leaves other fragments alone", async () => {
    const browser = browserState("#settings");
    let fetches = 0;
    await coordinatorFor(browser, async () => { fetches += 1; return { status: 204 }; }).wait();
    assert.equal(fetches, 0);
    assert.equal(browser.location.hash, "#settings");
    assert.deepEqual(browser.events, []);
  });

  test("adoptToken exchanges a rotated token without writing it into the URL, and rejects a malformed one", async () => {
    const browser = browserState("#settings");
    const requests = [];
    const coordinator = coordinatorFor(browser, async (url, init) => { requests.push({ url, body: init.body }); return { status: 204 }; });
    await coordinator.wait();
    await coordinator.adoptToken(TOKEN);
    assert.deepEqual(requests.map(({ url, body }) => [url, JSON.parse(body).token]), [["/api/auth/bootstrap", TOKEN]]);
    assert.equal(browser.location.hash, "#settings");
    assert.throws(() => coordinator.adoptToken("short"), /Owner authentication failed/);
  });

  for (const fragment of [`#token=${TOKEN}&extra=1`, `#token=${TOKEN}&token=${TOKEN}`, "#token=short"]) {
    test(`a malformed fragment (${fragment.slice(0, 12)}…${fragment.slice(-8)}) is removed and never sent`, async () => {
      const browser = browserState(fragment);
      let fetches = 0;
      const coordinator = coordinatorFor(browser, async () => { fetches += 1; return { status: 204 }; });
      assert.equal(browser.location.hash, "");
      await assert.rejects(coordinator.wait(), /Invalid owner authentication URL/);
      assert.equal(fetches, 0);
    });
  }

  test("a pairing fragment is removed at once and redeemed for a device cookie", async () => {
    const browser = browserState(`#pair=${TOKEN}`);
    const requests = [];
    const coordinator = coordinatorFor(browser, async (url, init) => {
      requests.push({ url, init, hash: browser.location.hash });
      return { status: 204 };
    });
    assert.equal(browser.location.hash, "");
    await coordinator.wait();
    assert.equal(requests.length, 1);
    assert.equal(requests[0].url, "/api/auth/pair");
    assert.equal(requests[0].hash, "");
    assert.deepEqual(JSON.parse(requests[0].init.body), { code: TOKEN });
    assert.equal(requests[0].init.credentials, "same-origin");
  });

  test("a pairing link naming the computer's other addresses redeems the same code", async () => {
    const browser = browserState(`#pair=${TOKEN}&via=https%3A%2F%2Fstudio.tail1.ts.net`);
    const bodies = [];
    const coordinator = coordinatorFor(browser, async (url, init) => {
      bodies.push(JSON.parse(init.body));
      return { status: 204 };
    });
    assert.equal(browser.location.hash, "");
    await coordinator.wait();
    assert.deepEqual(bodies, [{ code: TOKEN }]);
  });

  test("a spent pairing link still opens on a device that is already paired", async () => {
    const urls = [];
    const coordinator = coordinatorFor(browserState(`#pair=${TOKEN}`), async (url) => {
      urls.push(url);
      return { status: url === "/api/auth/pair" ? 401 : 204 };
    });
    await coordinator.wait();
    assert.deepEqual(urls, ["/api/auth/pair", "/api/auth/session"]);
  });

  test("a spent pairing link on an unpaired device says so without repeating the code", async () => {
    const coordinator = coordinatorFor(browserState(`#pair=${TOKEN}`), async () => ({ status: 401 }));
    await assert.rejects(coordinator.wait(), (error) => {
      assert.match(String(error), /pairing link has expired/);
      assert.equal(String(error).includes(TOKEN), false);
      return true;
    });
  });

  test("a refused exchange fails without repeating the token in the error", async () => {
    const coordinator = coordinatorFor(browserState(`#token=${TOKEN}`), async () => ({ status: 401 }));
    await assert.rejects(coordinator.wait(), (error) => {
      assert.match(String(error), /Owner authentication failed/);
      assert.equal(String(error).includes(TOKEN), false);
      return true;
    });
  });
});
