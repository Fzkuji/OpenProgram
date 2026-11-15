/** Optional generators load per selected style; default shapes stays synchronous. */
import * as shapes from "@dicebear/shapes";
import type { AvatarStyle } from "./types";
type Generator = typeof shapes;
export const STYLES = {
  shapes: () => Promise.resolve(shapes),
  avataaars: () => import("@dicebear/avataaars"),
  adventurer: () => import("@dicebear/adventurer"),
  micah: () => import("@dicebear/micah"),
  openPeeps: () => import("@dicebear/open-peeps"),
  personas: () => import("@dicebear/personas"),
  bigSmile: () => import("@dicebear/big-smile"),
  funEmoji: () => import("@dicebear/fun-emoji"),
  bottts: () => import("@dicebear/bottts"),
  thumbs: () => import("@dicebear/thumbs"),
  pixelArt: () => import("@dicebear/pixel-art"),
  identicon: () => import("@dicebear/identicon"),
  rings: () => import("@dicebear/rings"),
  initials: () => import("@dicebear/initials"),
} as const;
const ready = new Map<AvatarStyle, Generator>([["shapes", shapes]]);
const inflight = new Map<AvatarStyle, Promise<Generator>>();
export function loadedAvatarStyle(style: AvatarStyle): Generator | undefined {
  return ready.get(style);
}
export function loadAvatarStyle(style: AvatarStyle): Promise<Generator> {
  const hit = ready.get(style);
  if (hit) return Promise.resolve(hit);
  const loading = inflight.get(style);
  if (loading) return loading;
  const loader = STYLES[style];
  if (typeof loader !== "function") return Promise.reject(new Error("Unknown avatar style"));
  const request = Promise.resolve().then(loader).then(module => {
    const generator = module as Generator;
    ready.set(style, generator);
    return generator;
  }).finally(() => inflight.delete(style));
  inflight.set(style, request);
  return request;
}
