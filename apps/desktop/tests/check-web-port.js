const assert = require("assert");
const fs = require("fs");
const net = require("net");
const os = require("os");
const path = require("path");

const webPort = require("../main/worker/web-port");
const themeChrome = require("../main/windows/theme-chrome");

const stable = { development: false, port: 18100 };
const development = { development: true, port: 18200 };
const state = fs.mkdtempSync(path.join(os.tmpdir(), "openprogram-web-port-"));

async function main() {
  const files = { env: {}, instance: stable, stateDir: state };
  // Before any worker has started: 18100, which the worker may still move.
  assert.deepEqual(webPort.resolveWebPort(files), { port: 18100, source: "default" });
  assert.deepEqual(webPort.workerPortEnv(webPort.resolveWebPort(files)), {});

  // The worker chose another port at this account's first start (another
  // account's worker or another program held 18100): the App follows it.
  const before = webPort.resolveWebPort(files);
  fs.writeFileSync(path.join(state, webPort.RECORD_FILE), "18101\n");
  assert.equal(webPort.movedWebPort(before, files), 18101);
  const recorded = webPort.resolveWebPort(files);
  assert.deepEqual(recorded, { port: 18101, source: "record" });
  assert.equal(webPort.portIsFixed(recorded), false);
  assert.equal(webPort.movedWebPort(recorded, files), null);
  // The worker reads the record itself, so the App never pins the port for it.
  assert.deepEqual(webPort.workerPortEnv(recorded), {});

  // A port the user set wins over the record and never moves.
  fs.writeFileSync(path.join(state, "config.json"), JSON.stringify({ ui: { web_port: 18555 } }));
  const configured = webPort.resolveWebPort(files);
  assert.deepEqual(configured, { port: 18555, source: "settings" });
  assert.equal(webPort.portIsFixed(configured), true);
  fs.writeFileSync(path.join(state, webPort.RECORD_FILE), "18102\n");
  assert.equal(webPort.movedWebPort(configured, files), null);

  // The environment and the development instance are passed to the worker.
  const fromEnv = webPort.resolveWebPort({ ...files, env: { OPENPROGRAM_WEB_PORT: "18777" } });
  assert.deepEqual(fromEnv, { port: 18777, source: "env" });
  assert.deepEqual(webPort.workerPortEnv(fromEnv), { OPENPROGRAM_WEB_PORT: "18777" });
  const dev = webPort.resolveWebPort({ ...files, instance: development });
  assert.deepEqual(webPort.workerPortEnv(dev), { OPENPROGRAM_WEB_PORT: "18200" });

  // A junk record is ignored rather than trusted.
  fs.rmSync(path.join(state, "config.json"));
  fs.writeFileSync(path.join(state, webPort.RECORD_FILE), "80\n");
  assert.equal(webPort.resolveWebPort(files).source, "default");

  const server = net.createServer().listen(0, "127.0.0.1");
  await new Promise((resolve) => server.once("listening", resolve));
  const { port } = server.address();
  assert.equal(await webPort.portListening(port), true);
  await new Promise((resolve) => server.close(resolve));
  assert.equal(await webPort.portListening(port), false);

  // The port page names the port and the holder, in English or Chinese, and
  // tells the user what to do; it is a waiting page the probe keeps checking.
  const chrome = themeChrome.chromeForTheme("beige-light");
  const detail = { port: "18555", holder: "PID 4242: <other>.exe", setting: "settings" };
  const english = themeChrome.buildErrorPageHtml(chrome, "openprogram worker start", "port", detail);
  assert.match(english, /Port 18555 is in use/);
  assert.match(english, /PID 4242: &lt;other&gt;\.exe/);
  assert.match(english, /openprogram config set ui\.web_port/);
  assert.doesNotMatch(english, /Waiting for backend/);
  const chinese = themeChrome.buildErrorPageHtml(chrome, "", "port", { ...detail, chinese: true });
  assert.match(chinese, /端口 18555 已被占用/);
  const url = themeChrome.buildErrorPageUrl(chrome, "", "port", { ...detail, setting: "env" });
  assert.equal(themeChrome.waitPagePhase(url), "port");
  assert.match(decodeURIComponent(url), /OPENPROGRAM_WEB_PORT/);

  // main.js reads its port through this module and passes only a set port on.
  const main = fs.readFileSync(path.join(__dirname, "..", "main.js"), "utf8");
  assert.doesNotMatch(main, /OPENPROGRAM_WEB_PORT: WEB_PORT/);
  assert.match(main, /webPort\.resolveWebPort\(WEB_PORT_FILES\)/);
  assert.match(main, /followMovedWebPort\(\)/);
  // The relaunch onto the moved port keeps an AppImage on its own file.
  const follow = main.slice(main.indexOf("function followMovedWebPort"));
  assert.match(follow.slice(0, follow.indexOf("\n}\n")), /app\.relaunch\(shellContext\?\.relaunchOptions\?\.\(\)\)/);
  // Self-update reopens and verifies the App on whatever port this profile
  // chose (18101 when another account holds 18100), never on the development
  // instance's 18200.
  assert.equal(webPort.isChosenWebPort("18100"), true);
  assert.equal(webPort.isChosenWebPort("18101"), true);
  assert.equal(webPort.isChosenWebPort(18199), true);
  assert.equal(webPort.isChosenWebPort("18200"), false);
  assert.equal(webPort.isChosenWebPort(""), false);
  for (const name of ["self-update-ui.js", "self-update-reopen.js"]) {
    const source = fs.readFileSync(path.join(__dirname, "..", "main", "self-update", name), "utf8");
    assert.doesNotMatch(source, /"18100"/, `${name} pins the port`);
    assert.match(source, /isChosenWebPort\(/);
  }
  console.log("web port check passed");
}

main().finally(() => fs.rmSync(state, { recursive: true, force: true })).catch((error) => {
  console.error(error);
  process.exit(1);
});
