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
  /** Content rendered INSIDE the thumb element. When provided, the
      thumb's default round bg/border is dropped — the child takes
      over the visual (e.g. an icon that travels with the thumb).
      Thumb keeps its 14px hit-area for click/drag. */
  thumb?: React.ReactNode;
};

const Slider = React.forwardRef<
  React.ElementRef<typeof SliderPrimitive.Root>,
  SliderProps
>(({ className, stops, innerTicksOnly, startIcon, endIcon, thumb, ...props }, ref) => {
  // Read current step value so each tick (and downstream coloured
  // elements) can know if it sits in the filled half or the unfilled
  // half. Filled = i < currentValue → blue; otherwise grey.
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
    {/* Claude 效果器滑轨（实测）：轨 20px 高、6px 方圆角、暖灰面
        （#EDECE8 → 用 6% ink mix 适配双主题）；已滑过左段是 10% 黑的
        方形覆盖层。 */}
    <SliderPrimitive.Track className="relative h-[20px] w-full grow overflow-hidden rounded-[6px] bg-[color-mix(in_srgb,var(--text-bright)_6%,transparent)]">
      <SliderPrimitive.Range className="absolute h-full bg-[color-mix(in_srgb,var(--text-bright)_10%,transparent)]" />
    </SliderPrimitive.Track>
    {stops && stops > 1
      ? Array.from({ length: stops }).map((_, i) => {
          if (innerTicksOnly && (i === 0 || i === stops - 1)) return null;
          // No tick under the thumb itself — it would poke out from
          // behind the thumb's glyph.
          if (i === currentValue) return null;
          // Selected = the tick's index is BELOW the current thumb
          // position (i.e. the thumb has already passed it on the
          // way right). Selected ticks paint accent-blue and melt
          // into the filled range; unselected ticks paint
          // text-muted and melt into the grey track.
          // Claude：轨内圆点统一中性灰、不随滑过变色；唯独最右一档
          // （最强）是蓝紫点。
          const isMax = i === stops - 1;
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
              "pointer-events-none absolute top-1/2 size-[4px] rounded-full",
              "-translate-x-1/2 -translate-y-1/2",
              isMax
                ? "bg-[#7b7cf7]"
                : "bg-[color-mix(in_srgb,var(--text-bright)_22%,transparent)]",
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
        // Hit area stays 14px so Radix's thumb-center math (the
        // `100% - 14px + 7px` calc shared with ticks/icons) still
        // lines up. When a `thumb` child is provided it takes over
        // the visual — bg / border / focus ring are dropped so the
        // child renders unobstructed (otherwise the default focus
        // ring would draw a second concentric circle around the
        // custom thumb element). Without a child, fall back to the
        // default soft round bullet + standard focus ring.
        "relative block size-[14px] rounded-full",
        "transition-transform duration-150 ease-out",
        "outline-none",
        "disabled:pointer-events-none disabled:opacity-50",
        thumb
          ? "bg-transparent"
          : cn(
              "bg-[var(--slider-active)]",
              "border-2 border-[var(--bg-tertiary)]",
              "shadow-(--shadow-sm)",
              "hover:scale-110",
              "focus-visible:ring-2 focus-visible:ring-[var(--slider-active)]",
            ),
      )}
    >
      {thumb}
    </SliderPrimitive.Thumb>
  </SliderPrimitive.Root>
  );
});
Slider.displayName = SliderPrimitive.Root.displayName;

export { Slider };
