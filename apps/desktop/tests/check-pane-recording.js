// A window's page may capture only its own frame, only right after it asked
// to, and the recording lands in an .mp4 under the home directory.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const { createPaneRecording } = require("../main/windows/pane-recording");

const home = fs.mkdtempSync(path.join(os.tmpdir(), "pane-recording-"));
const handlers = {};
let displayHandler = null;
const owned = new Set();
createPaneRecording({
  ipcMain: { handle: (channel, fn) => { handlers[channel] = fn; } },
  session: { setDisplayMediaRequestHandler: (fn) => { displayHandler = fn; } },
  fs, path, homeDir: home,
  ownsPage: (event) => owned.has(event.sender),
});

function page(id) {
  const mainFrame = { processId: id, routingId: 1 };
  const sender = { id, mainFrame, once() {} };
  return { sender, senderFrame: mainFrame };
}
const ask = (frame, videoRequested = true) => {
  let answer;
  displayHandler({ frame, videoRequested }, (streams) => { answer = streams; });
  return answer;
};

(async () => {
  const app = page(7);
  owned.add(app.sender);

  // Nothing is captured until the page asks, and one ask lets one request through.
  assert.equal(ask(app.senderFrame), null);
  assert.equal(await handlers["pane-recording:arm"](app), true);
  assert.deepEqual(ask(app.senderFrame), { video: app.senderFrame });
  assert.equal(ask(app.senderFrame), null);

  // Another page's ask does not open this one, and a frame other than the main one cannot ask.
  const other = page(8);
  assert.equal(await handlers["pane-recording:arm"](other), false, "a page the App does not own");
  await handlers["pane-recording:arm"](app);
  assert.equal(ask(other.senderFrame), null);
  assert.equal(await handlers["pane-recording:arm"]({ sender: app.sender, senderFrame: { processId: 7, routingId: 2 } }), false);
  ask(app.senderFrame);

  // The file: refused outside the home directory or with another extension.
  assert.equal(await handlers["pane-recording:open"](app, "abcdef12", "/etc/evil.mp4"), false);
  assert.equal(await handlers["pane-recording:open"](app, "abcdef12", path.join(home, "a.txt")), false);
  assert.equal(await handlers["pane-recording:open"](app, "not an id", path.join(home, "a.mp4")), false);
  assert.equal(await handlers["pane-recording:open"](other, "abcdef12", path.join(home, "a.mp4")), false);

  const target = path.join(home, "video", "a.mp4");
  assert.equal(await handlers["pane-recording:open"](app, "abcdef12", target), true);
  assert.equal(await handlers["pane-recording:write"](app, "abcdef12", new Uint8Array([1, 2, 3]).buffer), true);
  assert.equal(await handlers["pane-recording:write"](app, "abcdef12", new Uint8Array([4, 5])), true);
  assert.equal(await handlers["pane-recording:write"](app, "ffffffff", new Uint8Array([9]).buffer), false);
  assert.equal(fs.existsSync(target), false, "the file appears only when the recording closes");
  assert.deepEqual(await handlers["pane-recording:close"](app, "abcdef12", false), { path: target, bytes: 5 });
  assert.deepEqual([...fs.readFileSync(target)], [1, 2, 3, 4, 5]);

  // A discarded recording leaves nothing behind.
  const gone = path.join(home, "video", "b.mp4");
  await handlers["pane-recording:open"](app, "abcdef13", gone);
  await handlers["pane-recording:write"](app, "abcdef13", new Uint8Array([1]).buffer);
  assert.deepEqual(await handlers["pane-recording:close"](app, "abcdef13", true), { path: "", bytes: 0 });
  assert.deepEqual(fs.readdirSync(path.join(home, "video")), ["a.mp4"]);
  assert.equal(await handlers["pane-recording:close"](app, "abcdef13", false), null);

  fs.rmSync(home, { recursive: true, force: true });
  console.log("pane recording: ok");
})().catch((error) => { console.error(error); process.exit(1); });
