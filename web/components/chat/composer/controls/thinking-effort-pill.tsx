/**
 * Effort pill — the trigger IS the picker.
 *
 * Collapsed: a 32px round chip showing just the biceps icon, tinted by
 * the current effort level. Hovering slides out a right-caret (the same
 * gesture as the neighbouring tool chips' ×). CLICK to expand — hover
 * alone never opens the slider.
 *
 * Expanded: the same pill expands to the right into the current value
 * plus an inline slider. Mouse-leave collapses.
 *
 * Layout: the pill is wrapped in a ``position: relative`` host. The
 * visible pill is absolute so expanding it does not resize the row.
 * All widths (32 collapsed / 48 hovered / 260 expanded) live in
 * chat.css on `.effort-pill-shell`.
 *
 * Extracted from composer/index.tsx to keep that file under the
 * project's no-1000-line-files rule.
 */
"use client";

import React, { useEffect, useRef, useState } from "react";

import { Slider } from "@/components/ui/slider";
import {
  type AnimatedNavIconHandle,
  BicepsFlexedIcon,
  ChevronRightIcon,
} from "@/components/animated-icons";

const capEffort = (s: string) => (s ? s[0].toUpperCase() + s.slice(1) : s);

// Extends div attributes so a tooltip trigger (HoverTip / Radix
// `asChild`) can inject its hover/focus handlers — they must reach the
// host DOM node or the tip never opens.
interface ThinkingEffortPillProps
  extends Omit<React.HTMLAttributes<HTMLDivElement>, "onChange" | "onToggle"> {
  expanded: boolean;
  onToggle: () => void;
  options: { value: string; desc?: string }[];
  value: string;
  onChange: (v: string) => void;
}

export const ThinkingEffortPill = React.forwardRef<
  HTMLDivElement,
  ThinkingEffortPillProps
>(function ThinkingEffortPill(
  { expanded, onToggle, options, value, onChange, ...rest },
  ref,
) {
  // No options → provider/model exposes no thinking knob (e.g. gpt-4o,
  // or a model whose picker is hidden): render nothing at all.
  if (options.length === 0) return null;

  // Exactly one option → the effort is fixed (e.g. claude-code, where
  // the proxy ignores reasoning_effort and the value is always
  // "auto"). Show the bare icon chip — no caret, no expand, no slider,
  // not clickable. A dropdown with a single choice is not useful.
  if (options.length === 1) {
    return (
      <div
        ref={ref}
        {...rest}
        className="effort-pill-fixed inline-flex h-[32px] w-[32px] items-center justify-center rounded-full text-text-primary select-none"
        style={{ backgroundColor: "var(--effort-off-bg)" }}
      >
        <BicepsFlexedIcon
          size={18}
          className="effort-pill-compact-icon text-text-primary"
          aria-hidden="true"
        />
      </div>
    );
  }

  // Two or more options → the interactive slider pill. Split into its
  // own component so the hooks below never sit behind the conditional
  // returns above (rules of hooks: the hook count must not change when
  // ``options.length`` flips as the user switches agents).
  return (
    <ThinkingEffortSliderPill
      ref={ref}
      expanded={expanded}
      onToggle={onToggle}
      options={options}
      value={value}
      onChange={onChange}
      {...rest}
    />
  );
});

const ThinkingEffortSliderPill = React.forwardRef<
  HTMLDivElement,
  ThinkingEffortPillProps
>(function ThinkingEffortSliderPill(
  {
    options,
    value,
    onChange,
    onMouseEnter,
    onMouseLeave,
    // `expanded` / `onToggle` from the parent are intentionally ignored
    // (hover-driven, see below) — destructured so they don't leak into
    // `rest` and onto the DOM node.
    expanded: _expanded,
    onToggle: _onToggle,
    ...rest
  },
  ref,
) {
  // Click-to-open, leave-to-close. The `expanded` / `onToggle` props
  // from the parent are intentionally ignored: the pill manages its own
  // state — click on the collapsed pill opens the slider, mouseleave on
  // the relative wrapper collapses it. Hover alone only slides out the
  // caret (chips-style affordance) and animates the icon.
  const [expanded, setExpanded] = useState(false);
  const valueIndex = Math.max(
    0,
    options.findIndex((o) => o.value === value),
  );
  const maxIndex = Math.max(0, options.length - 1);
  // Effort icon size scales linearly with effort: from 14px at the
  // low end to 22px at `xhigh`. The icon is rendered at the slider
  // thumb; position and size both encode the current value. `off`
  // doesn't use it — its thumb is a plain dot, not the biceps.
  const effortIconSize =
    maxIndex > 0
      ? Math.round(14 + (valueIndex / maxIndex) * 8)
      : 14;
  // Warm hue per effort level, interpolated continuously so EVERY level
  // gets its own colour: hsl hue runs 48° (yellow, lowest) → 0° (red,
  // highest) across the non-off stops, with saturation/lightness easing
  // up alongside. Endpoints match the old fixed palette (#fbbf24 …
  // #ff5c5c). NOT the project `--accent-*` tokens — those are muted /
  // earthy and looked drab in the slider. `off` keeps neutral
  // bright-white. Everything below derives from this single hue so the
  // collapsed tint / range / glyph all agree.
  const nonOff = options.filter((o) => o.value !== "off");
  const nonOffIdx = nonOff.findIndex((o) => o.value === value);
  const heat = nonOff.length > 1 ? nonOffIdx / (nonOff.length - 1) : 1;
  const warmHue =
    value === "off" || nonOffIdx < 0
      ? "var(--text-bright)"
      : `hsl(${Math.round(48 - 48 * heat)}, ${Math.round(96 + 4 * heat)}%, ${Math.round(56 + 12 * heat)}%)`;

  // Effort-level tint for the COLLAPSED pill — `warmHue` at low
  // opacity so it sits softly on the panel surface. `off` is special:
  // a neutral grey chip with no hue. It uses the solid theme-aware
  // `--effort-off-bg` token (dark #535350 / light #e8e6dc) — a flat
  // colour, no transparency.
  const collapsedTint =
    value === "off"
      ? "var(--effort-off-bg)"
      : `color-mix(in srgb, ${warmHue} 16%, transparent)`;

  // Active hue for the slider's filled elements (range bar, filled
  // tick dots, focus ring) — `warmHue` at ~70% so it still reads as
  // a soft fill against the grey track. Passed down via the
  // `--slider-active` CSS custom property.
  const activeColor = `color-mix(in srgb, ${warmHue} 72%, transparent)`;

  // Fully-opaque variant for the effort icon. It stays visually
  // distinct from the half-alpha `--slider-active` track color.
  const activeColorSolid = warmHue;

  // Effort icons (collapsed chip + slider thumb) are pqoqubbw animated
  // icons, driven from the pill host's hover — same controlled-ref
  // pattern as the sidebar nav rows.
  const effortIconChipRef = useRef<AnimatedNavIconHandle>(null);
  const effortIconThumbRef = useRef<AnimatedNavIconHandle>(null);
  const caretRef = useRef<AnimatedNavIconHandle>(null);

  // The thumb biceps flexes for as long as the slider is open. It can't
  // be driven from mouseenter like the chip icon: picking `off` unmounts
  // it, and the fresh icon mounted on the next value change would sit
  // frozen until the next re-hover. Re-keying on [expanded, value]
  // restarts the loop after every such remount.
  useEffect(() => {
    if (expanded && value !== "off") {
      effortIconThumbRef.current?.startAnimation?.();
    }
  }, [expanded, value]);

  return (
    <div
      ref={ref}
      {...rest}
      className="effort-pill-host relative inline-flex h-[32px] items-center"
      data-effort-expanded={expanded ? "true" : undefined}
      onMouseEnter={(e) => {
        onMouseEnter?.(e);
        effortIconChipRef.current?.startAnimation?.();
        caretRef.current?.startAnimation?.();
      }}
      onMouseLeave={(e) => {
        onMouseLeave?.(e);
        setExpanded(false);
        effortIconChipRef.current?.stopAnimation?.();
        effortIconThumbRef.current?.stopAnimation?.();
        caretRef.current?.stopAnimation?.();
      }}
    >
      {/* Visible pill. It stays in the bottom row and expands to the
          right. Widths (32 / 48 hover / 260 expanded) come from
          chat.css so collapsed always matches the neighbouring round
          controls. */}
      <div
        data-expanded={expanded ? "true" : undefined}
        className={[
          "effort-pill-shell absolute left-0 top-0 h-[32px] overflow-hidden",
          "rounded-full select-none",
          "text-[14px]",
          "transition-[width,background-color] duration-[220ms] ease-out",
          expanded ? "bg-bg-hover text-text-bright" : "text-text-primary",
        ].join(" ")}
        style={{
          // Tint the collapsed pill by current effort level (neutral
          // white-grey at `off`, ramps to soft red at `xhigh`). When
          // expanded we hand the bg back to the Tailwind class
          // (`bg-bg-hover`) above and skip the inline override.
          ...(expanded ? {} : { backgroundColor: collapsedTint }),
          // CSS variables inherited by the slider inside:
          //   --slider-active        →  range / ticks / focus ring
          //                              (soft, ~70% alpha)
          //   --slider-active-solid  →  effort-icon thumb
          //                              (opaque, full-strength hue)
          ["--slider-active" as string]: activeColor,
          ["--slider-active-solid" as string]: activeColorSolid,
        } as React.CSSProperties}
      >
        <div
          className={[
            "effort-pill-collapsed h-full flex items-center px-[7px] cursor-pointer",
            expanded ? "hidden" : "",
          ].join(" ")}
          onClick={() => setExpanded(true)}
        >
          <BicepsFlexedIcon
            ref={effortIconChipRef}
            size={18}
            className="effort-pill-compact-icon text-[var(--slider-active-solid)]"
            aria-hidden="true"
          />
          <span className="effort-pill-caret text-[var(--slider-active-solid)]">
            <ChevronRightIcon ref={caretRef} size={12} />
          </span>
        </div>
        <div
          className={[
            "effort-pill-expanded h-full flex items-center gap-[10px] px-[12px]",
            !expanded ? "hidden" : "",
          ].join(" ")}
        >
          <span className="shrink-0 min-w-[56px] text-center text-text-primary">
            {capEffort(value)}
          </span>
          <Slider
            min={0}
            max={maxIndex}
            step={1}
            stops={options.length}
            value={[valueIndex]}
            onValueChange={(v) => {
              const idx = v[0] ?? 0;
              const next = options[idx];
              if (next) onChange(next.value);
            }}
            onClick={(e) => e.stopPropagation()}
            thumb={
              value === "off" ? (
                // No effort → no muscle. A plain dot, slightly larger
                // than the track's tick dots, marks the off position.
                <span
                  aria-hidden="true"
                  className="absolute left-1/2 top-1/2 size-[10px] -translate-x-1/2 -translate-y-1/2 rounded-full bg-[var(--slider-active-solid)] pointer-events-none"
                />
              ) : (
                // Solid-filled glyph: the fill itself masks the track
                // behind the thumb (see .effort-pill-thumb-icon), so no
                // backing disc is needed.
                <BicepsFlexedIcon
                  ref={effortIconThumbRef}
                  size={effortIconSize}
                  className="effort-pill-thumb-icon absolute left-1/2 top-1/2 -translate-x-1/2 -translate-y-1/2 text-[var(--slider-active-solid)] pointer-events-none transition-[width,height] duration-150 ease-out"
                  aria-hidden="true"
                />
              )
            }
            className="flex-1"
          />
        </div>
      </div>
    </div>
  );
});
