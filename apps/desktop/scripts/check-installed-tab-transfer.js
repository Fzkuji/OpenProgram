const assert = require("node:assert/strict");
const { execFileSync } = require("node:child_process");

const CDP_HTTP = "http://127.0.0.1:9223";
const APP_ORIGIN = "http://127.0.0.1:18100";
const TIMEOUT_MS = 20_000;
const REQUEST_TIMEOUT_MS = 12_000;
const CLEANUP_TIMEOUT_MS = 2_000;

function withTimeout(promise, timeoutMs, description) {
  let timer = null;
  return Promise.race([
    promise,
    new Promise((_, reject) => {
      timer = setTimeout(
        () => reject(new Error(`timed out waiting for ${description}`)),
        timeoutMs,
      );
    }),
  ]).finally(() => {
    if (timer !== null) clearTimeout(timer);
  });
}

class CdpClient {
  constructor(url, options = {}) {
    this.url = url;
    this.timeoutMs = options.timeoutMs || REQUEST_TIMEOUT_MS;
    this.WebSocketImpl = options.WebSocketImpl || globalThis.WebSocket;
    this.nextId = 1;
    this.pending = new Map();
    this.listeners = new Map();
    this.socket = null;
  }

  async connect() {
    const socket = new this.WebSocketImpl(this.url);
    this.socket = socket;
    await withTimeout(new Promise((resolve, reject) => {
      const cleanup = () => {
        socket.removeEventListener("open", opened);
        socket.removeEventListener("error", failed);
        socket.removeEventListener("close", closed);
      };
      const opened = () => {
        cleanup();
        resolve();
      };
      const failed = (event) => {
        cleanup();
        reject(event?.error || new Error(`CDP socket error: ${this.url}`));
      };
      const closed = () => {
        cleanup();
        reject(new Error(`CDP socket closed before connect: ${this.url}`));
      };
      socket.addEventListener("open", opened);
      socket.addEventListener("error", failed);
      socket.addEventListener("close", closed);
    }), this.timeoutMs, `CDP connection ${this.url}`).catch((error) => {
      try { socket.close(); } catch { /* best effort */ }
      this.socket = null;
      throw error;
    });
    this.socket.addEventListener("message", (event) => {
      let message;
      try {
        message = JSON.parse(event.data);
      } catch (error) {
        this.rejectPending(error);
        return;
      }
      if (message.id) {
        const waiter = this.pending.get(message.id);
        if (!waiter) return;
        this.pending.delete(message.id);
        clearTimeout(waiter.timer);
        if (message.error) waiter.reject(new Error(JSON.stringify(message.error)));
        else waiter.resolve(message.result);
        return;
      }
      for (const listener of this.listeners.get(message.method) || []) {
        listener(message.params || {});
      }
    });
    this.socket.addEventListener("error", (event) => {
      this.rejectPending(event?.error || new Error(`CDP socket error: ${this.url}`));
    });
    this.socket.addEventListener("close", () => {
      this.rejectPending(new Error(`CDP socket closed: ${this.url}`));
      this.socket = null;
    });
    return this;
  }

  rejectPending(error) {
    for (const waiter of this.pending.values()) {
      clearTimeout(waiter.timer);
      waiter.reject(error);
    }
    this.pending.clear();
  }

  on(method, listener) {
    const listeners = this.listeners.get(method) || [];
    listeners.push(listener);
    this.listeners.set(method, listeners);
  }

  send(method, params = {}) {
    return new Promise((resolve, reject) => {
      if (!this.socket) {
        reject(new Error(`CDP socket is not connected: ${this.url}`));
        return;
      }
      const id = this.nextId++;
      const timer = setTimeout(() => {
        this.pending.delete(id);
        reject(new Error(`CDP request timed out: ${method}`));
      }, this.timeoutMs);
      this.pending.set(id, { resolve, reject, timer });
      try {
        this.socket.send(JSON.stringify({ id, method, params }));
      } catch (error) {
        clearTimeout(timer);
        this.pending.delete(id);
        reject(error);
      }
    });
  }

  close() {
    this.rejectPending(new Error(`CDP client closed: ${this.url}`));
    try { this.socket?.close(); } catch { /* already closed */ }
    this.socket = null;
  }
}

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

async function waitFor(check, description, timeoutMs = TIMEOUT_MS) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    const remaining = deadline - Date.now();
    const value = await withTimeout(
      Promise.resolve().then(check),
      Math.max(1, Math.min(1_000, remaining)),
      description,
    );
    if (value) return value;
    await sleep(50);
  }
  throw new Error(`timed out waiting for ${description}`);
}

async function json(path) {
  const response = await withTimeout(
    fetch(`${CDP_HTTP}${path}`),
    REQUEST_TIMEOUT_MS,
    `CDP HTTP ${path}`,
  );
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

async function installFailedSourceRemovalBreakpoint(client) {
  const scripts = [];
  client.on("Debugger.scriptParsed", (script) => {
    if (script.url.includes("/_next/static/chunks/")) scripts.push(script);
  });
  await client.send("Debugger.enable");
  for (const script of scripts) {
    const { scriptSource } = await client.send("Debugger.getScriptSource", {
      scriptId: script.scriptId,
    });
    const match = /\.sourceRemoved\([^,()]+,!1,!1\)/.exec(scriptSource);
    if (!match) continue;
    const index = match.index;
    const before = scriptSource.slice(0, index);
    const lineNumber = (before.match(/\n/g) || []).length;
    const lastNewline = before.lastIndexOf("\n");
    const columnNumber = index - lastNewline - 1;
    const result = await client.send("Debugger.setBreakpoint", {
      location: { scriptId: script.scriptId, lineNumber, columnNumber },
    });
    if (result.breakpointId) return result.breakpointId;
  }
  throw new Error("failed source-removal callsite was not found in installed assets");
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
  let browser = null;
  let source = null;
  let destination = null;
  const createdTargetIds = new Set();
  const destroyedTargetIds = new Set();
  let transfer = null;
  let destinationResidue = null;
  let baselineSource = null;
  let sourcePaused = false;
  let detachResult = null;
  try {
    browser = await new CdpClient(version.webSocketDebuggerUrl).connect();
    browser.on("Target.targetCreated", ({ targetInfo }) => {
      if (targetInfo?.type === "page" && !baselineTargetIds.includes(targetInfo.targetId)) {
        createdTargetIds.add(targetInfo.targetId);
      }
    });
    browser.on("Target.targetDestroyed", ({ targetId }) => {
      destroyedTargetIds.add(targetId);
    });
    await browser.send("Target.setDiscoverTargets", { discover: true });

    source = await new CdpClient(sourceTarget.webSocketDebuggerUrl).connect();
    await installFailedSourceRemovalBreakpoint(source);
    const sourcePausedPromise = new Promise((resolve) => {
      source.on("Debugger.paused", (detail) => {
        sourcePaused = true;
        resolve(detail);
      });
    });
    baselineSource = await sourceSnapshot(source);
    assert.equal(
      baselineSource.windowId,
      "main",
      "acceptance source must be the main window",
    );

    transfer = await evaluate(source, `(() => {
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
      return { token, tabId };
    })()`);

    detachResult = evaluate(source, `window.openprogramDesktop.tabTransfer.detach(
      ${JSON.stringify(transfer.token)}
    )`);
    await withTimeout(
      sourcePausedPromise,
      TIMEOUT_MS,
      "source renderer to reach failed source removal",
    );
    const destinationTarget = await waitFor(async () => {
      const targets = await shellTargets();
      return targets.find((target) => !baselineTargetIds.includes(target.id)) || null;
    }, "the detached renderer target to appear");
    destination = await new CdpClient(destinationTarget.webSocketDebuggerUrl).connect();
    const staged = await waitFor(async () => {
      const snapshot = await evaluate(destination, `(() => {
        const windowId = window.openprogramDesktop?.windowId ?? null;
        const tabId = ${JSON.stringify(transfer.tabId)};
        const token = ${JSON.stringify(transfer.token)};
        const domTabs = [...document.querySelectorAll('[role="tab"][data-tab-id]')]
          .map((tab) => tab.getAttribute('data-tab-id'));
        const persisted = JSON.parse(
          localStorage.getItem(\`centerTabs:\${windowId}\`) || 'null'
        );
        const journal = JSON.parse(
          localStorage.getItem(\`openprogram.tabTransferJournal:\${windowId}\`) || 'null'
        );
        return {
          windowId,
          domHasTab: domTabs.includes(tabId),
          persistedHasTab: !!persisted?.tabs?.some((tab) => tab.id === tabId),
          journalHasToken: !!journal?.entries?.[token],
        };
      })()`);
      return snapshot.domHasTab
        && snapshot.journalHasToken
        ? snapshot
        : null;
    }, "the destination renderer to inspect, accept, and stage the tab");

    await source.send("Debugger.resume");
    sourcePaused = false;
    const detached = { destinationId: await detachResult };
    assert.ok(detached.destinationId, "detach did not create a destination");
    transfer.destinationId = detached.destinationId;
    assert.equal(
      staged.windowId,
      transfer.destinationId,
      "staged renderer identity does not match the detached destination",
    );

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
    assert.deepEqual(
      after,
      baselineSource,
      "source tab state or persistent storage changed",
    );
    console.log(JSON.stringify({
      status: "PASS",
      installedApp: "/Applications/OpenProgram.app",
      worker: APP_ORIGIN,
      sourceWindowId: baselineSource.windowId,
      destinationWindowId: transfer.destinationId,
      baselineShellTargets: baselineTargetIds.length,
      createdRendererTargets: createdTargetIds.size,
      destroyedRendererTargets: [...createdTargetIds]
        .filter((id) => destroyedTargetIds.has(id)).length,
      destinationRendererStaged: true,
      sourceStateUnchanged: true,
      destinationStorageClean: true,
    }, null, 2));
  } finally {
    if (sourcePaused && source) {
      try {
        await withTimeout(
          source.send("Debugger.resume"),
          CLEANUP_TIMEOUT_MS,
          "source debugger resume",
        );
        sourcePaused = false;
      } catch {
        // Closing the CDP client below releases the paused renderer.
      }
    }
    if (detachResult) {
      await withTimeout(
        Promise.resolve(detachResult).catch(() => null),
        CLEANUP_TIMEOUT_MS,
        "detach result cleanup",
      ).catch(() => null);
    }
    if (transfer?.token) {
      try {
        await withTimeout(
          evaluate(
            source,
            `window.openprogramDesktop.tabTransfer.cancel(${JSON.stringify(transfer.token)})`,
          ),
          CLEANUP_TIMEOUT_MS,
          "transfer cancellation",
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
        }, "cleanup to restore the shell target set", CLEANUP_TIMEOUT_MS);
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
          await withTimeout(
            evaluate(
              source,
              `for (const key of ${JSON.stringify(dirtyKeys)}) localStorage.removeItem(key)`,
            ),
            CLEANUP_TIMEOUT_MS,
            "destination residue cleanup",
          );
        }
      } catch {
        // The process exits failed; never replace the diagnostic with cleanup noise.
      }
    }
    destination?.close();
    source?.close();
    browser?.close();
  }
}

if (require.main === module) {
  main().catch((error) => {
    console.error(`installed tab-transfer acceptance failed: ${error.stack || error}`);
    process.exitCode = 1;
  });
}

module.exports = { CdpClient, waitFor, withTimeout };
