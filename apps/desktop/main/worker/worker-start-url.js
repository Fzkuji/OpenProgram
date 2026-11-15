const { execFileSync } = require("child_process");

const DEFAULT_COMMANDS = [
  { command: "openprogram", args: [] },
  { command: "/opt/miniconda3/bin/openprogram", args: [] },
];

function ownerAuthTokenFromStartUrl(startUrl) {
  try {
    const match = /^#token=([A-Za-z0-9_-]{43})$/.exec(new URL(startUrl).hash);
    return match ? match[1] : null;
  } catch (_error) {
    return null;
  }
}

// `web auth-url` exits with this when this account has no worker but
// something else listens on the port (openprogram/interfaces/cli/commands/web.py).
const EXIT_PORT_HELD = 3;

/**
 * The start URL with this account's sign-in token, or null. `onPortHeld`
 * receives the holder `web auth-url` names when the port belongs to another
 * program or another account; such a listener is never given a token.
 */
function resolveAuthenticatedStartUrl(
  startUrl,
  env = process.env,
  commands = DEFAULT_COMMANDS,
  onPortHeld = () => {},
) {
  const baseUrl = new URL(startUrl).origin;
  for (const candidate of commands) {
    try {
      const raw = execFileSync(
        candidate.command,
        [...candidate.args, "web", "auth-url", "--base-url", baseUrl],
        {
          encoding: "utf8",
          env,
          stdio: ["ignore", "pipe", "pipe"],
          timeout: 5000,
          windowsHide: process.platform === "win32",
        },
      ).trim();
      const bootstrap = new URL(raw);
      if (!/^#token=[A-Za-z0-9_-]{43}$/.test(bootstrap.hash)) continue;
      const requested = new URL(startUrl);
      bootstrap.pathname = requested.pathname;
      bootstrap.search = requested.search;
      return bootstrap.toString();
    } catch (error) {
      if (error?.status === EXIT_PORT_HELD) {
        const line = String(error.stderr || "").trim().split(/\r?\n/).pop() || "";
        onPortHeld(line.replace(/^port \d+ is in use by /, ""));
        return null;
      }
      // Try the next explicitly allowed command.
    }
  }
  return null;
}

module.exports = { EXIT_PORT_HELD, resolveAuthenticatedStartUrl, ownerAuthTokenFromStartUrl };
