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

import { TOAST_EVENT, type ToastDetail, type ToastLink, type ToastTone } from "@/lib/format-utils/toast";
import styles from "./toast-host.module.css";

interface Item {
  id: number;
  message: string;
  tone: ToastTone;
  link?: ToastLink;
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
      setItems((cur) => [
        ...cur,
        { id, message: d.message, tone: d.tone ?? "info", link: d.link },
      ]);
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

  function dismiss(id: number) {
    setItems((cur) => cur.filter((x) => x.id !== id));
    if (timers.current[id]) {
      clearTimeout(timers.current[id]);
      delete timers.current[id];
    }
  }

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
          <span>{it.message}</span>
          {it.link ? (
            // `pointer-events: auto` (the host is none) so only the link
            // is clickable. A plain anchor — full nav to the settings
            // route is fine and matches the rest of the app.
            <a
              href={it.link.href}
              className={styles.link}
              onClick={() => dismiss(it.id)}
            >
              {it.link.label}
            </a>
          ) : null}
        </div>
      ))}
    </div>,
    document.body,
  );
}
