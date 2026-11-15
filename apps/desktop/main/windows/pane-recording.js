// Video recording of one pane of a window's own page.
//
// The page records itself: it asks Chromium for a stream of its own frame,
// crops the stream to the pane's element and encodes it. This module does the
// two things the page cannot: it answers the stream request with the page's
// own frame (so macOS never asks for Screen Recording), and it writes the
// encoded chunks to the file.
//
// A page may capture only while it has asked to (`pane-recording:arm`), and
// only its own main frame. Every other request for a display stream is
// refused, as it was before this module existed.

const ARM_MS = 5000;
const ID = /^[0-9a-f]{8,32}$/;

function createPaneRecording({ ipcMain, session, fs, path, homeDir, ownsPage }) {
  const armed = new Map();   // "process:routing" of a main frame -> timer
  const files = new Map();   // "webContents:id" -> { stream, target, part, bytes, failed }

  const frameKey = (frame) => (frame ? `${frame.processId}:${frame.routingId}` : "");
  const fileKey = (sender, id) => `${sender.id}:${id}`;

  session.setDisplayMediaRequestHandler((request, callback) => {
    const key = frameKey(request.frame);
    const timer = armed.get(key);
    if (!timer || !request.videoRequested) {
      callback(null);
      return;
    }
    clearTimeout(timer);
    armed.delete(key);
    callback({ video: request.frame });
  });

  // The file a recording may be written to: an .mp4 under the home directory.
  function allowedTarget(value) {
    if (typeof value !== "string" || !path.isAbsolute(value)) return null;
    const target = path.resolve(value);
    const root = path.resolve(homeDir);
    if (path.extname(target).toLowerCase() !== ".mp4") return null;
    if (!target.startsWith(root + path.sep)) return null;
    return target;
  }

  function finish(key, discard) {
    const file = files.get(key);
    if (!file) return Promise.resolve(null);
    files.delete(key);
    return new Promise((resolve) => {
      file.stream.end(() => {
        const keep = !discard && !file.failed && file.bytes > 0;
        try {
          if (keep) fs.renameSync(file.part, file.target);
          else fs.rmSync(file.part, { force: true });
        } catch (_error) {
          resolve(null);
          return;
        }
        resolve(keep ? { path: file.target, bytes: file.bytes } : { path: "", bytes: 0 });
      });
    });
  }

  ipcMain.handle("pane-recording:arm", (event) => {
    if (!ownsPage(event) || event.senderFrame !== event.sender.mainFrame) return false;
    const key = frameKey(event.senderFrame);
    clearTimeout(armed.get(key));
    armed.set(key, setTimeout(() => armed.delete(key), ARM_MS));
    return true;
  });

  ipcMain.handle("pane-recording:open", (event, id, value) => {
    if (!ownsPage(event) || !ID.test(String(id))) return false;
    const target = allowedTarget(value);
    const key = fileKey(event.sender, id);
    if (!target || files.has(key)) return false;
    try {
      fs.mkdirSync(path.dirname(target), { recursive: true });
      const part = `${target}.part`;
      const file = { stream: fs.createWriteStream(part), target, part, bytes: 0, failed: false };
      file.stream.on("error", () => { file.failed = true; });
      files.set(key, file);
      // A window that goes away mid-recording keeps what it recorded.
      event.sender.once("destroyed", () => { void finish(key, false); });
      return true;
    } catch (_error) {
      return false;
    }
  });

  ipcMain.handle("pane-recording:write", (event, id, chunk) => {
    const file = files.get(fileKey(event.sender, id));
    if (!file || file.failed) return false;
    const data = chunk instanceof ArrayBuffer ? Buffer.from(chunk) : ArrayBuffer.isView(chunk)
      ? Buffer.from(chunk.buffer, chunk.byteOffset, chunk.byteLength) : null;
    if (!data || !data.length) return false;
    file.bytes += data.length;
    return new Promise((resolve) => file.stream.write(data, (error) => resolve(!error)));
  });

  ipcMain.handle("pane-recording:close", (event, id, discard) =>
    finish(fileKey(event.sender, id), discard === true));

  return { allowedTarget };
}

module.exports = { createPaneRecording };
