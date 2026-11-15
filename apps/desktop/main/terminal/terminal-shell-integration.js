"use strict";
const fs = require("node:fs");
const path = require("node:path");
const os = require("node:os");
const crypto = require("node:crypto");
const ASSETS = [".zshenv", ".zprofile", ".zshrc", ".zlogin", "hooks.zsh", "hooks.bash"];
function prepareShellIntegration(launch, { preset, platform = process.platform, assets = path.join(__dirname, "shell-integration") } = {}) {
  const shell = path.basename(launch.command);
  const unavailable = reason => ({ ...launch, integrationReason: reason });
  if (platform !== "darwin" || preset !== "shell" || !["zsh", "bash"].includes(shell)) return unavailable("unsupported_launch");
  if (shell === "bash" && Object.hasOwn(launch.env, "PROMPT_COMMAND")) return unavailable("existing_prompt_command");
  let directory;
  try {
    directory = fs.mkdtempSync(path.join(os.tmpdir(), "openprogram-shell-")); fs.chmodSync(directory, 0o700);
    // Electron fs reads assets inside asar; shells source only the extracted files.
    for (const name of ASSETS) fs.writeFileSync(path.join(directory, name), fs.readFileSync(path.join(assets, name)), { mode: 0o600 });
    const nonce = crypto.randomBytes(16).toString("hex");
    const env = { ...launch.env, OP_SHELL_NONCE: nonce, OP_SHELL_DIR: directory, OP_SHELL_PARENT: String(process.pid) };
    if (shell === "zsh") {
      env.OP_SHELL_USER_ZDOTDIR_SET = Object.hasOwn(env, "ZDOTDIR") ? "1" : "0";
      env.OP_SHELL_USER_ZDOTDIR = env.ZDOTDIR || "";
      env.ZDOTDIR = directory;
    } else {
      env.PROMPT_COMMAND = 'if [ "$PPID" = "$OP_SHELL_PARENT" ]; then OP_SHELL_DEBUG="$(trap -p DEBUG)"; . "$OP_SHELL_DIR/hooks.bash"; fi';
      env.OP_SHELL_PROMPT = env.PROMPT_COMMAND;
    }
    let cleaned = false;
    return { ...launch, env, integration: { nonce, mode: shell === "zsh" ? "zsh-hooks" : "bash-debug",
      cleanup() { if (!cleaned) { cleaned = true; try { fs.rmSync(directory, { recursive: true, force: true }); } catch { /* cleanup cannot change process state */ } } } } };
  } catch {
    if (directory) try { fs.rmSync(directory, { recursive: true, force: true }); } catch { /* best effort */ }
    return unavailable("asset_preparation_failed");
  }
}
module.exports = { prepareShellIntegration, ASSETS };
