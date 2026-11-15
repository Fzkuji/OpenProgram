"use client";

import { useState } from "react";
import { AnimatePresence, motion, useReducedMotion, type Transition } from "framer-motion";

const movement = { duration: .2, ease: "easeOut" } as const;
const clarity: Transition = { duration: .2, times: [0,.342,.563,1], ease: ["linear","linear","easeOut"] };
const layers = [
  { radius: 0, enter: 0, center: [null,0,0,1], exit: 0 },
  { radius: .5, enter: 0, center: [null,0,1,0], exit: 0 },
  { radius: 1, enter: 0, center: [null,1,0,0], exit: 0 },
  { radius: 2, enter: 1, center: [null,0,0,0], exit: 1 },
];

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
      <AnimatePresence initial={false} mode="popLayout" custom={current.direction}>
        <motion.span key={current.sequence} custom={current.direction} className="effort-label-motion"
          initial="enter" animate="center" exit="exit"
          variants={{ enter: (d: number) => ({ y: `${d*.55}em`, opacity: 0 }), center: { y: 0, opacity: 1 }, exit: (d: number) => ({ y: `${-d*.55}em`, opacity: 0 }) }} transition={movement}>
          {layers.map(layer => <motion.span key={layer.radius} className={layer.radius ? "effort-label-blur" : "effort-label-sharp"}
            style={layer.radius ? { filter: `blur(${layer.radius}px)`, mixBlendMode: "plus-lighter" } : undefined}
            variants={{ enter: { opacity: layer.enter }, center: { opacity: layer.center, transition: clarity }, exit: { opacity: layer.exit, transition: movement } }}>{label}</motion.span>)}
        </motion.span>
      </AnimatePresence>
    </span>}
  </span>;
}
