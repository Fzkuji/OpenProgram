/**
 * Effort pill — the trigger IS the picker.
 *
 * Collapsed: a 32px round chip showing just the biceps icon, tinted by
 * the current effort level. Hovering slides out a right-caret (the same
 * gesture as the neighbouring tool chips' ×). CLICK to expand — hover
 * alone never opens the card.
 *
 * Expanded: a Claude-style floating card ABOVE the trigger (grammar C —
 * white `--surface-popover` panel, radius 12, `--shadow-popover`, 16px
 * padding): header row = muted "Effort" + bright current level + (?)
 * HoverTip; body = Faster / Smarter end labels over the dotted-track
 * slider. Mouse-leave on the host collapses (the shell's 8px
 * padding-bottom bridges the visual gap so the pointer never "exits"
 * on the way up).
 *
 * Layout: the pill is wrapped in a ``position: relative`` host. The
 * shell is absolute so neither state resizes the row. Collapsed widths
 * (32 / 48 hover) and the expanded card geometry live in chat.css on
 * `.effort-pill-shell`.
 *
 * Extracted from composer/index.tsx to keep that file under the
 * project's no-1000-line-files rule.
 */
"use client";

import React, { useRef, useState } from "react";

import { Slider } from "@/components/ui/slider";
import { UltraRain } from "./ultra-rain";
import { HoverTip } from "@/components/ui/tooltip";
import { useTranslation } from "@/lib/i18n";
import {
  type AnimatedNavIconHandle,
  BicepsFlexedIcon,
  ChevronRightIcon,
  CircleHelpIcon,
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
  /** 卡片真实开合的回传（内部 state 才是权威）——composer 用它给
   *  effortText 触发钮标 aria-expanded，HoverTip 靠这个在卡开着时
   *  不冒黑提示。 */
  onExpandedChange?: (v: boolean) => void;
}

export const ThinkingEffortPill = React.forwardRef<
  HTMLDivElement,
  ThinkingEffortPillProps
>(function ThinkingEffortPill(
  { expanded, onToggle, options, value, onChange, onExpandedChange, ...rest },
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
      onExpandedChange={onExpandedChange}
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
    onExpandedChange,
    ...rest
  },
  ref,
) {
  // Click-to-open, leave-to-close. The `expanded` / `onToggle` props
  // from the parent are intentionally ignored: the pill manages its own
  // state — click on the collapsed pill opens the card, mouseleave on
  // the relative wrapper collapses it. Hover alone only slides out the
  // caret (chips-style affordance) and animates the icon.
  const [expanded, setExpandedState] = useState(false);
  // 内部开合是权威状态；同步回传给 composer（effortText 的
  // aria-expanded 由它驱动，卡开着时 HoverTip 不冒提示）。
  const setExpanded = (v: boolean) => {
    setExpandedState(v);
    onExpandedChange?.(v);
  };
  const { text } = useTranslation();
  const valueIndex = Math.max(
    0,
    options.findIndex((o) => o.value === value),
  );
  const maxIndex = Math.max(0, options.length - 1);
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

  // The collapsed chip's icons are pqoqubbw animated icons, driven from
  // the pill host's hover — same controlled-ref pattern as the sidebar
  // nav rows.
  const effortIconChipRef = useRef<AnimatedNavIconHandle>(null);
  const caretRef = useRef<AnimatedNavIconHandle>(null);

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
        // 点选档位时 radix 滑块会 setPointerCapture，浏览器随即给宿主
        // 补发一次 mouseleave（指针其实没离开）——按着键（buttons≠0）
        // 的"离开"一律忽略，否则每次选择卡片都会消失。
        if (e.buttons !== 0) return;
        setExpanded(false);
        effortIconChipRef.current?.stopAnimation?.();
        caretRef.current?.stopAnimation?.();
      }}
    >
      {/* Shell. Collapsed = the 32px round chip (48px on hover — widths
          in chat.css). Expanded = repositioned by chat.css into a
          floating layer above the trigger; the visible surface is the
          .effort-card inside. */}
      <div
        data-expanded={expanded ? "true" : undefined}
        className={[
          "effort-pill-shell absolute select-none",
          expanded
            ? "" // floating-card geometry comes from chat.css
            : "left-0 top-0 h-[32px] overflow-hidden rounded-full text-[14px] text-text-primary transition-[width,background-color] duration-[220ms] ease-out",
        ].join(" ")}
        style={{
          // Tint the collapsed pill by current effort level (neutral
          // white-grey at `off`, ramps to soft red at `xhigh`). The
          // expanded card paints its own popover surface instead.
          ...(expanded ? {} : { backgroundColor: collapsedTint }),
          // CSS variables inherited by the slider inside:
          //   --slider-active        →  range / ticks / focus ring
          //                              (soft, ~70% alpha)
          //   --slider-active-solid  →  thumb dot
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
        {expanded && (
          /* Claude 实测规格：卡 220×101、衬 10、圆角 12；标题 13px；
             标题→标签 16、标签→轨 10。header（muted 维度 + bright 值
             + help）、Faster/Smarter 两端标签、点刻度滑轨。 */
          <div
            className={`effort-card ${value === "max" ? "effort-ultra" : ""} rounded-[12px] border border-[var(--border-popover)] bg-[var(--surface-popover)] p-[10px] shadow-(--shadow-popover)`}
          >
            <div className="flex items-center gap-[6px] text-[13px] leading-[18px]">
              <span className="text-text-muted">{text("Effort", "思考力度")}</span>
              {/* 最高档：紫色标识 + 滑轨紫色马赛克（Claude Ultracode 形制）。 */}
              <span
                className="font-medium text-text-bright"
                style={value === "max" ? { color: "#8E6BD9" } : undefined}
              >
                {capEffort(value)}
              </span>
              <HoverTip
                label={text(
                  "Higher effort lets the model think longer before answering.",
                  "力度越高，模型回答前思考越久。",
                )}
              >
                <span className="ml-auto inline-flex cursor-default text-text-muted">
                  <CircleHelpIcon size={16} aria-hidden="true" />
                </span>
              </HoverTip>
            </div>
            <div className="mt-[16px] flex items-center justify-between text-[12px] leading-[15px] text-text-muted">
              <span>{text("Faster", "更快")}</span>
              <span>{text("Smarter", "更强")}</span>
            </div>
            <div className="mt-[10px] h-[20px]">
              <Slider
                min={0}
                max={maxIndex}
                step={1}
                stops={options.length}
                value={[valueIndex]}
                // 最高档：滑过区叠紫色像素矩阵动画（入场辐射 + 逐格随机
                // 闪烁）。canvas 每次进入 max 时重新挂载 → 重播入场。
                rangeChildren={value === "max" ? <UltraRain key="ultra" /> : null}
                onValueChange={(v) => {
                  const idx = v[0] ?? 0;
                  const next = options[idx];
                  if (next) onChange(next.value);
                }}
                onClick={(e) => e.stopPropagation()}
                thumb={
                  // Claude 实测：16×20 白色圆角矩形钮（radius 6），
                  // 与轨同高；档位不给钮上色。
                  <span
                    aria-hidden="true"
                    className="absolute left-1/2 top-1/2 h-[20px] w-[16px] -translate-x-1/2 -translate-y-1/2 rounded-[6px] bg-white pointer-events-none shadow-[0_1px_3px_rgba(10,10,10,0.2)]"
                  />
                }
              />
            </div>
          </div>
        )}
      </div>
    </div>
  );
});
