"use client";

import React, { useEffect, useId, useRef, useState } from "react";
import { CircleHelp } from "lucide-react";

import { EffortSlider } from "./effort-slider";
import { EffortLabel } from "./effort-label";
import type { ThinkingOption } from "./use-thinking-effort";
import { HoverTip, TipBody } from "@/components/ui/tooltip";
import { useTranslation } from "@/lib/i18n";
import { effortLevelColor, formatEffortLabel } from "@/lib/effort/effort-color";
import { GLASS_SURFACE } from "@/lib/glass";
import { captionAlignment, THUMB_WIDTH } from "@/lib/effort/effort-matrix";
import { type AnimatedNavIconHandle, GaugeIcon } from "@/components/animated-icons";
import { SolarIcon } from "@/components/solar-icons";

// Extends div attributes so a tooltip trigger (HoverTip / Radix
// `asChild`) can inject its hover/focus handlers — they must reach the
// host DOM node or the tip never opens.
interface ThinkingEffortPillProps
  extends Omit<React.HTMLAttributes<HTMLDivElement>, "onChange" | "onToggle"> {
  expanded: boolean;
  onToggle: () => void;
  options: ThinkingOption[];
  value: string;
  onChange: (v: string) => void;
  fastEnabled: boolean;
  fastSupported: boolean;
  fastHint: string;
  toggleFast: () => void;
}

export const ThinkingEffortPill = React.forwardRef<
  HTMLDivElement,
  ThinkingEffortPillProps
>(function ThinkingEffortPill(
  { expanded, onToggle, options, value, onChange, ...rest },
  ref,
) {
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
    expanded,
    onToggle,
    fastEnabled,
    fastSupported,
    fastHint,
    toggleFast,
    ...rest
  },
  ref,
) {
  const { text } = useTranslation();
  const fastHintId = useId();
  const optionKey = options.map(option => option.value).join("|");
  const [preview, setPreview] = useState<{ index: number; options: string; original: string } | null>(null);
  const previewValid = expanded && preview?.options === optionKey && preview.original === value;
  const valueIndex = previewValid ? preview.index : Math.max(0, options.findIndex(option => option.value === value));
  const shownValue = options[valueIndex]?.value ?? value;
  const maxIndex = Math.max(0, options.length - 1);
  const recommendedIndex = options.findIndex((o) => o.recommended);
  const warmHue = effortLevelColor(options, value);

  // Effort-level tint for the COLLAPSED pill — `warmHue` at low
  // opacity so it sits softly on the panel surface. `off` is special:
  // a neutral grey chip with no hue. It uses the solid theme-aware
  // `--effort-off-bg` token (dark #535350 / light #e8e6dc) — a flat
  // colour, no transparency.
  const collapsedTint =
    value === "off"
      ? "var(--effort-off-bg)"
      : `color-mix(in srgb, ${warmHue} 16%, transparent)`;

  // Active hue for the slider's focus ring — `warmHue` at ~70% so it
  // still reads as a soft fill. Passed down via the `--slider-active`
  // CSS custom property.
  const activeColor = `color-mix(in srgb, ${warmHue} 72%, transparent)`;

  // Fully-opaque variant for the effort icon. It stays visually
  // distinct from the half-alpha `--slider-active` track color.
  const activeColorSolid = warmHue;

  // The collapsed chip's icons are pqoqubbw animated icons, driven from
  // the pill host's hover — same controlled-ref pattern as the sidebar
  // nav rows.
  const effortIconChipRef = useRef<AnimatedNavIconHandle>(null);
  const caretRef = useRef<AnimatedNavIconHandle>(null);

  const atHighest = options.length > 1 && valueIndex === maxIndex;
  useEffect(() => { if (!expanded) setPreview(null); }, [expanded]);

  return (
    <div
      ref={ref}
      {...rest}
      className="effort-pill-host relative inline-flex h-(--ui-button-h-lg) items-center"
      data-effort-expanded={expanded ? "true" : undefined}
      onMouseEnter={(e) => {
        onMouseEnter?.(e);
        effortIconChipRef.current?.startAnimation?.();
        caretRef.current?.startAnimation?.();
      }}
      onMouseLeave={(e) => {
        onMouseLeave?.(e);
        // 鼠标移开不再收起卡片——和其它弹层一致，只有点击卡片外部才关
        // （outside-click 由 composer 的 pointerdown 侦听负责）。这里只
        // 停掉折叠 chip 的图标 / caret 悬停动画。
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
            : "left-0 top-0 h-(--ui-button-h-lg) overflow-hidden rounded-md text-fs-base text-text-primary transition-[width,background-color] duration-[220ms] ease-out",
        ].join(" ")}
        style={{
          // Tint the collapsed pill by current effort level (neutral
          // white-grey at `off`, ramps to soft red at `xhigh`). The
          // expanded card paints its own popover surface instead.
          ...(expanded ? {} : { backgroundColor: collapsedTint }),
          // CSS variables inherited by the slider inside:
          //   --slider-active        →  focus ring (soft, ~70% alpha)
          //   --slider-active-solid  →  collapsed chip glyph
          //                              (opaque, full-strength hue)
          ["--slider-active" as string]: activeColor,
          ["--slider-active-solid" as string]: activeColorSolid,
        } as React.CSSProperties}
      >
        <div
          className={[
            "effort-pill-collapsed h-full flex items-center px-[8px] cursor-pointer",
            expanded ? "hidden" : "",
          ].join(" ")}
          onClick={onToggle}
        >
          <SolarIcon
            name="fire"
            ref={effortIconChipRef}
            size={16}
            fit="fill"
            className="effort-pill-compact-icon text-[var(--slider-active-solid)]"
            aria-hidden="true"
          />
          <span className="effort-pill-caret text-[var(--slider-active-solid)]">
            <SolarIcon ref={caretRef} name="alt-arrow-right" size={12} motionPreset="nudge" />
          </span>
        </div>
        {expanded && (
          /* 卡片内容自上而下：标题行（muted 维度 + 紫色当前档 + Fast 仪表
             + ? 帮助）、Faster/Smarter 两端标签、刻度滑轨、"推荐"标注。
             画框 = GLASS_SURFACE，与所有弹层同一画框。
             标题 13px；标题→标签 10、标签→轨 10、轨→标注 6。 */
          <div
            className={`effort-card ${atHighest ? "effort-ultra" : ""} ${GLASS_SURFACE}`}
          >
            <div className="effort-heading">
              <span className="text-text-muted">{text("Effort", "思考力度")}</span>
              <EffortLabel label={formatEffortLabel(shownValue)} index={valueIndex} accent={atHighest} />
              <HoverTip label={fastHint}>
                <button type="button"
                  className="effort-fast-toggle ml-auto"
                  style={{ background: "transparent", border: "none", color: fastEnabled ? "var(--accent-red)" : undefined }}
                  aria-label={text("Fast mode", "高速模式")}
                  aria-describedby={fastHintId}
                  aria-pressed={fastEnabled} aria-disabled={!fastSupported}
                  onClick={(e) => { e.stopPropagation(); if (fastSupported) toggleFast(); }}>
                  <GaugeIcon size={16} active={fastEnabled} />
                  <span id={fastHintId} className="sr-only">{fastHint}</span>
                </button>
              </HoverTip>
              <HoverTip
                label={
                  <TipBody
                    title={text("Thinking effort", "思考力度")}
                    detail={text(
                      "How long the model reasons before it answers. Higher levels think longer and use more tokens; lower levels answer faster. Recommended marks this model's default.",
                      "模型回答前推理多久。档位越高思考越久、消耗越多 token；越低回答越快。“推荐”标出该模型的默认档位。",
                    )}
                  />
                }
              >
                <button
                  type="button"
                  className="effort-help"
                  aria-label={text("About thinking effort", "关于思考力度")}
                  onClick={(e) => e.stopPropagation()}
                >
                  <CircleHelp size={14} aria-hidden="true" />
                </button>
              </HoverTip>
            </div>
            {options.length > 1 && <>
            <div className="effort-end-labels">
              <span>{text("Faster", "更快")}</span>
              <span>{text("Smarter", "更强")}</span>
            </div>
            <div className="effort-track">
              <EffortSlider key={optionKey} options={options} index={valueIndex}
                label={text("Thinking effort", "思考力度")}
                onPreview={index => setPreview(index === null ? null : { index, options: optionKey, original: value })}
                onCommit={index => { const next = options[index]; if (next) onChange(next.value); setPreview(null); }} />
            </div>
            {recommendedIndex >= 0 && (
              <div className="effort-recommended-row">
                <span
                  className="effort-recommended"
                  data-align={captionAlignment(recommendedIndex, options.length)}
                  style={
                    captionAlignment(recommendedIndex, options.length) === "center"
                      ? { left: `calc(${recommendedIndex / maxIndex} * (100% - ${THUMB_WIDTH}px) + ${THUMB_WIDTH / 2}px)` }
                      : undefined
                  }
                >
                  {text("Recommended", "推荐")}
                </span>
              </div>
            )}
            </>}
          </div>
        )}
      </div>
    </div>
  );
});
