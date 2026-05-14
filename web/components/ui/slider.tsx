"use client";

import * as React from "react";
import * as SliderPrimitive from "@radix-ui/react-slider";

import { cn } from "@/lib/utils";

type SliderProps = React.ComponentPropsWithoutRef<
  typeof SliderPrimitive.Root
> & {
  /** Draw N evenly-spaced tick marks centred on each step position.
      Pass `options.length` for a discrete step slider; omit / pass
      < 2 for a smooth slider. */
  stops?: number;
  /** Skip drawing the first and last tick. Use when the start/end
      markers are filled by `startIcon` / `endIcon` (or some other
      external marker) instead of plain tick dots. */
  innerTicksOnly?: boolean;
  /** Element rendered IN PLACE of the first tick — sits centred on
      the thumb's min position (cx = 7px) on the track. Use this to
      put an icon (e.g. a small lightning bolt) at the slider's left
      end. Receives `pointer-events: auto` so it can be clickable. */
  startIcon?: React.ReactNode;
  /** Element rendered IN PLACE of the last tick — centred on the
      thumb's max position (cx = 100% − 7px). */
  endIcon?: React.ReactNode;
};

const Slider = React.forwardRef<
  React.ElementRef<typeof SliderPrimitive.Root>,
  SliderProps
>(({ className, stops, innerTicksOnly, startIcon, endIcon, ...props }, ref) => {
  return (
  <SliderPrimitive.Root
    ref={ref}
    // `h-full` is the key for hit area: Radix accepts a click anywhere
    // on the Root and snaps the thumb to its x — but if the Root has no
    // explicit height it collapses to the 4px track and the user has
    // to click that narrow strip. Making the Root fill its parent
    // (32px tall inside the effort pill) gives an 8× more forgiving
    // click target while the track + thumb stay visually 4px / 14px
    // via `items-center`.
    className={cn(
      "relative flex h-full w-full touch-none select-none items-center",
      className,
    )}
    {...props}
  >
    {/* Track colour matches the unfilled segment — `text-bright` is
        near-white on dark and near-black on light, so the line stays
        high-contrast against either panel background. */}
    <SliderPrimitive.Track className="relative h-[4px] w-full grow overflow-hidden rounded-full bg-text-bright">
      <SliderPrimitive.Range className="absolute h-full bg-[var(--accent-blue)]" />
    </SliderPrimitive.Track>
    {stops && stops > 1
      ? Array.from({ length: stops }).map((_, i) => {
          if (innerTicksOnly && (i === 0 || i === stops - 1)) return null;
          // Ticks are painted in `text-bright` — the same hue as the
          // unfilled track, so on the empty (`off`) side they melt
          // into the line and don't appear as "floating dots on
          // nothing". On the filled blue range they pop as bright
          // notches that read as discrete stops — which is when the
          // user actually needs the granularity hint.
          return (
          <span
            key={i}
            // Each tick sits at the same x as the thumb-center for that
            // step. `calc(ratio * (100% - 14px) + 7px)` mirrors Radix's
            // own thumb-position math (14px thumb, half-width 7).
            // `translate(-50%, -50%)` then pulls the tick's own centre
            // onto that point. `pointer-events-none` keeps the track
            // click area uninterrupted.
            className={cn(
              "pointer-events-none absolute top-1/2 size-[6px] rounded-full",
              "-translate-x-1/2 -translate-y-1/2",
              "bg-text-bright",
            )}
            style={{ left: `calc(${i / (stops - 1)} * (100% - 14px) + 7px)` }}
            aria-hidden="true"
          />
        );
        })
      : null}
    {/* Start / end icons replace the leftmost / rightmost tick. They
        sit at the same cx coordinate as the thumb at its min / max
        position, so when the thumb travels to either end it lines up
        on top of the icon. `pointer-events-auto` overrides Radix's
        `touch-none` on the Root so the icon stays clickable (e.g. as
        a jump-to-min / jump-to-max shortcut). */}
    {startIcon ? (
      <div
        className="pointer-events-auto absolute top-1/2 -translate-x-1/2 -translate-y-1/2"
        style={{ left: "7px" }}
      >
        {startIcon}
      </div>
    ) : null}
    {endIcon ? (
      <div
        className="pointer-events-auto absolute top-1/2 -translate-x-1/2 -translate-y-1/2"
        style={{ left: "calc(100% - 7px)" }}
      >
        {endIcon}
      </div>
    ) : null}
    <SliderPrimitive.Thumb
      className={cn(
        "relative block size-[14px] rounded-full bg-[var(--accent-blue)]",
        "border-2 border-[var(--bg-tertiary)]",
        "shadow-[0_1px_2px_rgba(0,0,0,0.15)]",
        "transition-transform duration-150 ease-out",
        "hover:scale-110",
        "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent-blue)]/40",
        "disabled:pointer-events-none disabled:opacity-50",
      )}
    />
  </SliderPrimitive.Root>
  );
});
Slider.displayName = SliderPrimitive.Root.displayName;

export { Slider };
