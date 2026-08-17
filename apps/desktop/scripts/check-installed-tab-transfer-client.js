const assert = require("node:assert/strict");

const { CdpClient } = require("./check-installed-tab-transfer");

class FakeSocket {
  constructor(_url, { open = true, closeOnSend = false } = {}) {
    this.listeners = new Map();
    this.closeOnSend = closeOnSend;
    this.closed = false;
    if (open) queueMicrotask(() => this.emit("open", {}));
  }

  addEventListener(name, listener) {
    const listeners = this.listeners.get(name) || [];
    listeners.push(listener);
    this.listeners.set(name, listeners);
  }

  removeEventListener(name, listener) {
    this.listeners.set(
      name,
      (this.listeners.get(name) || []).filter((candidate) => candidate !== listener),
    );
  }

  emit(name, event) {
    for (const listener of [...(this.listeners.get(name) || [])]) listener(event);
  }

  send() {
    if (this.closeOnSend) queueMicrotask(() => {
      this.closed = true;
      this.emit("close", {});
    });
  }

  close() {
    this.closed = true;
    this.emit("close", {});
  }
}

async function main() {
  let closeSocket = null;
  class CloseWithoutReplySocket extends FakeSocket {
    constructor(url) {
      super(url, { closeOnSend: true });
      closeSocket = this;
    }
  }
  const client = await new CdpClient("ws://close", {
    timeoutMs: 50,
    WebSocketImpl: CloseWithoutReplySocket,
  }).connect();
  await assert.rejects(
    client.send("Runtime.evaluate"),
    /CDP socket closed/,
  );
  assert.equal(client.pending.size, 0);
  client.close();
  assert.equal(closeSocket.closed, true);

  let neverOpenSocket = null;
  class NeverOpenSocket extends FakeSocket {
    constructor(url) {
      super(url, { open: false });
      neverOpenSocket = this;
    }
  }
  await assert.rejects(
    new CdpClient("ws://never-open", {
      timeoutMs: 20,
      WebSocketImpl: NeverOpenSocket,
    }).connect(),
    /timed out waiting for CDP connection/,
  );
  assert.equal(neverOpenSocket.closed, true);

  console.log("installed tab-transfer CDP client checks passed");
}

main().catch((error) => {
  console.error(error.stack || error);
  process.exitCode = 1;
});
