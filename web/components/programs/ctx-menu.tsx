"use client";

import { useEffect, useRef, useState } from "react";
import styles from "./ctx-menu.module.css";

export interface CtxItem {
  type?: "sep";
  label?: string;
  action?: () => void;
}

export interface CtxMenuState {
  x: number;
  y: number;
  items: CtxItem[];
}

export function CtxMenu({
  state,
  onClose,
}: {
  state: CtxMenuState;
  onClose: () => void;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const [pos, setPos] = useState({ left: state.x, top: state.y });

  useEffect(() => {
    const r = ref.current?.getBoundingClientRect();
    if (!r) return;
    let { left, top } = pos;
    if (r.right > window.innerWidth) left = window.innerWidth - r.width - 4;
    if (r.bottom > window.innerHeight)
      top = window.innerHeight - r.height - 4;
    if (left !== pos.left || top !== pos.top) setPos({ left, top });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return (
    <div
      ref={ref}
      className={styles.ctxMenu}
      style={{ left: pos.left, top: pos.top }}
      onClick={(e) => e.stopPropagation()}
    >
      {state.items.map((it, i) =>
        it.type === "sep" ? (
          <div key={i} className={styles.ctxSep} />
        ) : (
          <div
            key={i}
            className={styles.ctxItem}
            onClick={() => {
              onClose();
              it.action?.();
            }}
          >
            {it.label}
          </div>
        ),
      )}
    </div>
  );
}
