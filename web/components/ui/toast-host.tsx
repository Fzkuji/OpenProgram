"use client";

/**
 * ToastHost — renders transient toasts fired via `showToast()` (lib/toast).
 *
 * Mounted once (in the top bar) so the bubbles appear at the TOP of the
 * page. Each toast auto-dismisses after its `duration`; the host is
 * `pointer-events:none` so it never blocks clicks underneath.
 */

import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";

import { TOAST_EVENT, type ToastDetail, type ToastTone } from "@/lib/toast";
import styles from "./toast-host.module.css";

interface Item {
  id: number;
  message: string;
  tone: ToastTone;
}

let _seq = 0;

export function ToastHost() {
  const [items, setItems] = useState<Item[]>([]);
  const [mounted, setMounted] = useState(false);
  const timers = useRef<Record<number, ReturnType<typeof setTimeout>>>({});

  useEffect(() => setMounted(true), []);

  useEffect(() => {
    function onToast(e: Event) {
      const d = (e as CustomEvent<ToastDetail>).detail;
      if (!d || !d.message) return;
      const id = ++_seq;
      setItems((cur) => [...cur, { id, message: d.message, tone: d.tone ?? "info" }]);
      timers.current[id] = setTimeout(() => {
        setItems((cur) => cur.filter((x) => x.id !== id));
        delete timers.current[id];
      }, d.duration ?? 3500);
    }
    window.addEventListener(TOAST_EVENT, onToast);
    const snapshot = timers.current;
    return () => {
      window.removeEventListener(TOAST_EVENT, onToast);
      Object.values(snapshot).forEach(clearTimeout);
    };
  }, []);

  if (!mounted || items.length === 0) return null;

  return createPortal(
    <div className={styles.host} role="status" aria-live="polite">
      {items.map((it) => (
        <div
          key={it.id}
          className={
            styles.toast +
            (it.tone === "warn" ? " " + styles.warn : "") +
            (it.tone === "error" ? " " + styles.error : "")
          }
        >
          {it.message}
        </div>
      ))}
    </div>,
    document.body,
  );
}
