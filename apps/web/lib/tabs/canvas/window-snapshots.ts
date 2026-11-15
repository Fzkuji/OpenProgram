import { create } from "zustand";
import { desktopBridge } from "../../desktop/desktop-bridge";
import { useCenterTabs } from "../center-tabs-store";

/** Last-seen pictures of panes for the window overview, keyed by tab id.
 *  Like Mission Control and the iPad switcher, a window that is not on
 *  screen shows how it looked when it was; web pages are captured live
 *  from their own view. Kept in memory only. */
export const useWindowSnapshots = create<{ shots: Record<string, string> }>(() => ({ shots: {} }));

/** Pictures also live in IndexedDB, so cards keep them across restarts. */
const DB = "openprogram-window-snapshots", STORE = "shots";
let db: Promise<IDBDatabase | null> | null = null;
function openDb() {
  if (!db) db = new Promise(resolve => {
    try {
      const request = indexedDB.open(DB, 1);
      request.onupgradeneeded = () => request.result.createObjectStore(STORE);
      request.onsuccess = () => resolve(request.result);
      request.onerror = () => resolve(null);
    } catch { resolve(null); }
  });
  return db;
}
async function persist(entries: ReadonlyArray<readonly [string, string | null]>) {
  const handle = await openDb();
  if (!handle) return;
  try {
    const store = handle.transaction(STORE, "readwrite").objectStore(STORE);
    for (const [id, shot] of entries) if (shot) store.put(shot, id); else store.delete(id);
  } catch { /* storage unavailable: memory still works */ }
}
/** Pictures show the theme they were taken in. A card in another theme
 *  than the live window it zooms into flashes at the hand-over, so the
 *  pictures are kept for one theme only: the saved ones carry it, and a
 *  theme change drops them all. */
const LOOK = "meta:look";
const currentLook = () => `${document.documentElement.dataset.theme ?? ""}/${document.documentElement.dataset.themeMode ?? ""}`;
async function clearSaved() {
  const handle = await openDb();
  if (!handle) return;
  try {
    const store = handle.transaction(STORE, "readwrite").objectStore(STORE);
    store.clear();
    store.put(currentLook(), LOOK);
  } catch { /* storage unavailable */ }
}
let lookSeen = "";
/** The theme may have changed: if it did, forget every picture (memory
 *  and saved). */
export function forgetSnapshots() {
  const look = currentLook();
  if (look === lookSeen) return;
  lookSeen = look;
  for (const id of Object.keys(chromes)) delete chromes[id];
  useWindowSnapshots.setState({ shots: {} });
  void clearSaved();
}
/** Load the saved pictures once; fresher ones taken meanwhile win. */
export async function restoreSnapshots() {
  const handle = await openDb();
  if (!handle) return;
  lookSeen = currentLook();
  try {
    const store = handle.transaction(STORE, "readonly").objectStore(STORE);
    const [keys, values] = await Promise.all([store.getAllKeys(), store.getAll()].map(request => new Promise<unknown[]>(resolve => {
      request.onsuccess = () => resolve(request.result as unknown[]);
      request.onerror = () => resolve([]);
    })));
    const look = values[keys.findIndex(key => key === LOOK)];
    if (look !== currentLook()) { await clearSaved(); return; }
    const saved: Record<string, string> = {};
    keys.forEach((key, i) => {
      const id = String(key), value = String(values[i]);
      if (id === LOOK) return;
      if (!id.startsWith(CHROME)) saved[id] = value;
      else if (!chromes[id.slice(CHROME.length)]) try { chromes[id.slice(CHROME.length)] = JSON.parse(value) as Chrome; } catch { /* unreadable */ }
    });
    useWindowSnapshots.setState(s => ({ shots: { ...saved, ...s.shots } }));
  } catch { /* nothing saved */ }
}
function remember(fresh: ReadonlyArray<readonly [string, string]>) {
  if (!fresh.length) return;
  useWindowSnapshots.setState(s => ({ shots: { ...s.shots, ...Object.fromEntries(fresh) } }));
  void persist(fresh);
}

/** A web page is its own native view, so its picture holds the page only,
 *  without the App's toolbar above it. A pane picture needs both, in the
 *  pane's own proportions: the page is drawn into the pane's picture where
 *  the page sits. The toolbar band is kept, so a page pictured off screen
 *  is framed the same way. */
type Chrome = { strip: string; band: number; width: number };
const CHROME = "chrome:";
const chromes: Record<string, Chrome> = {};
const MAX_WIDTH = 720;
const loadImage = (src: string) => new Promise<HTMLImageElement>((resolve, reject) => {
  const image = new Image();
  image.onload = () => resolve(image);
  image.onerror = reject;
  image.src = src;
});
function canvas(width: number, height: number) {
  const c = document.createElement("canvas");
  c.width = Math.max(1, Math.round(width)); c.height = Math.max(1, Math.round(height));
  return { c, g: c.getContext("2d")! };
}
/** The pane on screen: its own picture with the page drawn into its frame. */
async function framePage(id: string, region: string, page: string, pane: DOMRect, frame: DOMRect) {
  const [whole, inner] = await Promise.all([loadImage(region), loadImage(page)]);
  const k = whole.width / pane.width;
  const { c, g } = canvas(whole.width, whole.height);
  g.drawImage(whole, 0, 0);
  g.drawImage(inner, (frame.left - pane.left) * k, (frame.top - pane.top) * k, frame.width * k, frame.height * k);
  const band = frame.top - pane.top;
  if (band > 0) {
    const strip = canvas(whole.width, band * k);
    strip.g.drawImage(whole, 0, 0);
    chromes[id] = { strip: strip.c.toDataURL("image/jpeg", 0.85), band, width: pane.width };
    void persist([[CHROME + id, JSON.stringify(chromes[id])]]);
  }
  return c.toDataURL("image/jpeg", 0.82);
}
/** A page off screen: under the toolbar band it was last seen with. */
async function underChrome(page: string, chrome: Chrome) {
  const [inner, strip] = await Promise.all([loadImage(page), loadImage(chrome.strip)]);
  const width = Math.min(MAX_WIDTH, inner.width), k = width / chrome.width, pageHeight = inner.height * (width / inner.width);
  const { c, g } = canvas(width, chrome.band * k + pageHeight);
  g.drawImage(strip, 0, 0, width, chrome.band * k);
  g.drawImage(inner, 0, chrome.band * k, width, pageHeight);
  return c.toDataURL("image/jpeg", 0.82);
}

let inFlight: Promise<void> | null = null;

function overlaps(a: DOMRect, b: DOMRect) {
  return a.width > 0 && a.height > 0
    && a.left < b.right && b.left < a.right && a.top < b.bottom && b.top < a.bottom;
}

/** Capture every pane on screen now, except `skip`. A dragged tab's
 *  floating preview hides while the pictures are taken. Resolves within
 *  `limit` ms either way, so an overview never waits long for a picture.
 *  A `background` picture nobody asked for leaves the floating web preview
 *  on screen and keeps the earlier picture of any pane under it: hiding it
 *  would blink the preview's frame away from its native page. */
export function captureVisiblePanes(
  skip: readonly string[] = [],
  limit = 260,
  { background = false }: { background?: boolean } = {},
): Promise<void> {
  const bridge = desktopBridge();
  if (!bridge?.captureRegion || document.querySelector("[data-canvas-overview]")) return Promise.resolve();
  if (!inFlight) {
    const floating = background
      ? [...document.querySelectorAll<HTMLElement>('[data-pip="true"]')].map(pip => pip.getBoundingClientRect())
      : [];
    const panes = [...document.querySelectorAll<HTMLElement>("[data-canvas-root] [data-canvas-content]")]
      .filter(pane => pane.offsetWidth > 0 && pane.offsetHeight > 0 && !skip.includes(pane.dataset.canvasContent!))
      .filter(pane => !floating.some(rect => overlaps(rect, pane.getBoundingClientRect())));
    const tabs = useCenterTabs.getState().tabs;
    // The pane grips stay out of the pictures: hide them for two frames.
    const root = document.documentElement;
    root.setAttribute("data-capturing-panes", background ? "background" : "");
    const painted = new Promise<void>(resolve => requestAnimationFrame(() => requestAnimationFrame(() => resolve())));
    inFlight = painted.then(() => Promise.all(panes.map(async pane => {
      const id = pane.dataset.canvasContent!;
      const tab = tabs.find(candidate => candidate.id === id);
      const r = pane.getBoundingClientRect();
      const region = await bridge.captureRegion!({ x: r.left, y: r.top, width: r.width, height: r.height }).catch(() => null);
      const frame = tab?.kind === "web"
        ? [...document.querySelectorAll<HTMLElement>(`[data-web-frame="${CSS.escape(id)}"]`)].find(el => el.offsetWidth > 0)
        : undefined;
      if (!region || !frame) return [id, region] as const;
      const page = await bridge.webTab.capture?.(id, "presentation").catch(() => null);
      return [id, page ? await framePage(id, region, page, r, frame.getBoundingClientRect()).catch(() => region) : region] as const;
    }))).then(results => {
      remember(results.filter((entry): entry is readonly [string, string] => !!entry[1]));
    }).finally(() => { inFlight = null; root.removeAttribute("data-capturing-panes"); });
  }
  return Promise.race([inFlight, new Promise<void>(resolve => window.setTimeout(resolve, limit))]);
}

/** Pictures of web pages that are not on screen, taken from their views. */
export function captureWebTabs(ids: string[]) {
  const capture = desktopBridge()?.webTab.capture;
  if (!capture) return;
  for (const id of ids) {
    // Without a toolbar band seen yet, a bare page would not match its
    // pane; an earlier pane picture is better than that.
    void capture(id, "presentation").then(async shot => {
      const chrome = chromes[id];
      if (shot && chrome) remember([[id, await underChrome(shot, chrome)]]);
      else if (shot && !useWindowSnapshots.getState().shots[id]) remember([[id, shot]]);
    }).catch(() => {});
  }
}

/** Drop pictures of tabs that no longer exist. An empty tab list is the
 *  store still loading, never a reason to forget everything. */
export function pruneSnapshots(alive: Set<string>) {
  if (!alive.size) return;
  const { shots } = useWindowSnapshots.getState();
  const gone = Object.keys(shots).filter(id => !alive.has(id));
  for (const id of Object.keys(chromes)) if (!alive.has(id)) { delete chromes[id]; void persist([[CHROME + id, null]]); }
  if (!gone.length) return;
  useWindowSnapshots.setState({ shots: Object.fromEntries(Object.entries(shots).filter(([id]) => alive.has(id))) });
  void persist(gone.map(id => [id, null] as const));
}
