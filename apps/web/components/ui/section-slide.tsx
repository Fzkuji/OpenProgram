"use client"

// The content under a page's section switch. When the section changes, the
// new content slides in from the side the switch moved towards (a later
// section from the right, an earlier one from the left) and fades in, so the
// switch reads as moving the whole page, not as changing one setting. The
// first section shows without motion. The last section is remembered by
// name, so a page that is its own route per section (and so is drawn anew)
// still slides.

import * as React from "react"

const LAST = new Map<string, number>()

/** Slide `targets()` in when the section named `memory` moves to `index`. */
export function useSectionEnter(memory: string | undefined, index: number, targets: () => Element[]) {
  React.useLayoutEffect(() => {
    if (!memory || index < 0) return
    const last = LAST.get(memory)
    LAST.set(memory, index)
    if (last === undefined || last === index) return
    const direction = index > last ? "forward" : "back"
    for (const element of targets()) {
      if (!(element instanceof HTMLElement)) continue
      // Restart the entrance (motion.css) even if the last one is still on.
      delete element.dataset.sectionEnter
      void element.offsetWidth
      element.dataset.sectionEnter = direction
      element.addEventListener("animationend", function done(event) {
        if (event.target !== element) return
        delete element.dataset.sectionEnter
        element.removeEventListener("animationend", done)
      })
    }
    // `targets` is read when the section changes, not when it is redefined.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [memory, index])
}

export function SectionSlide({ index, memory, children, ...props }: React.ComponentProps<"div"> & {
  /** The position of the shown section in the switch. */
  index: number
  /** The name of the switch this content follows. */
  memory: string
}) {
  const box = React.useRef<HTMLDivElement>(null)
  useSectionEnter(memory, index, () => (box.current ? [box.current] : []))
  return (
    <div ref={box} data-slot="section-slide" {...props}>
      {children}
    </div>
  )
}
