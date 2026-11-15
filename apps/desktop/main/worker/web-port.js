"use strict";
// Which port this App's worker listens on, read from the same files the worker
// settles it with (openprogram/operations/worker/lifecycle.py,
// claim_worker_port). The worker is the only one that chooses a port: at a
// profile's first start it takes the lowest free port from 18100 up and
// records it in <state>/web-port, and it moves the record when another program
// or another account holds that port at a later start. The App only reads, so
// it and the worker always agree, and a move is seen as a changed record.
const fs = require("node:fs");
const net = require("node:net");
const os = require("node:os");
const path = require("node:path");

const FIRST_WEB_PORT = 18100;
// The ports a profile chooses from (ports.py WEB_PORT_SPAN); the development
// instance's 18200 is outside them.
const WEB_PORT_SPAN = 100;
const RECORD_FILE = "web-port";

/** The profile's state directory, as openprogram.state.paths.get_state_dir names it. */
function stateDirectory(env = process.env, home = os.homedir()) {
  const profile = (env.OPENPROGRAM_PROFILE || "").trim();
  return path.join(home, profile ? `.openprogram-${profile}` : ".openprogram");
}

function validPort(value) {
  const port = Number(value);
  return Number.isInteger(port) && port >= 1024 && port <= 65535 ? port : null;
}

function settingsPort(stateDir) {
  try {
    const config = JSON.parse(fs.readFileSync(path.join(stateDir, "config.json"), "utf8"));
    return validPort(config?.ui?.web_port);
  } catch {
    return null;
  }
}

function recordedPort(stateDir) {
  try {
    return validPort(fs.readFileSync(path.join(stateDir, RECORD_FILE), "utf8").trim());
  } catch {
    return null;
  }
}

/**
 * The port and where it comes from: "env" (OPENPROGRAM_WEB_PORT), "instance"
 * (the development App's instance.json), "settings" (ui.web_port), "record"
 * (chosen at the profile's first start) or "default" (no worker has started
 * yet; it will record 18100 unless something else holds it).
 */
function resolveWebPort({ env = process.env, instance, stateDir }) {
  const fromEnv = validPort(env.OPENPROGRAM_WEB_PORT);
  if (fromEnv) return { port: fromEnv, source: "env" };
  if (instance?.development) return { port: instance.port, source: "instance" };
  const fromSettings = settingsPort(stateDir);
  if (fromSettings) return { port: fromSettings, source: "settings" };
  const fromRecord = recordedPort(stateDir);
  if (fromRecord) return { port: fromRecord, source: "record" };
  return { port: FIRST_WEB_PORT, source: "default" };
}

/** Whether `value` (a URL's port string or a number) is one a profile chooses for itself. */
function isChosenWebPort(value) {
  const port = validPort(value);
  return port !== null && port >= FIRST_WEB_PORT && port < FIRST_WEB_PORT + WEB_PORT_SPAN;
}

/** Whether the worker never moves this port: the user or the instance set it. */
function portIsFixed(choice) {
  return choice.source === "env" || choice.source === "instance" || choice.source === "settings";
}

/**
 * The environment the worker needs to listen on `choice`. Only a port the
 * worker cannot read from the state files itself is passed; a chosen port is
 * left to the worker so it can move it.
 */
function workerPortEnv(choice) {
  return choice.source === "env" || choice.source === "instance"
    ? { OPENPROGRAM_WEB_PORT: String(choice.port) }
    : {};
}

/** The port the files name now, when the worker moved it after this App started. */
function movedWebPort(choice, { env = process.env, instance, stateDir }) {
  if (portIsFixed(choice)) return null;
  const now = resolveWebPort({ env, instance, stateDir });
  return now.port !== choice.port ? now.port : null;
}

/** Whether anything accepts connections on 127.0.0.1:`port`. */
function portListening(port, timeoutMs = 500) {
  return new Promise((resolve) => {
    const socket = net.connect({ host: "127.0.0.1", port: Number(port) });
    const done = (answer) => {
      socket.destroy();
      resolve(answer);
    };
    socket.setTimeout(timeoutMs, () => done(false));
    socket.once("connect", () => done(true));
    socket.once("error", () => done(false));
  });
}

module.exports = {
  FIRST_WEB_PORT,
  isChosenWebPort,
  portListening,
  RECORD_FILE,
  stateDirectory,
  resolveWebPort,
  portIsFixed,
  workerPortEnv,
  movedWebPort,
};
