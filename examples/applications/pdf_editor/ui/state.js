// State shared by the editor modules. app.js owns the document lifecycle
// and fills `hooks` with the functions other modules call back into.
import { Store } from "./bridge.js";

export const store = new Store({
  recent: [], docs: {}, last: null,
  layout: "continuous", tint: "day", sidebar: true, tab: "thumbs",
  author: "", signatures: [], tool: { color: {}, width: {}, size: 12 },
});

export const doc = {
  path: null,          // project-relative path of the open file
  info: null,          // info() of the file on disk (revision, outline, …)
  draft: null,         // staged draft id shown on screen, or null for the file itself
  edits: 0,            // edits committed into the draft
  log: [],             // human-readable list of committed edits
  pdf: null, viewer: null, bus: null, links: null, find: null, ui: null,
  pages: 0,
  outline: [],
  annotations: [],     // annotations in the loaded bytes (backend listing)
  overlay: new Map(),  // pending annotations drawn by this app, id -> item
  baseHash: null,      // annotationStorage hash right after loading
  canUndo: false, canRedo: false,
  history: { undo: [], redo: [] }, // committed states: { draft, edits, log }
  loadToken: 0,
  mode: "read",
  busy: false,
};

export const hooks = {};

export const COLORS = [
  { name: "Yellow", value: "#ffd43b" },
  { name: "Green", value: "#69db7c" },
  { name: "Blue", value: "#4dabf7" },
  { name: "Pink", value: "#f783ac" },
  { name: "Red", value: "#e03131" },
  { name: "Purple", value: "#7950f2" },
  { name: "Black", value: "#1f1f24" },
];

export const HIGHLIGHT_COLORS = "yellow=#FFD43B,green=#69DB7C,blue=#4DABF7,pink=#F783AC,red=#FF8787,purple=#B197FC";
