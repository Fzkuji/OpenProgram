"use client";

/**
 * The annotation tools, colours and stroke widths, shared by the screenshot
 * overlay and the composer's image annotator. In the overlay the bar floats
 * by the selection and the colours and widths open in a second bar under it
 * (above it when the bar sits above the selection); in the annotator it is
 * one row along the top.
 *
 * Every control is the App's flat ghost icon button with a 16 px Solar
 * glyph, as in the composer; the chosen tool keeps the hover fill.
 */
import type { ReactNode } from "react";

import { SolarIcon } from "@/components/solar-icons";
import type { SolarIconName } from "@/components/solar-icons/bodies";
import { Button } from "@/components/ui/button";
import { HoverTip, TipBody } from "@/components/ui/tooltip";
import { COLORS, WIDTHS, type Tool } from "@/lib/annotate/marks";
import { useTranslation } from "@/lib/i18n";
import { cn } from "@/lib/utils";
import styles from "./annotate-toolbar.module.css";

/** The key that picks each tool, as Snow Shot's single letters. */
export const TOOL_KEYS: Record<string, Tool> = {
  r: "rect", e: "ellipse", a: "arrow", p: "pen", h: "highlighter", m: "mosaic", t: "text", n: "number",
};

/** A Solar glyph at the composer's 16 px. */
export function Glyph({ name }: { name: SolarIconName }) {
  return <SolarIcon name={name} size={16} />;
}

const ICONS: Record<Tool, SolarIconName> = {
  rect: "stop",
  ellipse: "circle",
  arrow: "arrow-right-up",
  pen: "pen",
  highlighter: "highlighter",
  mosaic: "grid-3x3",
  text: "text",
  number: "hashtag-circle",
};

export function useToolNames() {
  const { text } = useTranslation();
  return {
    rect: text("Rectangle", "矩形"),
    ellipse: text("Ellipse", "椭圆"),
    arrow: text("Arrow", "箭头"),
    pen: text("Pen", "画笔"),
    highlighter: text("Highlighter", "荧光笔"),
    mosaic: text("Mosaic", "马赛克"),
    text: text("Text", "文字"),
    number: text("Step number", "序号"),
  } satisfies Record<Tool, string>;
}

export function ActionButton({ label, detail, onClick, icon, disabled, active }: {
  label: string;
  detail?: string;
  onClick: () => void;
  icon: SolarIconName;
  disabled?: boolean;
  active?: boolean;
}) {
  return (
    <HoverTip label={<TipBody title={label} detail={detail} />}>
      <Button variant="ghost" size="icon-sm" aria-label={label}
        aria-pressed={active === undefined ? undefined : active} disabled={disabled}
        className={cn(styles.button, active && styles.active)} onClick={onClick}>
        <Glyph name={icon} />
      </Button>
    </HoverTip>
  );
}

export function AnnotateToolbar({
  tool, onToolChange, color, onColorChange, width, onWidthChange,
  canUndo, canRedo, onUndo, onRedo, actions, layout = "floating", styleAbove = false, className,
}: {
  tool: Tool | null;
  onToolChange: (tool: Tool | null) => void;
  color: string;
  onColorChange: (color: string) => void;
  width: number;
  onWidthChange: (width: number) => void;
  canUndo: boolean;
  canRedo: boolean;
  onUndo: () => void;
  onRedo: () => void;
  /** Buttons after the drawing tools: copy, save, send. */
  actions?: ReactNode;
  layout?: "floating" | "inline";
  /** Floating: the colours bar goes above the tools bar. */
  styleAbove?: boolean;
  className?: string;
}) {
  const { text } = useTranslation();
  const names = useToolNames();
  const keyOf = (value: Tool) => Object.entries(TOOL_KEYS).find(([, t]) => t === value)?.[0].toUpperCase();
  const mod = typeof navigator !== "undefined" && /Mac/.test(navigator.platform) ? "⌘" : "Ctrl+";

  const tools = (
    <div className={styles.group} role="group" aria-label={text("Drawing tool", "绘制工具")}>
      {(Object.keys(ICONS) as Tool[]).map((value) => (
        <ActionButton key={value} label={names[value]} detail={keyOf(value)} icon={ICONS[value]}
          active={tool === value} onClick={() => onToolChange(tool === value ? null : value)} />
      ))}
    </div>
  );

  const history = (
    <>
      <ActionButton label={text("Undo", "撤销")} detail={`${mod}Z`} disabled={!canUndo} onClick={onUndo}
        icon="undo-left-round" />
      <ActionButton label={text("Redo", "重做")} detail={`${mod}⇧Z`} disabled={!canRedo} onClick={onRedo}
        icon="undo-right-round" />
    </>
  );

  const style = (
    <div className={styles.style}>
      <div className={styles.colors} role="radiogroup" aria-label={text("Colour", "颜色")}>
        {COLORS.map((swatch) => (
          <button key={swatch} type="button" role="radio" aria-checked={color === swatch} aria-label={swatch}
            className={styles.swatch} style={{ background: swatch }} onClick={() => onColorChange(swatch)} />
        ))}
      </div>
      <span className={styles.divider} />
      <div className={styles.widths} role="radiogroup" aria-label={text("Stroke width", "粗细")}>
        {WIDTHS.map((value, index) => (
          <button key={value} type="button" role="radio" aria-checked={width === value}
            aria-label={[text("Thin", "细"), text("Medium", "中"), text("Thick", "粗")][index]}
            className={styles.width} onClick={() => onWidthChange(value)}>
            <i style={{ height: Math.max(2, value * 0.75) }} />
          </button>
        ))}
      </div>
    </div>
  );

  if (layout === "inline") {
    return (
      <div className={cn(styles.inline, className)}>
        {tools}
        {style}
        <div className={styles.group}>{history}</div>
        {actions}
      </div>
    );
  }

  const bar = (
    <div className={cn(styles.bar, styles.surface)}>
      {tools}
      <span className={styles.divider} />
      {history}
      {actions ? <span className={styles.divider} /> : null}
      {actions}
    </div>
  );
  const styleBar = tool ? <div className={cn(styles.bar, styles.surface, styles.styleBar)}>{style}</div> : null;
  return (
    <div className={cn(styles.floating, className)} data-style-above={styleAbove || undefined}>
      {styleAbove ? styleBar : null}
      {bar}
      {styleAbove ? null : styleBar}
    </div>
  );
}
