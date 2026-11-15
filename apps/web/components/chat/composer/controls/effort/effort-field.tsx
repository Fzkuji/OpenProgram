"use client";

import { useEffect, useRef, useState } from "react";
import { createEffortRenderer, type EffortRenderer } from "./effort-field-renderer";

/** Lazily creates the GPU field and retains it for the release transition. */
export function EffortField({ active }: { active: boolean }) {
  const canvas = useRef<HTMLCanvasElement>(null);
  const renderer = useRef<EffortRenderer | null>(null);
  const [mounted, setMounted] = useState(active);
  const [available, setAvailable] = useState(false);
  if (active && !mounted) setMounted(true);
  useEffect(() => {
    if (!mounted || !canvas.current) return;
    const instance = createEffortRenderer(canvas.current, setAvailable);
    renderer.current = instance;
    return () => { renderer.current = null; instance.dispose(); };
  }, [mounted]);
  useEffect(() => { renderer.current?.setActive(active); }, [active, mounted]);
  return <span className="effort-field" data-active={active && available ? "true" : undefined} aria-hidden="true">
    <span className="effort-field-base" />
    <span className="effort-field-mask">{mounted && <canvas ref={canvas} />}</span>
  </span>;
}
