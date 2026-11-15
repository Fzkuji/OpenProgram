const assert = require("assert");
const fs = require("fs");
const os = require("os");
const path = require("path");
const { resolvePackagedWorker } = require("../main/self-update/packaged-runtime");

const root = fs.mkdtempSync(path.join(os.tmpdir(), "openprogram-runtime-check-"));
try {
  const runtime = path.join(root, "runtime");
  const python = path.join(runtime, "py", "python.exe");
  const pythonw = path.join(runtime, "py", "pythonw.exe");
  const playwright = path.join(runtime, "assets", "playwright");
  fs.mkdirSync(path.dirname(python), { recursive: true });
  const git = path.join(runtime, "git");
  fs.mkdirSync(playwright, { recursive: true });
  fs.mkdirSync(git, { recursive: true });
  fs.writeFileSync(python, "");
  fs.writeFileSync(pythonw, "");
  const capabilities = Object.fromEntries(
    [
      "web",
      "providers",
      "mcp",
      "memory",
      "channels",
      "search",
      "tui.ink",
      "browser.playwright",
    ].map((name) => [name, { present: true, verified: true }]),
  );
  fs.writeFileSync(
    path.join(runtime, "runtime-manifest.json"),
    JSON.stringify({
      schema: 2,
      openprogram: "0.6.1",
      python: "py/python.exe",
      capabilities,
      assets: {
        playwright: "assets/playwright",
        git: "git",
      },
    }),
  );
  const launch = resolvePackagedWorker(root, "0.6.1", "win32", {
    Path: "C:\\Windows\\System32",
  });
  assert.strictEqual(launch.command, pythonw);
  assert.strictEqual(launch.authCommand, python);
  assert.deepStrictEqual(
    launch.args,
    ["-I", "-B", "-m", "openprogram", "worker", "start"],
  );
  assert.deepStrictEqual(launch.env, {
    PLAYWRIGHT_BROWSERS_PATH: playwright,
    Path: `${path.join(git, "cmd")};C:\\Windows\\System32`,
  });
  assert.deepStrictEqual(
    resolvePackagedWorker(root, "0.6.1", "darwin", { PATH: "/usr/bin:/bin" }).env,
    {
      PLAYWRIGHT_BROWSERS_PATH: playwright,
      PATH: `${path.join(git, "bin")}:/usr/bin:/bin`,
      GIT_EXEC_PATH: path.join(git, "libexec", "git-core"),
      GIT_TEMPLATE_DIR: path.join(git, "share", "git-core", "templates"),
    },
  );
  assert.strictEqual(
    resolvePackagedWorker(root, "0.6.1", "linux").command,
    python,
  );

  assert.throws(
    () => resolvePackagedWorker(root, "0.6.2", "win32"),
    /runtime version mismatch/,
  );

  fs.rmSync(git, { recursive: true });
  assert.throws(
    () => resolvePackagedWorker(root, "0.6.1", "linux"),
    /runtime git asset is missing/,
  );
  fs.mkdirSync(git, { recursive: true });

  const manifestPath = path.join(runtime, "runtime-manifest.json");
  const withGit = fs.readFileSync(manifestPath, "utf8");
  const withoutGit = JSON.parse(withGit);
  delete withoutGit.assets.git;
  fs.writeFileSync(manifestPath, JSON.stringify(withoutGit));
  assert.deepStrictEqual(
    resolvePackagedWorker(root, "0.6.1", "darwin", { PATH: "/usr/bin" }).env,
    { PLAYWRIGHT_BROWSERS_PATH: playwright },
  );
  fs.writeFileSync(manifestPath, withGit);

  fs.writeFileSync(
    path.join(runtime, "runtime-manifest.json"),
    JSON.stringify({
      schema: 2,
      openprogram: "0.6.1",
      python: "../../usr/bin/python3",
      capabilities,
      assets: {
        playwright: "assets/playwright",
        git: "git",
      },
    }),
  );
  assert.throws(
    () => resolvePackagedWorker(root, "0.6.1", "win32"),
    /escapes runtime resources/,
  );
  console.log("packaged runtime checks passed");
} finally {
  fs.rmSync(root, { recursive: true, force: true });
}
