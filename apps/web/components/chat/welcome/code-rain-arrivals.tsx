/**
 * CodeRainArrivals — with the code rain on (Settings → General → Welcome
 * animation), a message that arrives live comes in through the rain.
 *
 * Every new character, in a sent message or a streamed reply alike, gets
 * its own stream falling from above. It stays hidden until its stream
 * lands, shows as code for a moment, then decodes into the real character
 * and fades from green to the text colour. Streams start at random moments
 * shortly after their character arrives, so text shows roughly in the
 * order it was written with neighbours landing in random order. History
 * that loads or comes back into view shows as it is. In-progress labels
 * (the "Thinking… ›" row, › included, and a running step's title:
 * CodeRainText) rain in the same way, again whenever their text changes,
 * and while they stay up a character now and then flickers back into code.
 *
 * Nothing in the transcript DOM changes. The characters not yet decoded
 * are hidden and tinted through CSS custom highlights (transcript.css),
 * and the drops and code glyphs are drawn on one canvas over the chat
 * area, so selection, find-in-page and React rendering are untouched.
 */
"use client";

import { useEffect } from "react";

import { CODE_RAIN_FONT, codeRainPalette, fillMirrored, mirror, randomGlyph } from "@/lib/chat/code-rain";
import { useWelcomeBackdropPref } from "@/lib/prefs/appearance/welcome-backdrop-pref";

/** Seconds a decoded character takes to fade from green to its colour. */
const FADE = 0.6;
const FADE_STEPS = 6;
/** Glyph spacing in a falling stream. */
const STEP = 18;
/** Seconds a stream takes to fade out once its head has landed. */
const LAND_FADE = 0.15;
/** Each character's stream: how far it falls (px), how fast (px/s), and
 *  how many glyphs long it is. */
const FALL = [150, 240] as const;
const SPEED = 700;
const LEN = [6, 11] as const;
/** Seconds after a character arrives within which its stream starts, at a
 *  random moment: the spread that shuffles neighbouring characters. */
const WINDOW = 0.7;
/** How often a settled label character flickers back into code (per
 *  second), and for how long (s). */
const FLICKER_RATE = 0.22;
const FLICKER = [0.09, 0.21] as const;
/** Past this many new characters, only those on screen are animated. */
const MAX_ANIMATED = 900;

type CharState = { ch: string; reveal: number; settle: number; glyph: string; next: number; fade: number };
type Tracker = {
  el: HTMLElement;
  role: "user" | "assistant" | "label";
  /** Characters of its text already handed out (decoded or in flight). */
  seen: number;
  /** Its text when last scheduled; a label whose text changes rains anew. */
  last: string;
  chars: Map<number, CharState>;
  observer: MutationObserver | null;
  dirty: boolean;
  /** When the element left the DOM; a re-keyed row is picked up again. */
  lostAt: number | null;
  /** Text positions, kept until the element's DOM changes: rebuilding
   *  them walks every text node of a long reply. */
  locate: Locator | null;
  ranges: Map<number, Range>;
  /** A label's next flicker. */
  nextFlicker: number;
};
type Locator = (index: number, length: number) => Range | null;
type Fresh = { index: number; ch: string; rect: DOMRect; fade: number };
/** A drop lands on one character (``anchor``) and is re-aimed at it every
 *  frame: the chat scrolls while a reply streams, and text reflows. */
type Drop = {
  x: number; y0: number; start: number; stopY: number; v: number; len: number; glyphs: string[];
  anchor: { tracker: Tracker; index: number; length: number };
};

const ROOTS = { user: ".user-md", assistant: ".chat-text" } as const;

export function CodeRainArrivals({ mount }: { mount: HTMLElement }) {
  const { enabled } = useWelcomeBackdropPref();
  useEffect(() => {
    if (!enabled) return;
    if (typeof Highlight === "undefined" || !("highlights" in CSS)) return;
    if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) return;
    return startArrivals(mount);
  }, [enabled, mount]);
  return null;
}

function startArrivals(mount: HTMLElement): () => void {
  const canvas = document.createElement("canvas");
  canvas.setAttribute("aria-hidden", "true");
  canvas.className = "code-rain-arrivals";
  document.body.append(canvas);
  const ctx = canvas.getContext("2d");
  if (!ctx) { canvas.remove(); return () => {}; }

  const hidden = new Highlight();
  const fades = Array.from({ length: FADE_STEPS }, () => new Highlight());
  CSS.highlights.set("code-rain-hidden", hidden);
  fades.forEach((h, i) => CSS.highlights.set(`code-rain-fade-${i}`, h));

  const trackers: Tracker[] = [];
  let drops: Drop[] = [];
  let raf = 0;
  let running = false;
  const now = () => performance.now() / 1000;
  const area = () => mount.closest<HTMLElement>(".chat-area");

  // ---- Text model: a message's text is the concatenation of its roots ----
  function roots(t: Tracker): HTMLElement[] {
    if (t.role === "label") return [t.el];
    return Array.from(t.el.querySelectorAll<HTMLElement>(ROOTS[t.role]));
  }
  function textOf(t: Tracker): string {
    return roots(t).map((r) => r.textContent ?? "").join("");
  }
  /** Maps text offsets to DOM positions. A streamed reply's markdown
   *  replaces its text nodes on every piece, so the element's observer
   *  drops it (and the ranges made from it) on every change. */
  function locator(t: Tracker): Locator {
    const nodes: Text[] = [];
    const starts: number[] = [];
    let total = 0;
    for (const root of roots(t)) {
      const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
      for (let n = walker.nextNode(); n; n = walker.nextNode()) {
        nodes.push(n as Text);
        starts.push(total);
        total += (n as Text).data.length;
      }
    }
    return (index: number, length: number): Range | null => {
      let lo = 0, hi = nodes.length - 1;
      while (lo < hi) {
        const mid = (lo + hi + 1) >> 1;
        if (starts[mid] <= index) lo = mid; else hi = mid - 1;
      }
      const node = nodes[lo];
      if (!node) return null;
      const offset = index - starts[lo];
      if (offset + length > node.data.length) return null;
      const range = document.createRange();
      range.setStart(node, offset);
      range.setEnd(node, offset + length);
      return range;
    };
  }
  /** The same Range for a character from frame to frame, so the
   *  highlights only change where a character's state does. */
  function rangeAt(t: Tracker, index: number, length: number): Range | null {
    const cached = t.ranges.get(index);
    if (cached) return cached;
    t.locate ??= locator(t);
    const range = t.locate(index, length);
    if (range) t.ranges.set(index, range);
    return range;
  }

  // ---- New characters get their timing ----
  function schedule(t: Tracker) {
    t.dirty = false;
    const text = textOf(t);
    if (t.role === "label" && !text.startsWith(t.last)) {
      t.chars.clear();
      t.seen = 0;
    }
    t.last = text;
    if (text.length < t.seen) {
      // The markdown re-rendered shorter (a closing marker consumed text).
      for (const i of t.chars.keys()) if (i >= text.length) t.chars.delete(i);
      t.seen = text.length;
      return;
    }
    if (text.length === t.seen) return;
    const from = t.seen;
    t.seen = text.length;
    const box = area()?.getBoundingClientRect();
    if (!box) return;
    const fresh: Fresh[] = [];
    for (let i = from; i < text.length; i++) {
      let ch = text[i];
      const code = ch.charCodeAt(0);
      if (code >= 0xd800 && code <= 0xdbff && i + 1 < text.length) ch = text.slice(i, i + 2);
      const at = i;
      i += ch.length - 1;
      if (!ch.trim()) continue;
      const range = rangeAt(t, at, ch.length);
      const rect = range?.getClientRects()[0];
      if (!range || !rect || !rect.width) continue;
      // Off-screen text is not animated: it would decode unseen.
      if (rect.bottom < box.top || rect.top > box.bottom) continue;
      fresh.push({ index: at, ch, rect, fade: plainColour(range) ? FADE : 0 });
    }
    if (fresh.length > MAX_ANIMATED) fresh.length = MAX_ANIMATED;
    if (!fresh.length) return;
    rainOnto(t, fresh, box, now());
  }

  /** Every new character gets its own stream from above, sent and
   *  streamed text alike. Each one falls at a random moment within WINDOW
   *  of its arrival, so text shows roughly in the order it was written
   *  while neighbouring characters land in random order; faster text only
   *  means more streams at once. A character decodes as its stream lands. */
  function rainOnto(t: Tracker, fresh: Fresh[], box: DOMRect, t0: number) {
    for (const f of fresh) {
      const x = f.rect.left + f.rect.width / 2 - box.left;
      const y = f.rect.top + f.rect.height / 2 - box.top;
      const start = t0 + Math.random() * WINDOW;
      const fall = FALL[0] + Math.random() * (FALL[1] - FALL[0]);
      const len = LEN[0] + Math.floor(Math.random() * (LEN[1] - LEN[0] + 1));
      drops.push(drop(t, f, x, y - fall, start, y, SPEED, len));
      const reveal = start + fall / SPEED;
      t.chars.set(f.index, state(f, reveal, reveal + 0.12 + Math.random() * 0.13));
    }
  }

  const state = (f: Fresh, reveal: number, settle: number): CharState =>
    ({ ch: f.ch, reveal, settle, glyph: randomGlyph(), next: 0, fade: f.fade });
  /** The green fades into the message's body colour (transcript.css), so
   *  only characters in that colour fade; code, links and the like decode
   *  straight into their own colour. */
  function plainColour(range: Range): boolean {
    const parent = range.startContainer.parentElement;
    const root = parent?.closest<HTMLElement>(`${ROOTS.user}, ${ROOTS.assistant}`);
    return !!parent && !!root && getComputedStyle(parent).color === getComputedStyle(root).color;
  }
  const drop = (tracker: Tracker, f: Fresh, x: number, y0: number, start: number, stopY: number, v: number, len: number): Drop => ({
    x, y0, start, stopY, v, len, glyphs: Array.from({ length: len }, randomGlyph),
    anchor: { tracker, index: f.index, length: f.ch.length },
  });

  // ---- Tracking messages that arrive live ----
  function track(el: HTMLElement) {
    const role = el.classList.contains("code-rain-text") ? "label"
      : el.classList.contains("user") ? "user" : el.classList.contains("assistant") ? "assistant" : null;
    // A label in a fold row rains in with its whole row, › included.
    if (role === "label") el = el.closest<HTMLElement>(".tl-toggle") ?? el;
    if (!role || trackers.some((t) => t.el === el)) return;
    // A row re-keyed by the server (the sent turn's local id replaced by
    // its real one) mounts again with the same text: keep its timing.
    const text = el.querySelector(ROOTS.user)?.textContent ?? "";
    const lost = role === "user"
      ? trackers.find((t) => t.lostAt !== null && t.role === "user" && textOf({ ...t, el }) === text)
      : undefined;
    if (lost) {
      lost.el = el;
      lost.lostAt = null;
      observe(lost);
      return;
    }
    const t: Tracker = {
      el, role, seen: 0, last: "", chars: new Map(), observer: null, dirty: true, lostAt: null,
      locate: null, ranges: new Map(), nextFlicker: 0,
    };
    trackers.push(t);
    // Older replies stop decoding after a while; keep the work bounded.
    // Labels go once they leave the DOM.
    const messages = () => trackers.filter((o) => o.role !== "label");
    while (messages().length > 4) {
      const oldest = messages()[0];
      dispose(oldest);
      trackers.splice(trackers.indexOf(oldest), 1);
    }
    observe(t);
    wake();
  }
  /** A message sent while a reply runs shows twice for a moment: its own
   *  row, and the steering bubble inside the reply where it ends up. The
   *  rain follows the steering bubble, and finds the message again if its
   *  row goes away first. */
  function follow(t: Tracker) {
    // Runs every frame while the message rains in, so it looks only at
    // steering bubbles unless the message has lost its element.
    const connected = t.el.isConnected;
    if (connected && t.el.closest(".steering-input")) return;
    const candidates = mount.querySelectorAll<HTMLElement>(connected ? ".steering-input .message.user" : ".message.user");
    if (!candidates.length) return;
    const text = textOf(t);
    let next: HTMLElement | null = null;
    for (const el of candidates) {
      if (el === t.el || trackers.some((o) => o.el === el)) continue;
      if ((el.querySelector(ROOTS.user)?.textContent ?? "") !== text) continue;
      next = el;
      if (el.closest(".steering-input")) break;
    }
    if (!next) return;
    t.el = next;
    t.lostAt = null;
    observe(t);
  }
  function observe(t: Tracker) {
    t.observer?.disconnect();
    forget(t);
    t.observer = new MutationObserver(() => { t.dirty = true; forget(t); wake(); });
    t.observer.observe(t.el, { childList: true, subtree: true, characterData: true });
  }
  function forget(t: Tracker) {
    t.locate = null;
    t.ranges.clear();
  }
  function dispose(t: Tracker) {
    t.observer?.disconnect();
    t.chars.clear();
    forget(t);
  }
  const rows = new MutationObserver((records) => {
    for (const record of records) {
      for (const node of record.addedNodes) {
        if (!(node instanceof HTMLElement)) continue;
        if (node.matches(".code-rain-text")) track(node);
        node.querySelectorAll<HTMLElement>(".code-rain-text").forEach(track);
        const row = node.matches("[data-appear]") ? node : node.querySelector<HTMLElement>("[data-appear]");
        if (!row) continue;
        for (const message of row.querySelectorAll<HTMLElement>(".message.user, .message.assistant")) {
          if (!message.parentElement?.closest(".message")) track(message);
        }
      }
    }
  });
  rows.observe(mount, { childList: true, subtree: true });

  // ---- Drawing ----
  /** Between flickers, with nothing else moving, the loop sleeps on a
   *  timer instead of drawing empty frames. */
  let nap = 0;
  function wake() {
    window.clearTimeout(nap);
    if (running) return;
    running = true;
    raf = requestAnimationFrame(frame);
  }
  /** A label that has stopped being one (its row finished running). */
  const retired = (tr: Tracker) =>
    tr.role === "label" && !tr.el.matches(".code-rain-text") && !tr.el.querySelector(".code-rain-text");
  function frame() {
    const t = now();
    for (const tr of trackers) {
      if (tr.role === "user" && tr.chars.size) follow(tr);
      if (!tr.el.isConnected || retired(tr)) { if (tr.lostAt === null) tr.lostAt = t; continue; }
      if (tr.dirty) schedule(tr);
      if (tr.role === "label" && t >= tr.nextFlicker) flicker(tr, t);
    }
    for (let i = trackers.length - 1; i >= 0; i--) {
      const tr = trackers[i];
      if (tr.lostAt !== null && t - tr.lostAt > 1) { dispose(tr); trackers.splice(i, 1); }
    }
    if (paint(t)) { raf = requestAnimationFrame(frame); return; }
    running = false;
    clear();
    // Wake for the next flicker, or to let go of an element that left.
    const due = trackers.map((tr) => tr.lostAt !== null ? tr.lostAt + 1.05 : tr.role === "label" ? tr.nextFlicker : Infinity);
    const next = Math.min(...due);
    if (next < Infinity) nap = window.setTimeout(wake, Math.max(0, next - now()) * 1000);
  }
  /** Now and then a settled label character flickers back into code for a
   *  moment, the way text scrambles in The Matrix: on average FLICKER_RATE
   *  times a second for each character. */
  function flicker(tr: Tracker, t: number) {
    const text = textOf(tr);
    const settled: number[] = [];
    for (let i = 0; i < text.length; i++) if (text[i].trim() && !tr.chars.has(i)) settled.push(i);
    const first = tr.nextFlicker === 0;
    tr.nextFlicker = t - Math.log(1 - Math.random()) / (FLICKER_RATE * Math.max(1, settled.length));
    // A new label rains in first; its flicker starts afterwards.
    if (first || !settled.length) return;
    const i = settled[Math.floor(Math.random() * settled.length)];
    const until = t + FLICKER[0] + Math.random() * (FLICKER[1] - FLICKER[0]);
    // Revealed already, so it shows as code at once, without the bright head.
    tr.chars.set(i, { ch: text[i], reveal: t - 1, settle: until, glyph: randomGlyph(), next: 0, fade: 0 });
  }
  function clear() {
    ctx!.clearRect(0, 0, canvas.width, canvas.height);
    hidden.clear();
    fades.forEach((h) => h.clear());
  }
  /** Brings a highlight to exactly ``want``, touching only what changed. */
  function sync(highlight: Highlight, want: Set<AbstractRange>) {
    for (const range of highlight) if (!want.has(range)) highlight.delete(range);
    for (const range of want) highlight.add(range);
  }

  function paint(t: number): boolean {
    const el = area();
    if (!el) return false;
    const box = el.getBoundingClientRect();
    const dpr = window.devicePixelRatio || 1;
    const w = Math.round(box.width * dpr), h = Math.round(box.height * dpr);
    if (canvas.width !== w || canvas.height !== h) { canvas.width = w; canvas.height = h; }
    // Style writes only when the area moved, not on every frame.
    const place = `${box.left},${box.top},${box.width},${box.height}`;
    if (canvas.dataset.place !== place) {
      canvas.dataset.place = place;
      Object.assign(canvas.style, {
        left: `${box.left}px`, top: `${box.top}px`, width: `${box.width}px`, height: `${box.height}px`,
      });
    }
    ctx!.setTransform(1, 0, 0, 1, 0, 0);
    ctx!.clearRect(0, 0, w, h);
    ctx!.setTransform(dpr, 0, 0, dpr, 0, 0);
    // Never draw over the composer floating at the bottom of the chat.
    const composer = document.getElementById("composer-mount")?.firstElementChild?.getBoundingClientRect();
    const floor = composer && composer.top < box.bottom && composer.bottom > box.top ? composer.top - box.top : box.height;
    ctx!.save();
    ctx!.beginPath();
    ctx!.rect(0, 0, box.width, floor);
    ctx!.clip();
    const pal = codeRainPalette();
    ctx!.textAlign = "center";
    ctx!.textBaseline = "middle";
    mirror(ctx!);

    const wantHidden = new Set<Range>();
    const wantFades = fades.map(() => new Set<Range>());
    let busy = drops.length > 0;

    // Characters: hidden until reached, code while decoding, then green.
    for (const tr of trackers) {
      if (!tr.chars.size || tr.lostAt !== null) continue;
      busy = true;
      for (const [index, c] of tr.chars) {
        if (t >= c.settle + c.fade) { tr.chars.delete(index); continue; }
        const range = rangeAt(tr, index, c.ch.length);
        if (!range) { tr.chars.delete(index); continue; }
        if (t >= c.settle) {
          wantFades[Math.min(FADE_STEPS - 1, Math.floor(((t - c.settle) / FADE) * FADE_STEPS))].add(range);
          continue;
        }
        wantHidden.add(range);
        if (t < c.reveal) continue;
        const rect = range.getClientRects()[0];
        if (!rect) continue;
        if (t >= c.next) { c.glyph = randomGlyph(); c.next = t + 0.05 + Math.random() * 0.05; }
        const hot = t - c.reveal < 0.08;
        ctx!.font = `600 ${Math.max(11, Math.round(rect.height * 0.72))}px ${CODE_RAIN_FONT}`;
        ctx!.fillStyle = hot ? pal.head : pal.tail;
        ctx!.globalAlpha = 1;
        ctx!.shadowColor = pal.tail;
        ctx!.shadowBlur = pal.dark ? (hot ? 10 : 5) : 0;
        fillMirrored(ctx!, c.glyph, rect.left + rect.width / 2 - box.left, rect.top + rect.height / 2 - box.top);
      }
    }
    sync(hidden, wantHidden);
    fades.forEach((highlight, i) => sync(highlight, wantFades[i]));

    // Drops: a bright head and a fading tail that fall onto the text and
    // fade where they land, so nothing ever rises back up.
    ctx!.font = `600 15px ${CODE_RAIN_FONT}`;
    drops = drops.filter((d) => {
      // Follow the character: same fall length, landing where it is now.
      const { tracker, index, length } = d.anchor;
      const rect = tracker.lostAt === null && tracker.el.isConnected
        ? rangeAt(tracker, index, length)?.getClientRects()[0] : undefined;
      // A stream whose message is between elements waits, unseen, rather
      // than landing where the old element was.
      if (!rect && !trackers.includes(tracker)) return false;
      if (!rect && tracker.lostAt !== null) return true;
      if (rect) {
        const stopY = rect.top + rect.height / 2 - box.top;
        d.y0 += stopY - d.stopY;
        d.stopY = stopY;
        d.x = rect.left + rect.width / 2 - box.left;
      }
      const head = d.y0 + (t - d.start) * d.v;
      if (head < d.y0) return true;
      // Once the head lands, the stream stops where it is and fades out
      // instead of pouring its tail onto text that has already decoded.
      const landed = (head - d.stopY) / d.v;
      if (landed >= LAND_FADE) return false;
      const keep = landed > 0 ? 1 - landed / LAND_FADE : 1;
      const tip = Math.min(head, d.stopY);
      for (let k = 0; k < d.len; k++) {
        const y = tip - k * STEP;
        if (y < d.y0) break;
        // Streams fade in where they start instead of popping into view.
        const enter = keep * Math.min(1, 0.25 + (y - d.y0) / (3 * STEP));
        if (k === 0) {
          ctx!.fillStyle = pal.head;
          ctx!.globalAlpha = enter;
          ctx!.shadowColor = pal.tail;
          ctx!.shadowBlur = pal.dark ? 10 : 0;
        } else {
          ctx!.fillStyle = pal.tail;
          ctx!.globalAlpha = enter * Math.max(0.05, 1 - k / d.len) * (pal.dark ? 0.9 : 0.75);
          ctx!.shadowBlur = 0;
        }
        if (Math.random() < 0.03) d.glyphs[k] = randomGlyph();
        fillMirrored(ctx!, d.glyphs[k], d.x, y);
      }
      return true;
    });
    ctx!.restore();
    ctx!.globalAlpha = 1;
    ctx!.shadowBlur = 0;
    return busy;
  }

  return () => {
    cancelAnimationFrame(raf);
    window.clearTimeout(nap);
    rows.disconnect();
    trackers.forEach(dispose);
    CSS.highlights.delete("code-rain-hidden");
    fades.forEach((_, i) => CSS.highlights.delete(`code-rain-fade-${i}`));
    canvas.remove();
  };
}
