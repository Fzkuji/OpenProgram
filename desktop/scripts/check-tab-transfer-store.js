const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");

const modulePath = path.join(__dirname, "..", "tab-transfer-store.js");
const {
  loadTransferDecisions,
  saveTransferDecisionsAtomic,
  putTransferDecision,
  ackTransferDecision,
} = require(modulePath);

const EMPTY_STORE = { version: 1, decisions: {} };

function makeTempStore() {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "openprogram-transfers-"));
  return {
    dir,
    filePath: path.join(dir, "tab-transfers.json"),
    cleanup() { fs.rmSync(dir, { recursive: true, force: true }); },
  };
}

function decision(token = "transfer-1") {
  return {
    token,
    status: "committed",
    sourceId: "window-source",
    destinationId: "window-destination",
    sourceEmpty: false,
    requiredRoles: [
      { role: "source", windowId: "window-source" },
      { role: "source", windowId: "window-source" },
      { role: "destination", windowId: "window-destination" },
    ],
    finalizedRoles: [],
    decidedAt: 1234,
  };
}

function installOneShotFailure(method, failAt) {
  const original = fs[method];
  let calls = 0;
  fs[method] = function injectedFailure(...args) {
    calls += 1;
    if (calls === failAt) {
      const error = new Error(`injected ${method} failure at call ${failAt}`);
      error.code = "EIO";
      throw error;
    }
    return original.apply(this, args);
  };
  return () => { fs[method] = original; };
}

function assertNoTemporaryFiles(dir) {
  const leftovers = fs.readdirSync(dir).filter((name) => name.endsWith(".tmp"));
  assert.deepEqual(leftovers, []);
}

function checkLoadSavePutAndRestart() {
  const store = makeTempStore();
  try {
    assert.deepEqual(loadTransferDecisions(store.filePath), EMPTY_STORE);

    saveTransferDecisionsAtomic(store.filePath, EMPTY_STORE);
    assert.equal(fs.statSync(store.filePath).mode & 0o777, 0o600);
    assert.deepEqual(loadTransferDecisions(store.filePath), EMPTY_STORE);

    const saved = putTransferDecision(store.filePath, decision());
    assert.equal(saved.token, "transfer-1");
    assert.deepEqual(saved.requiredRoles, [
      { role: "source", windowId: "window-source" },
      { role: "destination", windowId: "window-destination" },
    ]);

    delete require.cache[require.resolve(modulePath)];
    const restarted = require(modulePath);
    assert.deepEqual(
      restarted.loadTransferDecisions(store.filePath),
      { version: 1, decisions: { "transfer-1": saved } },
    );
    assert.equal(fs.statSync(store.filePath).mode & 0o777, 0o600);
    assertNoTemporaryFiles(store.dir);
  } finally {
    store.cleanup();
  }
}

function checkCorruptRecovery() {
  const store = makeTempStore();
  try {
    fs.writeFileSync(store.filePath, "{not-json", { mode: 0o600 });
    assert.deepEqual(loadTransferDecisions(store.filePath), EMPTY_STORE);
    assert.equal(fs.readFileSync(store.filePath, "utf8"), "{not-json");

    fs.writeFileSync(store.filePath, JSON.stringify({ version: 2, decisions: {} }));
    assert.deepEqual(loadTransferDecisions(store.filePath), EMPTY_STORE);

    fs.writeFileSync(store.filePath, JSON.stringify({ version: 1, decisions: [] }));
    assert.deepEqual(loadTransferDecisions(store.filePath), EMPTY_STORE);
  } finally {
    store.cleanup();
  }
}

function checkAtomicFailurePreservesPrior(method, failAt) {
  const store = makeTempStore();
  const prior = {
    version: 1,
    decisions: { prior: decision("prior") },
  };
  try {
    saveTransferDecisionsAtomic(store.filePath, prior);
    const restore = installOneShotFailure(method, failAt);
    try {
      assert.throws(
        () => saveTransferDecisionsAtomic(store.filePath, {
          version: 1,
          decisions: { replacement: decision("replacement") },
        }),
        new RegExp(`injected ${method} failure`),
      );
    } finally {
      restore();
    }
    assert.deepEqual(loadTransferDecisions(store.filePath), prior);
    assertNoTemporaryFiles(store.dir);
  } finally {
    store.cleanup();
  }
}

function checkPutReturnsOnlyAfterDurableSave() {
  const store = makeTempStore();
  try {
    saveTransferDecisionsAtomic(store.filePath, EMPTY_STORE);
    const restore = installOneShotFailure("renameSync", 1);
    try {
      assert.throws(
        () => putTransferDecision(store.filePath, decision("not-durable")),
        /injected renameSync failure/,
      );
    } finally {
      restore();
    }
    assert.deepEqual(loadTransferDecisions(store.filePath), EMPTY_STORE);
    assertNoTemporaryFiles(store.dir);
  } finally {
    store.cleanup();
  }
}

function checkReadFailureCannotOverwritePriorStore() {
  const store = makeTempStore();
  const prior = {
    version: 1,
    decisions: { prior: decision("prior") },
  };
  try {
    saveTransferDecisionsAtomic(store.filePath, prior);
    const restore = installOneShotFailure("readFileSync", 1);
    try {
      assert.throws(
        () => putTransferDecision(store.filePath, decision("replacement")),
        /injected readFileSync failure/,
      );
    } finally {
      restore();
    }
    assert.deepEqual(loadTransferDecisions(store.filePath), prior);
    assertNoTemporaryFiles(store.dir);
  } finally {
    store.cleanup();
  }
}

function checkAcknowledgements() {
  const store = makeTempStore();
  try {
    const saved = putTransferDecision(store.filePath, decision("ack-token"));
    const beforeRejectedAck = fs.readFileSync(store.filePath);
    assert.throws(
      () => ackTransferDecision(
        store.filePath,
        "missing-token",
        { role: "source", windowId: "window-source" },
      ),
      /Unknown transfer decision/,
    );
    assert.throws(
      () => ackTransferDecision(
        store.filePath,
        "ack-token",
        { role: "source", windowId: "wrong-window" },
      ),
      /not required/,
    );
    assert.deepEqual(fs.readFileSync(store.filePath), beforeRejectedAck);

    const sourceAck = ackTransferDecision(
      store.filePath,
      "ack-token",
      { role: "source", windowId: "window-source" },
    );
    assert.equal(sourceAck.complete, false);
    assert.deepEqual(sourceAck.decision.finalizedRoles, [
      { role: "source", windowId: "window-source" },
    ]);
    const afterSourceAck = fs.readFileSync(store.filePath);

    const duplicateAck = ackTransferDecision(
      store.filePath,
      "ack-token",
      { role: "source", windowId: "window-source" },
    );
    assert.deepEqual(duplicateAck, sourceAck);
    assert.deepEqual(fs.readFileSync(store.filePath), afterSourceAck);

    const restore = installOneShotFailure("renameSync", 1);
    try {
      assert.throws(
        () => ackTransferDecision(
          store.filePath,
          "ack-token",
          { role: "destination", windowId: "window-destination" },
        ),
        /injected renameSync failure/,
      );
    } finally {
      restore();
    }
    assert.deepEqual(
      loadTransferDecisions(store.filePath).decisions["ack-token"],
      sourceAck.decision,
    );

    const destinationAck = ackTransferDecision(
      store.filePath,
      "ack-token",
      { role: "destination", windowId: "window-destination" },
    );
    assert.equal(destinationAck.complete, true);
    assert.deepEqual(destinationAck.decision, {
      ...saved,
      finalizedRoles: [
        { role: "source", windowId: "window-source" },
        { role: "destination", windowId: "window-destination" },
      ],
    });
    assert.deepEqual(loadTransferDecisions(store.filePath), EMPTY_STORE);
    assert.throws(
      () => ackTransferDecision(
        store.filePath,
        "ack-token",
        { role: "destination", windowId: "window-destination" },
      ),
      /Unknown transfer decision/,
    );
    assertNoTemporaryFiles(store.dir);
  } finally {
    store.cleanup();
  }
}

checkLoadSavePutAndRestart();
checkCorruptRecovery();
checkAtomicFailurePreservesPrior("writeFileSync", 1);
checkAtomicFailurePreservesPrior("fsyncSync", 1);
checkAtomicFailurePreservesPrior("renameSync", 1);
checkAtomicFailurePreservesPrior("fsyncSync", 2);
checkPutReturnsOnlyAfterDurableSave();
checkReadFailureCannotOverwritePriorStore();
checkAcknowledgements();
console.log("tab transfer decision store checks passed");
