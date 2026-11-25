/**
 * Menus drawn by iOS in the phone app (apps/mobile/ios/OpenProgram/
 * NativeMenus.swift): the same menus the system's own apps show, so the
 * phone's menus look and move like theirs. In a browser, or on an iOS
 * before 17.4, `nativeMenus()` is false and the page keeps its own menus.
 *
 * An item is an action (`run`), a submenu (`children`) or, with `inline`,
 * a section of the menu it sits in. `icon` is an SF Symbol name.
 */

import { callMobile, mobileBridge } from "./bridge";

export interface NativeMenuItem {
  title: string;
  subtitle?: string;
  icon?: string;
  checked?: boolean;
  destructive?: boolean;
  disabled?: boolean;
  run?: () => void;
  children?: NativeMenuItem[];
  inline?: boolean;
}

type Sent = Omit<NativeMenuItem, "run" | "children"> & { id?: string; children?: Sent[] };

/** The picks of the last few menus, by menu. */
const runs = new Map<string, Map<string, () => void>>();
let count = 0;

if (typeof window !== "undefined") {
  window.addEventListener("openprogram-mobile-menu", (event) => {
    const { key, id } = (event as CustomEvent<{ key: string; id: string }>).detail ?? {};
    const run = runs.get(key)?.get(id);
    // After the menu has closed, so what it opens is not closed with it.
    if (run) window.setTimeout(run, 0);
  });
}

export function nativeMenus(): boolean {
  return !!mobileBridge()?.menus;
}

function pack(items: NativeMenuItem[]): { key: string; items: Sent[] } {
  const key = `menu-${++count}`;
  const picks = new Map<string, () => void>();
  const walk = (list: NativeMenuItem[]): Sent[] => list.map(({ run, children, ...item }) => {
    const sent: Sent = { ...item };
    if (run) {
      sent.id = String(picks.size);
      picks.set(sent.id, run);
    }
    if (children) sent.children = walk(children);
    return sent;
  });
  const sent = walk(items);
  runs.set(key, picks);
  for (const old of runs.keys()) {
    if (runs.size <= 4) break;
    runs.delete(old);
  }
  return { key, items: sent };
}

function rectOf(element: Element) {
  const { x, y, width, height } = element.getBoundingClientRect();
  return { x, y, width, height };
}

/** Open `items` as the menu of `anchor`, a button the person just tapped. */
export function openNativeMenu(anchor: Element, items: NativeMenuItem[]): void {
  callMobile("menu", { ...pack(items), rect: rectOf(anchor) });
}

/** A finger is on `element`: a long press on it shows `items`, with the
 *  element lifted (its corners at `radius`, `pad` around it, cut to
 *  `within`). `null` when the finger lifts
 *  or scrolls away. */
export function armNativePress(element: Element | null, items: NativeMenuItem[] = [], radius = 16, pad = 0, within?: Element | null): void {
  if (!element) {
    callMobile("menuPress", {});
    return;
  }
  // Only what is on screen lifts: a long message is cut to the screen.
  // `pad` gives text that runs to its box's edge a margin in the lift.
  // `within` is the scroller it shows in: what is under the bars does not lift.
  const { x, y, width, height } = rectOf(element);
  const box = within?.getBoundingClientRect() ?? { left: 0, top: 0, right: window.innerWidth, bottom: window.innerHeight };
  const left = Math.max(x - pad, box.left);
  const right = Math.min(x + width + pad, box.right);
  const top = Math.max(y - pad, box.top);
  const bottom = Math.min(y + height + pad, box.bottom);
  callMobile("menuPress", {
    ...pack(items), rect: { x: left, y: top, width: right - left, height: Math.max(0, bottom - top) }, radius,
  });
}
