"use client"

// The raised face under the chosen option of a choice groove (ToggleGroup
// `segmented`, TabsList). It sits behind the options and slides to the one
// that is chosen, so a choice reads as one face moving in a groove. It
// follows the choice through the attribute each part sets (`data-state`,
// `aria-selected`), not through React state, so any wrapper on an option
// (a tooltip trigger rewrites `data-state`) leaves it in place.
//
// The groove also takes a press the way a finger would: pressed anywhere,
// the face glides under the pointer and follows it while held, and on
// release the option under the pointer is chosen, the way the option's own
// press would choose it.
//
// Its slide runs past the option by a share of the distance (up to 8px),
// swings back a little and settles, like a damped spring; the groove's
// walls stop it, so at the ends it never leaves the groove.

import * as React from "react"

import { cn } from "@/lib/utils"

const WATCHED = ["data-state", "aria-selected", "aria-checked"]
/** How far the face runs past its option before it settles: a share of the
 *  distance travelled, up to a cap, in px. */
const OVERSHOOT_SHARE = 0.08
const OVERSHOOT_MAX = 8

type Spot = { x: number; y: number; width: number; height: number }

/** Where each remembered groove's face last stood, so a groove drawn again
 *  (a page that is its own route per section) slides from there. */
const LAST = new Map<string, Spot>()

export function SlidingThumb({ selector, memory, className }: {
  /** The chosen option: a selector one of the groove's own children matches. */
  selector: string
  /** A name for this choice; a groove mounted again under the same name
   *  slides from where the last one stood instead of appearing in place. */
  memory?: string
  className?: string
}) {
  const thumb = React.useRef<HTMLSpanElement>(null)

  React.useLayoutEffect(() => {
    const face = thumb.current
    const groove = face?.parentElement
    if (!face || !groove) return
    const options = () => Array.from(groove.children).filter((child): child is HTMLElement => child !== face && child instanceof HTMLElement)
    const chosenOption = () => options().find((child) => child.matches(selector))
    const spotOf = (option: HTMLElement): Spot => {
      const box = groove.getBoundingClientRect()
      const at = option.getBoundingClientRect()
      return { x: at.left - box.left - groove.clientLeft, y: at.top - box.top - groove.clientTop, width: at.width, height: at.height }
    }
    const set = ({ x, y, width, height }: Spot) => {
      face.style.transform = `translate(${x}px, ${y}px)`
      face.style.width = `${width}px`
      face.style.height = `${height}px`
    }
    /** Where the face is drawn now, mid-slide included. */
    const current = (): Spot | null => {
      const box = face.getBoundingClientRect()
      const outer = groove.getBoundingClientRect()
      if (!box.width) return null
      return { x: box.left - outer.left - groove.clientLeft, y: box.top - outer.top - groove.clientTop, width: box.width, height: box.height }
    }
    const still = () => typeof window.matchMedia === "function" && window.matchMedia("(prefers-reduced-motion: reduce)").matches
    const slide = (from: Spot, to: Spot) => {
      set(to)
      if (typeof face.animate !== "function" || still()) return
      for (const running of face.getAnimations()) running.cancel()
      const travel = to.x - from.x
      const direction = Math.sign(travel)
      const frame = ({ x, y, width, height }: Spot) => ({ transform: `translate(${x}px, ${y}px)`, width: `${width}px`, height: `${height}px` })
      // Runs past the option and swings back once, like a damped spring. The
      // groove's walls stop it: it never leaves the groove's padding box.
      const wall = Math.max(0, groove.clientWidth - to.width)
      const past = Math.min(Math.max(to.x + direction * Math.min(Math.abs(travel) * OVERSHOOT_SHARE, OVERSHOOT_MAX), 0), wall)
      const back = to.x - (past - to.x) * 0.3
      const keys = direction
        ? [
            { ...frame(from), easing: "cubic-bezier(0.25, 0.8, 0.35, 1)" },
            { ...frame({ ...to, x: past }), offset: 0.55, easing: "ease-in-out" },
            { ...frame({ ...to, x: back }), offset: 0.8, easing: "ease-in-out" },
            frame(to),
          ]
        : [frame(from), frame(to)]
      face.animate(keys, { duration: 420 })
    }

    let shown = false
    let dragging = false
    const place = (moving: boolean) => {
      if (dragging) return
      const chosen = chosenOption()
      if (!chosen) { face.style.opacity = "0"; shown = false; return }
      const spot = spotOf(chosen)
      const before = memory ? LAST.get(memory) : undefined
      if (memory) LAST.set(memory, spot)
      face.style.opacity = "1"
      if (!shown) {
        shown = true
        // Drawn again after a change of section: slide from where the last
        // one stood. Otherwise the first placement lands without a slide.
        if (before && (before.x !== spot.x || before.width !== spot.width)) slide(before, spot)
        else set(spot)
        return
      }
      const from = current()
      if (moving && from) slide(from, spot)
      else { for (const running of face.getAnimations?.() ?? []) running.cancel(); set(spot) }
    }
    place(false)

    // Press anywhere in the groove, as a finger would: the face glides under
    // the pointer at once and follows it while held; the option under it on
    // release is chosen. Each frame the face closes part of the gap to the
    // pointer, so a press far away and a drag both move it smoothly.
    let press: { id: number; pointer: number; at: Spot; frame: number } | null = null
    const live = () => options().filter((option) => !(option as HTMLButtonElement).disabled && option.getAttribute("aria-disabled") !== "true")
    /** The option nearest a point measured from the groove's inner left edge. */
    const optionAt = (x: number) => {
      let best: HTMLElement | undefined
      let gap = Infinity
      for (const option of live()) {
        const spot = spotOf(option)
        const distance = x < spot.x ? spot.x - x : x > spot.x + spot.width ? x - spot.x - spot.width : 0
        if (distance < gap) { gap = distance; best = option }
      }
      return best
    }
    const inner = (clientX: number) => clientX - groove.getBoundingClientRect().left - groove.clientLeft
    const follow = () => {
      if (!press) return
      const under = optionAt(press.pointer)
      if (under) {
        const all = live()
        const first = spotOf(all[0])
        const last = spotOf(all[all.length - 1])
        const goal = spotOf(under)
        const width = goal.width
        const x = Math.min(Math.max(press.pointer - width / 2, first.x), last.x + last.width - width)
        const ease = 0.35
        press.at = {
          x: press.at.x + (x - press.at.x) * ease,
          y: goal.y,
          width: press.at.width + (width - press.at.width) * ease,
          height: goal.height,
        }
        set(press.at)
      }
      press.frame = requestAnimationFrame(follow)
    }
    const onDown = (event: PointerEvent) => {
      if (event.button !== 0 || !event.isPrimary || press) return
      const option = options().find((child) => child.contains(event.target as Node))
      if (!option || !live().includes(option) || typeof requestAnimationFrame !== "function") return
      // Keep the browser from choosing on press (Tabs choose on mouse down)
      // and from selecting the options' text; the release chooses.
      event.preventDefault()
      groove.setPointerCapture?.(event.pointerId)
      for (const running of face.getAnimations?.() ?? []) running.cancel()
      const at = current() ?? spotOf(option)
      dragging = true
      groove.dataset.dragging = ""
      press = { id: event.pointerId, pointer: inner(event.clientX), at, frame: requestAnimationFrame(follow) }
    }
    const onMove = (event: PointerEvent) => {
      if (press && event.pointerId === press.id) press.pointer = inner(event.clientX)
    }
    const end = (event: PointerEvent, choose: boolean) => {
      if (!press || event.pointerId !== press.id) return
      cancelAnimationFrame(press.frame)
      const target = choose ? optionAt(inner(event.clientX)) : undefined
      press = null
      dragging = false
      delete groove.dataset.dragging
      groove.releasePointerCapture?.(event.pointerId)
      if (target && target !== chosenOption()) {
        // Choose it the way a press does: Tabs choose on mouse down, toggle
        // groups and plain buttons on click.
        target.dispatchEvent(new MouseEvent("mousedown", { bubbles: true, cancelable: true, button: 0 }))
        target.dispatchEvent(new MouseEvent("click", { bubbles: true, cancelable: true, button: 0 }))
      } else {
        place(true)
      }
      // Focus follows the choice, as a press would move it, without the
      // keyboard focus ring a pointer press does not show.
      target?.focus({ preventScroll: true, focusVisible: false } as FocusOptions)
      if (!choose) return
      // The browser's own click for this press follows; the release already chose.
      const swallow = (click: MouseEvent) => { click.stopPropagation(); click.preventDefault() }
      groove.addEventListener("click", swallow, { capture: true, once: true })
      setTimeout(() => groove.removeEventListener("click", swallow, { capture: true }), 0)
    }
    const onUp = (event: PointerEvent) => end(event, true)
    const onCancel = (event: PointerEvent) => end(event, false)
    groove.addEventListener("pointerdown", onDown)
    groove.addEventListener("pointermove", onMove)
    groove.addEventListener("pointerup", onUp)
    groove.addEventListener("pointercancel", onCancel)

    // A page without layout (server render, a test DOM) keeps the first placement.
    const sizes = typeof ResizeObserver === "undefined" ? null : new ResizeObserver(() => place(false))
    sizes?.observe(groove)
    for (const child of options()) sizes?.observe(child)
    // The thumb's own style writes are not a change of choice.
    const choices = typeof MutationObserver === "undefined" ? null
      : new MutationObserver((records) => { if (records.some((record) => record.target !== face)) place(true) })
    choices?.observe(groove, { attributes: true, attributeFilter: WATCHED, subtree: true, childList: true })
    return () => {
      sizes?.disconnect()
      choices?.disconnect()
      groove.removeEventListener("pointerdown", onDown)
      groove.removeEventListener("pointermove", onMove)
      groove.removeEventListener("pointerup", onUp)
      groove.removeEventListener("pointercancel", onCancel)
    }
  }, [selector, memory])

  return (
    <span
      ref={thumb}
      aria-hidden="true"
      data-slot="sliding-thumb"
      className={cn(
        "pointer-events-none absolute top-0 left-0 z-0 rounded-md bg-bg-input opacity-0 shadow-raised",
        className
      )}
    />
  )
}
