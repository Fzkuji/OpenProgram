"use strict";

const fs = require("node:fs");
const path = require("node:path");
const { spawn } = require("node:child_process");

function existingAbsolute(value, existsSync, pathApi = path) {
  return typeof value === "string"
    && pathApi.isAbsolute(value)
    && existsSync(value)
    ? value
    : null;
}

function resolveWindowsShell(env, existsSync) {
  const systemRoot = env.SystemRoot || env.SYSTEMROOT || "C:\\Windows";
  const powershell = existingAbsolute(
    path.win32.join(systemRoot, "System32", "WindowsPowerShell", "v1.0", "powershell.exe"),
    existsSync,
    path.win32,
  );
  if (powershell) return { command: powershell, kind: "powershell" };
  const comspec = existingAbsolute(env.ComSpec || env.COMSPEC, existsSync, path.win32);
  if (comspec) return { command: comspec, kind: "cmd" };
  return { command: "powershell.exe", kind: "powershell" };
}

function resolveTerminalCommand(
  preset,
  {
    platform = process.platform,
    env = process.env,
    existsSync = fs.existsSync,
  } = {},
) {
  if (preset !== "shell" && preset !== "claude") {
    throw new Error(`unsupported terminal preset: ${preset}`);
  }
  if (platform === "win32") {
    const shell = resolveWindowsShell(env, existsSync);
    if (shell.kind === "cmd") {
      return {
        command: shell.command,
        args: preset === "claude" ? ["/d", "/k", "claude"] : ["/d"],
        useConpty: true,
      };
    }
    return {
      command: shell.command,
      args: preset === "claude"
        ? ["-NoLogo", "-NoExit", "-Command", "claude"]
        : ["-NoLogo"],
      useConpty: true,
    };
  }
  const command = existingAbsolute(env.SHELL, existsSync) || "/bin/zsh";
  return {
    command,
    args: preset === "claude" ? ["-l", "-i", "-c", "exec claude"] : ["-l"],
    useConpty: false,
  };
}

async function waitForTerminalPid(
  terminal,
  {
    timeoutMs = 3_000,
    pollMs = 10,
    now = Date.now,
    delay = (milliseconds) => new Promise((resolve) => setTimeout(resolve, milliseconds)),
  } = {},
) {
  const deadline = now() + timeoutMs;
  while (!Number.isInteger(terminal?.pid) || terminal.pid <= 0) {
    if (now() >= deadline) return null;
    await delay(pollMs);
  }
  return terminal.pid;
}

function killWindowsProcessTree(
  pid,
  {
    platform = process.platform,
    env = process.env,
    spawnImpl = spawn,
  } = {},
) {
  if (platform !== "win32" || !Number.isInteger(pid) || pid <= 0) return false;
  const systemRoot = env.SystemRoot || env.SYSTEMROOT || "C:\\Windows";
  const taskkill = path.win32.join(systemRoot, "System32", "taskkill.exe");
  try {
    const child = spawnImpl(taskkill, ["/F", "/T", "/PID", String(pid)], {
      windowsHide: true,
      stdio: "ignore",
    });
    child.on?.("error", () => {});
    child.unref?.();
    return true;
  } catch (_error) {
    return false;
  }
}

module.exports = {
  killWindowsProcessTree,
  resolveTerminalCommand,
  waitForTerminalPid,
};
