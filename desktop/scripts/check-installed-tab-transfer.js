const assert = require("node:assert/strict");
const { execFileSync } = require("node:child_process");

const CDP_HTTP = "http://127.0.0.1:9223";
const APP_ORIGIN = "http://127.0.0.1:18100";
const TIMEOUT_MS = 12_000;

class CdpClient {
  constructor(url) {
    this.url = url;
    this.nextId = 1;
    this.pending = new Map();
    this.listeners = new Map();
    this.socket = null;
  }

  async connect() {
    this.socket = new WebSocket(this.url);
    await new Promise((resolve, reject) => {
      this.socket.addEventListener("open", resolve, { once: true });
      this.socket.addEventListener("error", reject, { once: true });
    });
    this.socket.addEventListener("message", (event) => {
      const message = JSON.parse(event.data);
      if (message.id) {
        const waiter = this.pending.get(message.id);
        if (!waiter) return;
        this.pending.delete(message.id);
        if (message.error) waiter.reject(new Error(JSON.stringify(message.error)));
        else waiter.resolve(message.result);
        return;
      }
      for (const listener of this.listeners.get(message.method) || []) {
        listener(message.params || {});
      }
    });
    return this;
  }

  on(method, listener) {
    const listeners = this.listeners.get(method) || [];
    listeners.push(listener);
    this.listeners.set(method, listeners);
  }

  send(method, params = {}) {
    return new Promise((resolve, reject) => {
      const id = this.nextId++;
      this.pending.set(id, { resolve, reject });
      this.socket.send(JSON.stringify({ id, method, params }));
    });
  }

  close() {
    this.socket?.close();
  }
}

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

async function waitFor(check, description) {
  const deadline = Date.now() + TIMEOUT_MS;
  while (Date.now() < deadline) {
    const value = await check();
    if (value) return value;
    await sleep(50);
  }
  throw new Error(`timed out waiting for ${description}`);
}

async function json(path) {
  const response = await fetch(`${CDP_HTTP}${path}`);
  assert.equal(response.status, 200, `${path} must return 200`);
  return response.json();
}

async function evaluate(client, expression) {
  const response = await client.send("Runtime.evaluate", {
    expression,
    awaitPromise: true,
    returnByValue: true,
  });
  if (response.exceptionDetails) {
    throw new Error(response.exceptionDetails.exception?.description || "evaluation failed");
  }
  return response.result.value;
}

async function shellTargets() {
  const targets = await json("/json/list");
  return targets.filter((target) =>
    target.type === "page"
      && (target.url.startsWith(`${APP_ORIGIN}/`)
        || target.url.startsWith("http://localhost:18100/")),
  );
}

async function sourceSnapshot(source) {
  return evaluate(source, `(() => {
    const windowId = window.openprogramDesktop?.windowId ?? null;
    const keys = [
      \`centerTabs:\${windowId}\`,
      \`openprogram.sessionDraftState:\${windowId}\`,
      \`openprogram.tabTransferJournal:\${windowId}\`,
    ];
    return {
      windowId,
      storage: Object.fromEntries(keys.map((key) => [key, localStorage.getItem(key)])),
      tabs: [...document.querySelectorAll('[role="tab"][data-tab-id]')].map((tab) => ({
        id: tab.getAttribute('data-tab-id'),
        selected: tab.getAttribute('aria-selected'),
      })),
    };
  })()`);
}

async function main() {
  const processList = execFileSync("ps", ["-axo", "command="], { encoding: "utf8" });
  assert.match(
    processList,
    /\/Applications\/OpenProgram\.app\/Contents\/MacOS\/OpenProgram(?:\s|$)/,
    "installed /Applications/OpenProgram.app must be running",
  );
  const health = await fetch(`${APP_ORIGIN}/healthz`);
  assert.equal(health.status, 200, "default 18100 worker must be healthy");

  const version = await json("/json/version");
  assert.match(version.Browser || "", /^Chrome\//, "CDP browser endpoint is unavailable");
  assert.match(version["User-Agent"] || "", /openprogram-desktop\//);

  const initialTargets = await shellTargets();
  assert.ok(initialTargets.length >= 1, "no installed App shell target found");
  const sourceTarget = initialTargets.find((target) => target.url.endsWith("/chat"))
    || initialTargets[0];
  const baselineTargetIds = initialTargets.map((target) => target.id).sort();

  const browser = await new CdpClient(version.webSocketDebuggerUrl).connect();
  const createdTargetIds = new Set();
  const destroyedTargetIds = new Set();
  browser.on("Target.targetCreated", ({ targetInfo }) => {
    if (targetInfo?.type === "page" && !baselineTargetIds.includes(targetInfo.targetId)) {
      createdTargetIds.add(targetInfo.targetId);
    }
  });
  browser.on("Target.targetDestroyed", ({ targetId }) => {
    destroyedTargetIds.add(targetId);
  });
  await browser.send("Target.setDiscoverTargets", { discover: true });

  const source = await new CdpClient(sourceTarget.webSocketDebuggerUrl).connect();
  const before = await sourceSnapshot(source);
  assert.equal(before.windowId, "main", "acceptance source must be the main window");

  let transfer = null;
  let destinationResidue = null;
  try {
    transfer = await evaluate(source, `(async () => {
      const bridge = window.openprogramDesktop;
      if (!bridge?.tabTransfer) throw new Error('tabTransfer bridge unavailable');
      const tabId = \`acceptance-\${crypto.randomUUID()}\`;
      const payload = {
        tabs: [{ id: tabId, kind: 'ntp', title: 'New tab' }],
        source: { windowId: bridge.windowId, kind: 'tab' },
        fileDrafts: [],
        chats: [],
      };
      const token = bridge.tabTransfer.prepare(payload);
      if (!token) throw new Error('prepare rejected temporary payload');
      const destinationId = await bridge.tabTransfer.detach(token);
      if (!destinationId) throw new Error('detach did not create a destination');
      return { token, destinationId, tabId };
    })()`);

    await waitFor(
      () => createdTargetIds.size > 0 && [...createdTargetIds].some((id) => destroyedTargetIds.has(id)),
      "the detached renderer target to be created and destroyed",
    );
    await waitFor(async () => {
      const status = await evaluate(
        source,
        `window.openprogramDesktop.tabTransfer.status(${JSON.stringify(transfer.token)})`,
      );
      return status === null;
    }, "the transfer transaction to clear");
    await waitFor(async () => {
      const ids = (await shellTargets()).map((target) => target.id).sort();
      return JSON.stringify(ids) === JSON.stringify(baselineTargetIds);
    }, "the installed App shell target set to return to baseline");

    destinationResidue = await evaluate(source, `(() => {
      const id = ${JSON.stringify(transfer.destinationId)};
      const keys = [
        \`centerTabs:\${id}\`,
        \`openprogram.sessionDraftState:\${id}\`,
        \`openprogram.tabTransferJournal:\${id}\`,
      ];
      return Object.fromEntries(keys.map((key) => [key, localStorage.getItem(key)]));
    })()`);
    assert.deepEqual(
      destinationResidue,
      Object.fromEntries(Object.keys(destinationResidue).map((key) => [key, null])),
      "rolled-back detached window left persistent storage",
    );

    const after = await sourceSnapshot(source);
    assert.deepEqual(after, before, "source tab state or persistent storage changed");
    console.log(JSON.stringify({
      status: "PASS",
      installedApp: "/Applications/OpenProgram.app",
      worker: APP_ORIGIN,
      sourceWindowId: before.windowId,
      destinationWindowId: transfer.destinationId,
      baselineShellTargets: baselineTargetIds.length,
      createdRendererTargets: createdTargetIds.size,
      destroyedRendererTargets: [...createdTargetIds]
        .filter((id) => destroyedTargetIds.has(id)).length,
      sourceStateUnchanged: true,
      destinationStorageClean: true,
    }, null, 2));
  } finally {
    if (transfer?.token) {
      try {
        await evaluate(
          source,
          `window.openprogramDesktop.tabTransfer.cancel(${JSON.stringify(transfer.token)})`,
        );
      } catch {
        // The expected rollback removes the token before this idempotent cleanup.
      }
    }
    if (transfer?.destinationId) {
      try {
        await waitFor(async () => {
          const ids = (await shellTargets()).map((target) => target.id).sort();
          return JSON.stringify(ids) === JSON.stringify(baselineTargetIds);
        }, "cleanup to restore the shell target set");
      } catch {
        // Preserve the original failure; cancel above requested normal cleanup.
      }
      try {
        const residue = destinationResidue || await evaluate(source, `(() => {
          const id = ${JSON.stringify(transfer.destinationId)};
          const keys = [
            \`centerTabs:\${id}\`,
            \`openprogram.sessionDraftState:\${id}\`,
            \`openprogram.tabTransferJournal:\${id}\`,
          ];
          return Object.fromEntries(keys.map((key) => [key, localStorage.getItem(key)]));
        })()`);
        const dirtyKeys = Object.entries(residue)
          .filter(([, value]) => value !== null)
          .map(([key]) => key);
        if (dirtyKeys.length > 0) {
          await evaluate(
            source,
            `for (const key of ${JSON.stringify(dirtyKeys)}) localStorage.removeItem(key)`,
          );
        }
      } catch {
        // The process exits failed; never replace the diagnostic with cleanup noise.
      }
    }
    source.close();
    browser.close();
  }
}

main().catch((error) => {
  console.error(`installed tab-transfer acceptance failed: ${error.stack || error}`);
  process.exitCode = 1;
});
