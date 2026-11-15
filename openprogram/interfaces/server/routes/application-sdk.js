/* The application has an opaque origin. Only its parent owns authenticated API access. */
(() => {
  let port;
  const pending = new Map();
  let resolveReady;
  const ready = new Promise(resolve => { resolveReady = resolve; });
  window.addEventListener('message', event => {
    if (event.source !== parent || event.data?.type !== 'openprogram.application.connect' || port || !event.ports[0]) return;
    port = event.ports[0];
    port.onmessage = ({ data }) => {
      const entry = pending.get(data.id);
      if (!entry) return;
      pending.delete(data.id);
      clearTimeout(entry.timer);
      data.error ? entry.reject(new Error(data.error)) : entry.resolve(data.result);
    };
    resolveReady();
  });
  async function call(method, params = {}) {
    await ready;
    const id = crypto.randomUUID();
    return new Promise((resolve, reject) => {
      if (pending.size >= 64) { reject(new Error("Too many pending application requests")); return; }
      const timer = setTimeout(() => { pending.delete(id); reject(new Error("Application request timed out; check run status before retrying")); }, 30000);
      pending.set(id, { resolve, reject, timer });
      port.postMessage({ id, method, params });
    });
  }
  window.openprogramApp = Object.freeze({
    ready,
    load: () => call('state.load'),
    save: (value, version) => call('state.save', { value, version }),
    run: (operation, input = {}, requestKey = crypto.randomUUID()) => call('operation.run', { operation, input, request_key: requestKey }),
    runs: () => call('runs.list'),
    status: (id, after = 0) => call('run.status', { id, after }),
    cancel: id => call('run.cancel', { id }),
    answer: (id, requestId, answer) => call('run.answer', { id, request_id: requestId, answer }),
  });
})();
