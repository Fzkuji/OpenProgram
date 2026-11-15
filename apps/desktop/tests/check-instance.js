const assert = require("assert");
const fs = require("fs");
const os = require("os");
const path = require("path");
const { loadInstance, resolveInstanceWorker } = require("../main/instance");

const root = fs.mkdtempSync(path.join(os.tmpdir(), "openprogram-instance-check-"));
try {
  // No instance.json, or an unpackaged shell: the stable App.
  assert.strictEqual(loadInstance(root, true).development, false);
  assert.strictEqual(loadInstance(root, true).port, 18100);
  fs.writeFileSync(path.join(root, "instance.json"), "{}");
  assert.strictEqual(loadInstance(root, false).development, false);
  assert.throws(() => loadInstance(root, true), /name/);

  const runtime = path.join(root, "runtime");
  const python = path.join(runtime, "python", "bin", "python3.12");
  fs.mkdirSync(path.dirname(python), { recursive: true });
  fs.writeFileSync(python, "");
  fs.writeFileSync(
    path.join(runtime, "runtime-manifest.json"),
    JSON.stringify({ python: "python/bin/python3.12" }),
  );
  const write = (value) => fs.writeFileSync(path.join(root, "instance.json"), JSON.stringify(value));
  const valid = { name: "OpenProgram Dev", profile: "dev", port: 18200, debugPort: 9224, runtime };
  write(valid);
  const instance = loadInstance(root, true);
  assert.deepStrictEqual(
    { ...instance },
    { development: true, name: "OpenProgram Dev", profile: "dev", port: 18200, debugPort: 9224, runtimeRoot: runtime },
  );
  const worker = resolveInstanceWorker(instance);
  assert.strictEqual(worker.command, python);
  assert.deepStrictEqual(worker.args, ["-I", "-B", "-m", "openprogram", "worker", "start"]);
  assert.deepStrictEqual(worker.env, { OPENPROGRAM_PROFILE: "dev" });

  // The development App never takes the stable port or a relative runtime.
  write({ ...valid, port: 18100 });
  assert.throws(() => loadInstance(root, true), /port/);
  write({ ...valid, debugPort: 9223 });
  assert.throws(() => loadInstance(root, true), /debugPort/);
  write({ ...valid, runtime: "runtime" });
  assert.throws(() => loadInstance(root, true), /runtime/);
  write(valid);
  fs.writeFileSync(
    path.join(runtime, "runtime-manifest.json"),
    JSON.stringify({ python: "../../escape" }),
  );
  assert.throws(() => resolveInstanceWorker(loadInstance(root, true)), /escapes/);
} finally {
  fs.rmSync(root, { recursive: true, force: true });
}
console.log("instance checks passed");
