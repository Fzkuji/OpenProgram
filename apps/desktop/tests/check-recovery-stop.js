"use strict";
// How the recovery page stops the worker before it moves `current`. Windows
// has no signal to ask with, so the request is the `worker.stop` file the
// worker reads; the process tree is ended only when that was not answered.
// The Windows branch runs here against a stand-in for the process table and
// for taskkill; it has not run on Windows.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const recovery = require("../shell/recovery");

const PID = 4242;

function temporaryRoot() {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), "recovery-stop-"));
  const root = path.join(directory, "state", "runtime");
  fs.mkdirSync(root, { recursive: true });
  fs.writeFileSync(path.join(directory, "state", "worker.pid"), `${PID} 1700000000\n`);
  return root;
}

const stopFile = (root) => path.join(path.dirname(root), "worker.stop");

async function checkAnOrderlyWindowsStopKillsNothing() {
  const root = temporaryRoot();
  const ended = [];
  let seen = null;
  // The worker: gone once it has read a request that names it.
  const isAlive = (pid) => {
    assert.equal(pid, PID);
    try {
      seen = fs.readFileSync(stopFile(root), "utf8");
    } catch {
      return true;
    }
    return seen.trim() !== String(PID);
  };
  await recovery.stopWorker(root, "win32", {
    isAlive, endTree: (pid) => ended.push(pid), orderlyMs: 2_000, waitMs: 4_000, pollMs: 5,
  });
  assert.equal(seen, `${PID}\n`, "the request names the worker's PID the way lifecycle.request_stop writes it");
  assert.deepEqual(ended, [], "a worker that stops when asked is not ended by force");
  assert.equal(fs.existsSync(stopFile(root)), false, "the request is cleared, so a reused PID is never asked");
  assert.equal(fs.existsSync(path.join(root, "recovery.log")), false, "nothing to report");
}

async function checkAWindowsWorkerThatIgnoresTheRequestIsEnded() {
  const root = temporaryRoot();
  const ended = [];
  let alive = true;
  let askedBeforeEnd = false;
  const started = Date.now();
  await recovery.stopWorker(root, "win32", {
    isAlive: () => alive,
    endTree: (pid) => {
      askedBeforeEnd = fs.existsSync(stopFile(root));
      ended.push([pid, Date.now() - started]);
      alive = false;
    },
    orderlyMs: 120, waitMs: 2_000, pollMs: 5,
  });
  assert.equal(ended.length, 1);
  assert.equal(ended[0][0], PID);
  assert.ok(ended[0][1] >= 100, `the orderly wait comes first (ended after ${ended[0][1]} ms)`);
  assert.equal(askedBeforeEnd, true, "it was asked before it was ended");
  assert.equal(fs.existsSync(stopFile(root)), false);
  assert.match(fs.readFileSync(path.join(root, "recovery.log"), "utf8"), /PID 4242\) did not stop within 120 ms/);
}

async function checkAWindowsWorkerThatSurvivesChangesNothing() {
  const root = temporaryRoot();
  const ended = [];
  await assert.rejects(
    recovery.stopWorker(root, "win32", {
      isAlive: () => true, endTree: (pid) => ended.push(pid), orderlyMs: 30, waitMs: 120, pollMs: 5,
    }),
    /did not stop, so nothing was changed/,
  );
  assert.deepEqual(ended, [PID], "force is tried once");
  assert.equal(fs.existsSync(stopFile(root)), false, "no request is left for a later process");
}

async function checkNoWorkerMeansNothingToDo() {
  const root = temporaryRoot();
  const ended = [];
  await recovery.stopWorker(root, "win32", { isAlive: () => false, endTree: (pid) => ended.push(pid) });
  assert.deepEqual(ended, []);
  assert.equal(fs.existsSync(stopFile(root)), false);
}

async function checkPosixStillSignals() {
  const root = temporaryRoot();
  const signals = [];
  const realKill = process.kill;
  let alive = true;
  process.kill = (pid, signal) => {
    signals.push([pid, signal]);
    alive = false;
    return true;
  };
  try {
    await recovery.stopWorker(root, "linux", { isAlive: () => alive, endTree: () => assert.fail("no taskkill"), pollMs: 5 });
  } finally {
    process.kill = realKill;
  }
  assert.deepEqual(signals, [[PID, "SIGTERM"]]);
  assert.equal(fs.existsSync(stopFile(root)), false, "the file is the Windows route only");
}

(async () => {
  await checkAnOrderlyWindowsStopKillsNothing();
  await checkAWindowsWorkerThatIgnoresTheRequestIsEnded();
  await checkAWindowsWorkerThatSurvivesChangesNothing();
  await checkNoWorkerMeansNothingToDo();
  await checkPosixStillSignals();
  console.log("recovery stop ok");
})().catch((error) => {
  console.error(error);
  process.exit(1);
});
