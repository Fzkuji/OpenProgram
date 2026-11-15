"use client";

/**
 * ToastHost — renders transient toasts fired via `showToast()` (lib/toast).
 *
 * Mounted once (in the app shell) and portalled to <body>, so the bubbles
 * sit under the tab strip. Horizontally they centre over the centre area
 * the user is working in (toast-anchor.ts): read once when a toast shows,
 * then followed with a ResizeObserver only while a toast is visible.
 * Each toast auto-dismisses after its `duration`; the host is
 * `pointer-events:none` so it never blocks clicks underneath.
 */

import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";

import { TOAST_DISMISS_EVENT, TOAST_EVENT, type ToastAction, type ToastDetail, type ToastLink, type ToastTone } from "@/lib/format-utils/toast";
import { useCenterTabs } from "@/lib/tabs/center-tabs-store";
import { toastAnchor, toastCentreX } from "./toast-anchor";
import styles from "./toast-host.module.css";

interface Item {
  id: number;
  message: string;
  tone: ToastTone;
  link?: ToastLink;
  action?: ToastAction;
  sessionId?: string;
  uniformWidth?: boolean;
  key?: string;
}

let _seq = 0;

export function ToastHost() {
  const [items, setItems] = useState<Item[]>([]);
  const [mounted, setMounted] = useState(false);
  const timers = useRef<Record<number, ReturnType<typeof setTimeout>>>({});
  const hostRef = useRef<HTMLDivElement>(null);
  const newest = items.length > 0 ? items[items.length - 1] : null;
  const newestId = newest?.id ?? 0;
  const newestSession = newest?.sessionId;

  useEffect(() => setMounted(true), []);

  useEffect(() => {
    function onToast(e: Event) {
      const d = (e as CustomEvent<ToastDetail>).detail;
      if (!d || !d.message) return;
      const id = ++_seq;
      setItems((cur) => [
        ...cur,
        { id, message: d.message, tone: d.tone ?? "info", link: d.link, action: d.action, sessionId: d.sessionId, uniformWidth: d.uniformWidth, key: d.key },
      ]);
      timers.current[id] = setTimeout(() => {
        setItems((cur) => cur.filter((x) => x.id !== id));
        delete timers.current[id];
      }, d.duration ?? 3500);
    }
    function onDismiss(e: Event) {
      const key = (e as CustomEvent<string>).detail;
      setItems((cur) => cur.filter((x) => x.key !== key));
    }
    window.addEventListener(TOAST_EVENT, onToast);
    window.addEventListener(TOAST_DISMISS_EVENT, onDismiss);
    const snapshot = timers.current;
    return () => {
      window.removeEventListener(TOAST_EVENT, onToast);
      window.removeEventListener(TOAST_DISMISS_EVENT, onDismiss);
      Object.values(snapshot).forEach(clearTimeout);
    };
  }, []);

  // Centre over the working area: pick the anchor when a toast shows (or
  // a newer one arrives), then follow its size and the host's own while
  // any toast is visible. Nothing runs while no toast shows.
  useLayoutEffect(() => {
    const host = hostRef.current;
    if (!host || !newestId) return;
    const anchor = toastAnchor(document, useCenterTabs.getState(), newestSession);
    const place = () => {
      const box = anchor?.isConnected ? anchor.getBoundingClientRect() : null;
      const x = toastCentreX(box, document.documentElement.clientWidth, host.offsetWidth);
      host.style.left = `${x}px`;
    };
    place();
    const observer = new ResizeObserver(place);
    if (anchor) observer.observe(anchor);
    else observer.observe(document.documentElement);
    observer.observe(host);
    return () => observer.disconnect();
  }, [mounted, newestId, newestSession]);

  function dismiss(id: number) {
    setItems((cur) => cur.filter((x) => x.id !== id));
    if (timers.current[id]) {
      clearTimeout(timers.current[id]);
      delete timers.current[id];
    }
  }

  if (!mounted || items.length === 0) return null;

  return createPortal(
    <div ref={hostRef} className={styles.host} role="status" aria-live="polite">
      {items.map((it) => (
        <div
          key={it.id}
          className={
            styles.toast +
            (it.tone === "warn" ? " " + styles.warn : "") +
            (it.tone === "error" ? " " + styles.error : "") +
            (it.uniformWidth ? " w-[520px] justify-between" : "")
          }
        >
          <span className={styles.message}>{it.message}</span>
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
          {it.action ? (
            <button
              type="button"
              className={styles.link}
              onClick={() => { dismiss(it.id); it.action?.run(); }}
            >
              {it.action.label}
            </button>
          ) : null}
        </div>
      ))}
    </div>,
    document.body,
  );
}
