/** The character renderer loads on first use, like the optional DiceBear
 *  styles: a page that shows no Bot never pays for it. */
import type { AvatarDefinition } from "open-bot-avatar";
import type { mountAvatar } from "open-bot-avatar/dom";

import { BOT_SHAPES, DEFAULT_BOT_SHAPE, type BotShape } from "./catalog";

export interface BotRuntime {
  mountAvatar: typeof mountAvatar;
  definition(shape: string): AvatarDefinition;
  /** Prepare a shape's volume, the renderer's costly first step for it
   *  (./warm.ts spreads these out). */
  prepare(shape: string): void;
}

let ready: BotRuntime | undefined;
let inflight: Promise<BotRuntime> | undefined;

export function loadedBotRuntime(): BotRuntime | undefined {
  return ready;
}

export function loadBotRuntime(): Promise<BotRuntime> {
  if (ready) return Promise.resolve(ready);
  if (inflight) return inflight;
  const request = Promise.all([
    import("open-bot-avatar"),
    import("open-bot-avatar/dom"),
    import("./gallery"),
  ]).then(([core, dom, gallery]) => {
    const byId = new Map<string, AvatarDefinition>();
    for (const definition of [...core.examples, ...gallery.GALLERY_DEFINITIONS]) byId.set(definition.id, definition);
    const missing = BOT_SHAPES.filter((shape) => !byId.has(shape));
    if (missing.length) throw new Error(`Bot shapes without a definition: ${missing.join(", ")}`);
    const fallback = byId.get(DEFAULT_BOT_SHAPE)!;
    const definition = (shape: string) => byId.get(shape as BotShape) ?? fallback;
    ready = {
      mountAvatar: dom.mountAvatar,
      definition,
      // The renderer keeps a prepared shape for the page's life; drawing it
      // once still is what prepares it.
      prepare: (shape) => { core.renderAvatarSvg(definition(shape), { reducedMotion: true }); },
    };
    return ready;
  }).finally(() => { inflight = undefined; });
  inflight = request;
  return request;
}
