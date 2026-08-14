const assert = require("assert");
const fs = require("fs");
const os = require("os");
const path = require("path");
const { resolvePackagedWorker } = require("../packaged-runtime");

const root = fs.mkdtempSync(path.join(os.tmpdir(), "openprogram-runtime-check-"));
try {
  const runtime = path.join(root, "runtime");
  const python = path.join(runtime, "python", "bin", "python3");
  fs.mkdirSync(path.dirname(python), { recursive: true });
  fs.writeFileSync(python, "");
  fs.writeFileSync(
    path.join(runtime, "runtime-manifest.json"),
    JSON.stringify({ python: "python/bin/python3" }),
  );
  const launch = resolvePackagedWorker(root);
  assert.strictEqual(launch.command, python);
  assert.deepStrictEqual(
    launch.args,
    ["-I", "-B", "-m", "openprogram", "worker", "start"],
  );

  fs.writeFileSync(
    path.join(runtime, "runtime-manifest.json"),
    JSON.stringify({ python: "../../usr/bin/python3" }),
  );
  assert.throws(() => resolvePackagedWorker(root), /escapes runtime resources/);
  console.log("packaged runtime checks passed");
} finally {
  fs.rmSync(root, { recursive: true, force: true });
}
