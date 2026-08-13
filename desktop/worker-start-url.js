const { execFileSync } = require("child_process");

function resolveAuthenticatedStartUrl(startUrl, env = process.env) {
  const baseUrl = new URL(startUrl).origin;
  for (const bin of ["openprogram", "/opt/miniconda3/bin/openprogram"]) {
    try {
      const raw = execFileSync(bin, ["web", "auth-url", "--base-url", baseUrl], {
        encoding: "utf8",
        env,
        stdio: ["ignore", "pipe", "ignore"],
        timeout: 5000,
      }).trim();
      const bootstrap = new URL(raw);
      if (!/^#token=[A-Za-z0-9_-]{43}$/.test(bootstrap.hash)) continue;
      const requested = new URL(startUrl);
      bootstrap.pathname = requested.pathname;
      bootstrap.search = requested.search;
      return bootstrap.toString();
    } catch (_error) {
      // Try the known miniconda install when PATH has no OpenProgram CLI.
    }
  }
  return null;
}

module.exports = { resolveAuthenticatedStartUrl };
