"use client";

/** A page of the Activity panel arrives and leaves as a page on a phone
 *  does: it slides in over the overview from the right, and going back slides
 *  it off again, with the overview coming up from a little to the left and a
 *  little dimmed. A swipe towards the overview drags the page with the
 *  fingers; lifting them far or fast enough finishes the way back, and
 *  anything less lets the page spring home. The overview is in the document
 *  under the page only while the page moves. */
import { useCallback, useLayoutEffect, useMemo, useRef, useState } from "react";
import type { PagePull } from "./activity-page-types";
import { springTo, stillMotion, swipeTurns } from "./sideways";

/** How far left the overview rests while covered, as a share of its width, and how much it dims. */
const PARALLAX = 0.24;
const DIM = 0.5;

export function usePageSlide(onLeft: () => void) {
  const layer = useRef<HTMLDivElement>(null);
  const under = useRef<HTMLElement>(null);
  const [beneath, setBeneath] = useState(false);
  const x = useRef(0);
  const stop = useRef<() => void>(() => {});
  const left = useRef(onLeft);
  left.current = onLeft;

  const apply = useCallback((value: number) => {
    x.current = value;
    const page = layer.current;
    const list = under.current;
    if (page) page.style.transform = value ? `translate3d(${value}px,0,0)` : "";
    if (!list) return;
    const width = list.clientWidth || 1;
    const covered = page ? Math.max(0, 1 - value / width) : 0;
    list.style.transform = covered ? `translate3d(${-PARALLAX * width * covered}px,0,0)` : "";
    list.style.opacity = covered ? String(1 - DIM * covered) : "";
  }, []);
  // Either element may have just entered the document: put it where the slide is.
  useLayoutEffect(() => { apply(x.current); });

  const width = () => layer.current?.clientWidth || under.current?.clientWidth || 0;

  /** Call as the page opens: it comes in from the right. */
  const enter = useCallback(() => {
    stop.current();
    const from = under.current?.clientWidth ?? 0;
    if (stillMotion() || !from) { x.current = 0; return; }
    x.current = from;
    setBeneath(true);
    stop.current = springTo(from, 0, 0, apply, () => setBeneath(false));
  }, [apply]);

  /** Go back: the page slides off, then `onLeft` runs. */
  const leave = useCallback((speed = 0) => {
    stop.current();
    const to = width();
    const done = () => { x.current = 0; setBeneath(false); left.current(); };
    if (stillMotion() || !to || !layer.current) { done(); return; }
    layer.current.style.pointerEvents = "none";
    setBeneath(true);
    stop.current = springTo(x.current, to, speed, apply, done);
  }, [apply]);

  /** Drop any slide under way, with nothing left moving. */
  const cut = useCallback(() => { stop.current(); x.current = 0; setBeneath(false); }, []);

  const pull = useMemo<PagePull>(() => ({
    move: distance => {
      stop.current();
      setBeneath(true);
      apply(Math.max(0, Math.min(distance, width() || distance)));
    },
    end: (distance, speed) => {
      // A flick back towards the page keeps it, however far it was pulled.
      if (speed > -300 && swipeTurns(distance, speed, Math.max(64, width() * 0.33))) { leave(speed); return; }
      stop.current = springTo(x.current, 0, speed, apply, () => setBeneath(false));
    },
  }), [apply, leave]);

  return { layer, under, beneath, enter, leave, cut, pull };
}
