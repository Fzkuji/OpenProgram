"use client";

import { useState } from "react";
import { AnimatePresence, motion, useIsPresent, useReducedMotion, type Transition } from "framer-motion";

const movement = { duration: .2, ease: "easeOut" } as const;
const clarity: Transition = { duration: .2, times: [0,.342,.563,1], ease: ["linear","linear","easeOut"] };
// Each keyframe list starts from the layer's enter value, not null: a null
// start is read back from the page, which forced a style pass in the
// keypress that changed the level (traced).
const layers = [
  { radius: 0, enter: 0, center: [0,0,0,1], exit: 0 },
  { radius: .5, enter: 0, center: [0,0,1,0], exit: 0 },
  { radius: 1, enter: 0, center: [0,1,0,0], exit: 0 },
  { radius: 2, enter: 1, center: [1,0,0,0], exit: 1 },
];
// The slide is a whole transform string, which framer hands to the
// compositor; its own `y` runs on the main thread every frame.
const shift = (em: number) => `translateY(${em}em)`;

export function EffortLabel({ label, index, accent }: { label: string; index: number; accent: boolean }) {
  const reduced = useReducedMotion();
  const [previous, setPrevious] = useState({ index, direction: 1, sequence: 0 });
  let current = previous;
  if (previous.index !== index) {
    current = { index, direction: index > previous.index ? 1 : -1, sequence: previous.sequence + 1 };
    setPrevious(current);
  }
  return <span className="effort-level" data-accent={accent ? "true" : undefined}>
    <span className="sr-only">{label}</span>
    {reduced ? <span aria-hidden="true">{label}</span> : <span aria-hidden="true" className="effort-label-stack">
      <AnimatePresence initial={false} custom={current.direction}>
        <LabelLayer key={current.sequence} direction={current.direction} label={label} />
      </AnimatePresence>
    </span>}
  </span>;
}

/** One label in the stack. The outgoing one leaves the flow (data-leaving,
 *  effort-pill.css), so the stack takes the new label's width at once
 *  without the layout measuring that AnimatePresence's popLayout does. */
function LabelLayer({ direction, label }: { direction: number; label: string }) {
  const present = useIsPresent();
  return <motion.span custom={direction} className="effort-label-motion" data-leaving={present ? undefined : ""}
    initial="enter" animate="center" exit="exit"
    variants={{ enter: (d: number) => ({ transform: shift(d*.55), opacity: 0 }), center: { transform: shift(0), opacity: 1 }, exit: (d: number) => ({ transform: shift(-d*.55), opacity: 0 }) }} transition={movement}>
    {layers.map(layer => <motion.span key={layer.radius} className={layer.radius ? "effort-label-blur" : "effort-label-sharp"}
      style={layer.radius ? { filter: `blur(${layer.radius}px)`, mixBlendMode: "plus-lighter" } : undefined}
      variants={{ enter: { opacity: layer.enter }, center: { opacity: layer.center, transition: clarity }, exit: { opacity: layer.exit, transition: movement } }}>{label}</motion.span>)}
  </motion.span>;
}
