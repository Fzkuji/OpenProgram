/**
 * Effort pill — the trigger IS the picker.
 *
 * Collapsed: a pill that reads `effort: medium ⌄`, sized to its content.
 * Click to expand.
 *
 * Expanded: the same pill animates its width out and the caret/text
 * content swaps for `{value}` + an inline ``<Slider />``. Dragging the
 * slider only updates the value — it doesn't collapse. Closes when the
 * user clicks anywhere outside the composer wrapper (the document-level
 * click-outside handler in the composer flips ``expanded`` back to
 * ``false``).
 *
 * Layout: the pill is wrapped in a ``position: relative`` host that
 * keeps the collapsed footprint reserved in the bottom-row flex flow.
 * The visible pill is ``position: absolute`` on top of that footprint,
 * so when it expands it floats over the rest of the row without
 * shoving the context badge / other controls aside.
 *
 * Extracted from composer/index.tsx to keep that file under the
 * project's no-1000-line-files rule.
 */
"use client";

import React, {
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
} from "react";

import { Slider } from "@/components/ui/slider";
import { Lightning } from "@phosphor-icons/react/dist/ssr";

import { CaretIcon } from "../icons";

const capEffort = (s: string) => (s ? s[0].toUpperCase() + s.slice(1) : s);

interface ThinkingEffortPillProps {
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
  { expanded, onToggle, options, value, onChange },
  ref,
) {
  // No options → provider/model exposes no thinking knob (e.g. gpt-4o,
  // or a model whose picker is hidden): render nothing at all.
  if (options.length === 0) return null;

  // Exactly one option → the effort is fixed (e.g. claude-code, where
  // the proxy ignores reasoning_effort and the value is always
  // "auto"). Show it as a static label — no caret, no expand, no
  // slider, not clickable. A dropdown with a single choice is dead UI.
  if (options.length === 1) {
    return (
      <div
        ref={ref}
        className="inline-flex h-[32px] items-center rounded-full pl-[14px] pr-[14px] text-[14px] text-text-primary select-none whitespace-nowrap"
        style={{ backgroundColor: "var(--effort-off-bg)" }}
      >
        <span>Effort: {capEffort(options[0].value)}</span>
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
    />
  );
});

const ThinkingEffortSliderPill = React.forwardRef<
  HTMLDivElement,
  ThinkingEffortPillProps
>(function ThinkingEffortSliderPill(
  { options, value, onChange },
  ref,
) {
  // Hover-driven open/close — no click required. The `expanded` /
  // `onToggle` props from the parent are intentionally ignored: the
  // pill manages its own state via mouseenter / mouseleave on the
  // relative wrapper (so leaving the spacer footprint also collapses).
  const [expanded, setExpanded] = useState(false);
  const valueIndex = Math.max(
    0,
    options.findIndex((o) => o.value === value),
  );
  const maxIndex = Math.max(0, options.length - 1);
  // Lightning size scales linearly with effort: from 10px at the
  // `off` end to 18px at `xhigh`. A single bolt rides on the thumb
  // — its position tells "where on the scale" and its size tells
  // "how much effort" at a glance.
  const lightningSize =
    maxIndex > 0
      ? Math.round(10 + (valueIndex / maxIndex) * 8)
      : 10;
  // Warm hue per effort level. NOT the project `--accent-*` tokens —
  // those are deliberately muted/earthy (`--accent-orange` is a
  // brownish #b8651f, `--accent-yellow` reads as dirt-yellow), which
  // looked drab in the slider. Plain vivid hex hues, used as-is (no
  // white mixing). `off` keeps neutral bright-white. Everything
  // below derives from this single hue so the collapsed tint /
  // range / bolt all agree.
  const warmHue =
    {
      off: "var(--text-bright)",
      minimal: "#fbbf24",
      low: "#fbbf24",
      medium: "#ff9d2e",
      high: "#ff9d2e",
      xhigh: "#ff5c5c",
    }[value] ?? "var(--text-bright)";

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

  // Fully-opaque variant for the Lightning bolt itself — it floats
  // above the ring as a standalone glyph and would look faded if it
  // inherited the half-alpha `--slider-active`.
  const activeColorSolid = warmHue;

  // Measure the spacer so the collapsed pill width exactly matches
  // its content. Hard-coding 132px gave the same chip the same
  // footprint regardless of label text — `effort: xhigh` left a
  // ~30px trailing gap. Re-measures whenever the value changes.
  const spacerRef = useRef<HTMLSpanElement>(null);
  const [collapsedWidth, setCollapsedWidth] = useState<number>(120);
  // `measured` gates the width transition. On first mount the pill
  // renders at the 120px placeholder (also what SSR ships), then the
  // layout effect below corrects it to the real measured width. If
  // the transition were live, that 120 → real correction would
  // visibly "bounce" on every page load.
  //
  // Critically, `measured` must flip to true in a LATER render than
  // the width correction — if both happen in the same render the
  // transition class lands at the same moment the width changes and
  // the browser still animates from the SSR-painted 120px. So:
  //   useLayoutEffect → correct width (transition still off)
  //   rAF in useEffect → enable transition one frame later
  const [measured, setMeasured] = useState(false);
  useLayoutEffect(() => {
    if (spacerRef.current) {
      setCollapsedWidth(spacerRef.current.offsetWidth);
    }
  }, [value]);
  useEffect(() => {
    const id = requestAnimationFrame(() => setMeasured(true));
    return () => cancelAnimationFrame(id);
  }, []);

  return (
    <div
      ref={ref}
      className="relative inline-flex h-[32px] items-center"
      data-effort-expanded={expanded ? "true" : undefined}
      onMouseEnter={() => setExpanded(true)}
      onMouseLeave={() => setExpanded(false)}
    >
      {/* Invisible spacer keeps the collapsed pill's footprint in the
          flex layout so expanding doesn't push the context badge or
          other controls. Mirrors the collapsed pill content exactly. */}
      <span
        ref={spacerRef}
        aria-hidden="true"
        // `whitespace-nowrap` + `shrink-0` keep the spacer measuring
        // its FULL single-line content width even when the parent
        // flex row would otherwise compress it (which would wrap the
        // text and make the spacer report a too-narrow offsetWidth,
        // dragging the pill down with it).
        className="invisible inline-flex shrink-0 items-center gap-[5px] pl-[14px] pr-[10px] text-[14px] whitespace-nowrap"
      >
        <span>Effort: {capEffort(value)}</span>
        <CaretIcon />
      </span>

      {/* Visible pill. Only the WIDTH and the background colour
          animate — the two content layers below switch via
          `display: none` rather than opacity, so there's no
          fade-in/fade-out crossfade (the user explicitly wanted
          this gone). The slider lives in a fixed-260px-wide layer,
          so as the pill expands the slider's internal layout never
          recalculates — `overflow: hidden` on the pill just reveals
          progressively more of the same stable layer.

          Pill widths: 132px collapsed → 260px expanded (narrower
          than the previous 340 since the slider track + icons
          read fine in a tighter footprint). */}
      <div
        className={[
          "absolute left-0 top-0 h-[32px] overflow-hidden",
          "rounded-full select-none",
          "text-[14px]",
          // Width transition is gated on `measured` so the first-mount
          // 120px → real-width correction doesn't animate (no bounce
          // on page refresh). Background colour can always transition.
          measured
            ? "transition-[width,background-color] duration-[220ms] ease-out"
            : "transition-[background-color] duration-[220ms] ease-out",
          expanded ? "bg-bg-hover text-text-bright" : "text-text-primary",
        ].join(" ")}
        style={{
          width: expanded ? 260 : collapsedWidth,
          // Tint the collapsed pill by current effort level (neutral
          // white-grey at `off`, ramps to soft red at `xhigh`). When
          // expanded we hand the bg back to the Tailwind class
          // (`bg-bg-hover`) above and skip the inline override.
          ...(expanded ? {} : { backgroundColor: collapsedTint }),
          // CSS variables inherited by the slider inside:
          //   --slider-active        →  range / ticks / focus ring
          //                              (soft, ~70% alpha)
          //   --slider-active-solid  →  Lightning bolt thumb
          //                              (opaque, full-strength hue)
          ["--slider-active" as string]: activeColor,
          ["--slider-active-solid" as string]: activeColorSolid,
        } as React.CSSProperties}
      >
        {/* Collapsed content. `hidden` (display: none) when expanded
            so there's no overlap / fade — instant swap on toggle.
            `whitespace-nowrap` keeps the label on a single line at
            the same width the spacer measured. */}
        <div
          className={[
            "h-full flex items-center gap-[5px] pl-[14px] pr-[10px] whitespace-nowrap",
            expanded ? "hidden" : "",
          ].join(" ")}
        >
          <span>Effort: {capEffort(value)}</span>
          <CaretIcon />
        </div>

        {/* Expanded content. Fixed 260px wide so the slider track +
            tick math don't recompute mid-transition. Hidden via
            display:none when collapsed. */}
        <div
          className={[
            "h-full flex items-center gap-[10px] px-[12px]",
            !expanded ? "hidden" : "",
          ].join(" ")}
          style={{ width: 260 }}
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
            // Stop click from bubbling to the pill's onClick.
            onClick={(e) => e.stopPropagation()}
            // The thumb itself is a Lightning bolt that travels with
            // the value. Size scales from 10px (off) → 22px (xhigh)
            // so position tells you "where" and size tells you
            // "how much" simultaneously. Colour is the same effort
            // hue used by the filled range (via `--slider-active`).
            // `aria-hidden` on the icon since the slider Root
            // already announces value/min/max.
            thumb={
              <>
                {/* Hollow-looking ring around the bolt:
                    - Interior is painted in `bg-bg-hover` (the
                      expanded pill's own background), so it appears
                      "transparent" against the surrounding pill and
                      visually cuts the slider track + any tick at
                      the thumb's position.
                    - The 1px border in soft `text-bright` gives the
                      shape a visible outline so it reads as a ring,
                      not just an invisible mask.
                    Sized 4px wider than the bolt so the cut feels
                    generous and the ring frames the glyph cleanly. */}
                <span
                  aria-hidden="true"
                  // Ring scales with the bolt — always `lightningSize
                  // + 8`, so as effort climbs both the bolt and its
                  // frame grow together (the bolt still sits proudly
                  // inside the ring rather than bursting through it).
                  // Size animates with the same 150ms easing as the
                  // bolt's `transition-[width,height]`.
                  className="absolute left-1/2 top-1/2 -translate-x-1/2 -translate-y-1/2 rounded-full bg-bg-hover border-[3px] border-[color-mix(in_srgb,var(--text-bright)_40%,transparent)] transition-[width,height] duration-150 ease-out"
                  style={{
                    width: lightningSize + 8,
                    height: lightningSize + 8,
                  }}
                />
                <Lightning
                  size={lightningSize}
                  weight="fill"
                  className="absolute left-1/2 top-1/2 -translate-x-1/2 -translate-y-1/2 text-[var(--slider-active-solid)] pointer-events-none transition-[width,height] duration-150 ease-out"
                  aria-hidden="true"
                />
              </>
            }
            className="flex-1"
          />
        </div>
      </div>
    </div>
  );
});
