/* The application has an opaque origin. Only its parent owns authenticated API access. */
(() => {
  let port;
  const pending = new Map();
  const listeners = new Map();
  let resolveReady;
  const ready = new Promise(resolve => { resolveReady = resolve; });
  // The host's theme and language, applied to this page's <html>: its data-*
  // attributes, lang and the custom properties set on the host's root, so the
  // kit's styles draw as they do in the App.
  let appearanceApplied;
  const appearance = new Promise(resolve => { appearanceApplied = resolve; });
  setTimeout(() => appearanceApplied(), 1500);
  function applyAppearance(value) {
    if (!value || typeof value !== 'object') return;
    const root = document.documentElement;
    const attributes = value.attributes && typeof value.attributes === 'object' ? value.attributes : {};
    for (const name of [...root.getAttributeNames()]) {
      if (name.startsWith('data-') && !(name in attributes)) root.removeAttribute(name);
    }
    for (const [name, text] of Object.entries(attributes)) {
      if ((name.startsWith('data-') || name === 'lang') && typeof text === 'string') root.setAttribute(name, text);
    }
    const properties = value.properties && typeof value.properties === 'object' ? value.properties : {};
    for (const [name, text] of Object.entries(properties)) {
      if (name.startsWith('--') && typeof text === 'string') root.style.setProperty(name, text);
    }
    appearanceApplied();
  }
  window.addEventListener('message', event => {
    if (event.source !== parent || event.data?.type !== 'openprogram.application.connect' || port || !event.ports[0]) return;
    port = event.ports[0];
    port.onmessage = ({ data }) => {
      if (data?.event === 'appearance') applyAppearance(data.value);
      // The host's own word, not an answer: a conversation changed, the composer moved,
      // the state document was saved ('state'), the host's recording changed ('audio').
      if (typeof data?.event === 'string') {
        for (const listener of listeners.get(data.event) || []) {
          try { listener(data.value); } catch (error) { console.error(error); }
        }
        return;
      }
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
  function on(event, listener) {
    if (!listeners.has(event)) listeners.set(event, new Set());
    listeners.get(event).add(listener);
    return () => listeners.get(event)?.delete(listener);
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
    /* Applications that declare `chat`: the owner's composer for a conversation the
       window keeps. `open` names the conversation by a key of the window's own and the
       folder it works in; the window then hears `chat` (its messages and whether a reply
       is running) and `composer` (the composer's height) through `on`. */
    on,
    /* Resolves once the host's theme and language are applied (or after 1.5 s). */
    appearance,
    /* Applications that declare `audio.record`: the host records the microphone and the
       computer's sound under `key`, hands each chunk to the manifest's recording.ingest
       operation and runs recording.finish once it stops. Closing the window does not stop it. */
    audio: Object.freeze({
      start: (options = {}) => call('audio.start', options),
      stop: () => call('audio.stop'),
      status: () => call('audio.status'),
    }),
    /* Applications that declare `conversation`: show a conversation the backend opened. */
    conversations: Object.freeze({
      show: sessionId => call('conversation.show', { session_id: sessionId }),
    }),
    chat: Object.freeze({
      open: (key, folder = '') => call('chat.open', { key, folder }),
      note: text => call('chat.note', { text }),
      hint: text => call('chat.hint', { text }),
      send: text => call('chat.send', { text }),
      place: rect => call('chat.place', { rect }),
      reveal: () => call('chat.reveal'),
      close: () => call('chat.close'),
    }),
  });
})();
