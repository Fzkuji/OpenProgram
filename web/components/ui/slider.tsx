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
  /** Skip drawing the first and last tick. Use this when something
      outside the slider (e.g. an icon on each end) already marks
      the min/max stops. */
  innerTicksOnly?: boolean;
};

const Slider = React.forwardRef<
  React.ElementRef<typeof SliderPrimitive.Root>,
  SliderProps
>(({ className, stops, innerTicksOnly, ...props }, ref) => {
  // Read the current step value so each tick can colour itself to
  // match the segment it sits on (blue under the filled range, white
  // on the unfilled track). For discrete sliders we use the `[idx]`
  // shape; fall back to defaultValue / 0 just in case.
  const currentValue = Array.isArray(props.value)
    ? props.value[0] ?? 0
    : Array.isArray(props.defaultValue)
      ? props.defaultValue[0] ?? 0
      : 0;

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
          // Ticks colour-match the segment they live on:
          //   i  <  currentValue  →  under the blue range, paint blue.
          //   i  >= currentValue  →  on the white track, paint bright.
          // The tick at i === currentValue is covered by the thumb;
          // grouping it with the unfilled side keeps the colour-flip
          // happen the instant the thumb passes a stop.
          const isFilled = i < currentValue;
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
              isFilled ? "bg-[var(--accent-blue)]" : "bg-text-bright",
            )}
            style={{ left: `calc(${i / (stops - 1)} * (100% - 14px) + 7px)` }}
            aria-hidden="true"
          />
        );
        })
      : null}
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
