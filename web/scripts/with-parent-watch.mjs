#!/usr/bin/env node
// Spawn `next start` and watch a parent PID supplied via OPENPROGRAM_PARENT_PID.
// If the parent disappears (e.g. the Python worker was SIGKILLed), terminate
// the Next.js child and exit. Keeps the frontend bound to the worker's
// lifetime so users never see "frontend up, backend gone".

import { spawn } from "node:child_process";
import { resolve } from "node:path";

const parentPid = parseInt(process.env.OPENPROGRAM_PARENT_PID || "0", 10);
const port = process.env.PORT || "3000";

const nextBin = resolve(process.cwd(), "node_modules/.bin/next");
const child = spawn(nextBin, ["start", "-p", String(port)], {
  cwd: process.cwd(),
  env: process.env,
  stdio: "inherit",
});

let stopping = false;
function stopChild(signal = "SIGTERM") {
  if (stopping) return;
  stopping = true;
  try { child.kill(signal); } catch (_) {}
  setTimeout(() => {
    try { child.kill("SIGKILL"); } catch (_) {}
    process.exit(0);
  }, 5000).unref();
}

child.on("exit", (code, sig) => {
  process.exit(code ?? (sig ? 1 : 0));
});

for (const sig of ["SIGINT", "SIGTERM", "SIGHUP"]) {
  process.on(sig, () => stopChild(sig));
}

if (parentPid > 0) {
  setInterval(() => {
    try {
      process.kill(parentPid, 0);
    } catch (_) {
      console.error(`[web-watch] parent PID ${parentPid} gone; shutting down next`);
      stopChild("SIGTERM");
    }
  }, 1000).unref();
}
