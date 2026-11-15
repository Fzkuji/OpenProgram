"use client";

import { useLayoutEffect, useRef, useState } from "react";
import * as Slider from "@radix-ui/react-slider";
import { EffortField } from "./effort-field";
import { formatEffortLabel } from "@/lib/effort/effort-color";
import type { ThinkingOption } from "./use-thinking-effort";

type Drag = { id: number; x: number; offset: number };

/** Discrete selection with continuous, bounded pointer feedback between stops. */
export function EffortSlider({ options, index, onPreview, onCommit, label }: {
  options: ThinkingOption[]; index: number; onPreview: (index: number | null) => void;
  onCommit: (index: number) => void; label: string;
}) {
  const root = useRef<HTMLSpanElement>(null);
  const handle = useRef<HTMLSpanElement>(null);
  const drag = useRef<Drag | null>(null);
  const current = useRef(index);
  const [dragging, setDragging] = useState(false);
  const [hover, setHover] = useState(false);
  const maximum = options.length - 1;
  const ratio = maximum > 0 ? index / maximum : 0;
  function lean() {
    const node = root.current, pointer = drag.current;
    if (!node || !pointer || maximum < 1) return;
    const box = node.getBoundingClientRect();
    const width = box.width - 16;
    if (width <= 0) return;
    const fraction = Math.max(0, Math.min(1, (pointer.x - pointer.offset - box.left - 8) / width));
    const steps = (fraction - current.current / maximum) * maximum;
    const strength = Math.min(.12 * (1 - Math.exp(-Math.abs(steps) / .12)), .48);
    const pixels = Math.round(Math.sign(steps) * strength * width / maximum / .5) * .5;
    node.style.setProperty("--effort-lean", `${pixels}px`);
  }
  function end() {
    drag.current = null; root.current?.style.removeProperty("--effort-lean"); setDragging(false);
  }
  useLayoutEffect(() => { current.current = index; lean(); });
  return <Slider.Root ref={root} className="effort-slider" value={[index]} min={0} max={maximum} step={1}
    style={{ "--effort-position": ratio } as React.CSSProperties}
    data-dragging={dragging ? "true" : undefined} data-thumb-tip={hover || dragging ? "true" : undefined}
    onValueChange={([next]) => { if (next !== undefined) onPreview(next); }}
    onValueCommit={([next]) => { if (next !== undefined) onCommit(next); end(); }}
    onClick={event => event.stopPropagation()}
    onPointerDown={event => {
      if (event.button !== 0 || !root.current) return;
      const box = root.current.getBoundingClientRect();
      const offset = event.clientX - (box.left + 8 + ratio * (box.width - 16));
      drag.current = { id: event.pointerId, x: event.clientX, offset: Math.abs(offset) <= .7 * 16 / 2 ? offset : 0 };
      setDragging(true); lean();
    }}
    onPointerMove={event => {
      const box = handle.current?.getBoundingClientRect();
      setHover(!!box && event.clientX >= box.left && event.clientX <= box.right && event.clientY >= box.top && event.clientY <= box.bottom);
      if (drag.current?.id !== event.pointerId) return;
      if (event.buttons === 0) { end(); onPreview(null); return; }
      drag.current.x = event.clientX; lean();
    }}
    onPointerLeave={() => setHover(false)}
    onPointerUp={end}
    onPointerCancel={() => { end(); onPreview(null); }}
    onLostPointerCapture={() => { const interrupted = drag.current !== null; end(); if (interrupted) onPreview(null); }}>
    <Slider.Track className="effort-slider-track">
      <span className="effort-fill-window" aria-hidden="true">
        <span className="effort-fill-surface"><EffortField active={index === maximum} /></span>
      </span>
    </Slider.Track>
    <span className="effort-stop-row" aria-hidden="true">
      {options.map((option) => <span key={option.value} className="effort-stop-target">
        <span className="effort-stop" data-recommended={option.recommended ? "true" : undefined} />
        <span className="effort-stop-tip">{formatEffortLabel(option.value)}</span>
      </span>)}
    </span>
    <span className="effort-handle-rail" aria-hidden="true"><span className="effort-handle-carriage">
      <span ref={handle} className="effort-handle"><span className="effort-thumb-tip">{formatEffortLabel(options[index]?.value ?? "")}</span></span>
    </span></span>
    <Slider.Thumb className="effort-input-thumb" aria-label={label} aria-valuetext={formatEffortLabel(options[index]?.value ?? "")} />
  </Slider.Root>;
}
