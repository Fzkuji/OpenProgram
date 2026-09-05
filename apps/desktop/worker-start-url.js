const { execFileSync } = require("child_process");

const DEFAULT_COMMANDS = [
  { command: "openprogram", args: [] },
  { command: "/opt/miniconda3/bin/openprogram", args: [] },
];

function resolveAuthenticatedStartUrl(
  startUrl,
  env = process.env,
  commands = DEFAULT_COMMANDS,
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
          stdio: ["ignore", "pipe", "ignore"],
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
    } catch (_error) {
      // Try the next explicitly allowed command.
    }
  }
  return null;
}

module.exports = { resolveAuthenticatedStartUrl };
