"use client";

/**
 * Desktop generic context-menu overlay page. Same mechanism as the
 * main-menu overlay (desktop/main.js openMainMenu): a dedicated
 * top-layer WebContentsView that paints ABOVE native web-tab views a
 * DOM menu can't cover. Unlike main-menu (fixed rows), this page is
 * data-driven — the opener passes `items` ([{id,label,disabled?}])
 * JSON-encoded in the URL query, and each chosen id is routed back to
 * the real UI window via mainMenu.choose(id) on the shared
 * main-menu:action channel. Callers namespace their ids (e.g.
 * "tabmenu:*") so onAction subscribers each recognise only their own.
 *
 * Styles are the canonical menu family (menu-styles.ts), same as the
 * main-menu overlay page.
 */

import { useEffect, useMemo, useRef, useState } from "react";
import { useSearchParams } from "next/navigation";

import { itemCls, MENU_PANEL } from "@/components/chat/top-bar/menu-styles";

interface ContextMenuItem {
  id: string;
  label: string;
  disabled?: boolean;
}

interface MainMenuBridge {
  choose(id: string): void;
  close(): void;
}

function mainMenuBridge(): MainMenuBridge | null {
  const api = (
    window as unknown as {
      openprogramDesktop?: { mainMenu?: MainMenuBridge };
    }
  ).openprogramDesktop?.mainMenu;
  return api ?? null;
}

export default function ContextMenuOverlayPage() {
  const params = useSearchParams();
  const panelRef = useRef<HTMLDivElement>(null);

  const items = useMemo<ContextMenuItem[]>(() => {
    try {
      const parsed = JSON.parse(params.get("items") ?? "[]") as unknown;
      if (!Array.isArray(parsed)) return [];
      return parsed.filter(
        (item): item is ContextMenuItem =>
          typeof item === "object"
          && item !== null
          && typeof (item as ContextMenuItem).id === "string"
          && typeof (item as ContextMenuItem).label === "string",
      );
    } catch {
      return [];
    }
  }, [params]);

  const firstEnabled = items.findIndex((item) => !item.disabled);
  const [active, setActive] = useState(firstEnabled < 0 ? 0 : firstEnabled);

  // Theme comes from the opener (query) — same contract as the
  // main-menu overlay page.
  useEffect(() => {
    const theme = params.get("theme");
    if (theme === "dark" || theme === "light") {
      document.documentElement.dataset.theme = theme;
    }
    document.documentElement.style.background = "transparent";
    document.body.style.background = "transparent";
  }, [params]);

  const choose = (item: ContextMenuItem) => {
    if (item.disabled) return;
    mainMenuBridge()?.choose(item.id);
  };
  const close = () => mainMenuBridge()?.close();

  useEffect(() => {
    const step = (from: number, dir: 1 | -1) => {
      // Next enabled row, wrapping.
      for (let n = 1; n <= items.length; n += 1) {
        const i = (from + dir * n + items.length * n) % items.length;
        if (!items[i]?.disabled) return i;
      }
      return from;
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.preventDefault();
        close();
      } else if (e.key === "ArrowDown") {
        e.preventDefault();
        setActive((i) => step(i, 1));
      } else if (e.key === "ArrowUp") {
        e.preventDefault();
        setActive((i) => step(i, -1));
      } else if (e.key === "Enter") {
        e.preventDefault();
        const item = items[active];
        if (item) choose(item);
      }
    };
    // Double safety beside main.js's blur close: a pointerdown outside
    // the panel closes the overlay.
    const onDown = (e: PointerEvent) => {
      if (panelRef.current && !panelRef.current.contains(e.target as Node)) {
        close();
      }
    };
    window.addEventListener("keydown", onKey);
    window.addEventListener("pointerdown", onDown);
    return () => {
      window.removeEventListener("keydown", onKey);
      window.removeEventListener("pointerdown", onDown);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [active, items]);

  return (
    // The view is 24px (gutter) wider/taller than the panel on every side
    // for the drop shadow. Pin the panel top-LEFT into the inset box —
    // main.js places the view so the panel's top-left lands on the anchor.
    <div
      style={{
        position: "absolute",
        top: 24,
        right: 24,
        bottom: 24,
        left: 24,
        display: "flex",
        justifyContent: "flex-start",
        alignItems: "flex-start",
      }}
    >
      <div ref={panelRef} className={MENU_PANEL} style={{ width: "100%" }} role="menu">
        {items.map((item, i) => (
          <div
            key={item.id}
            role="menuitem"
            aria-disabled={item.disabled || undefined}
            tabIndex={-1}
            className={itemCls(i === active && !item.disabled)}
            style={
              item.disabled
                ? { opacity: 0.55, cursor: "default", color: "var(--text-muted)" }
                : undefined
            }
            onMouseEnter={() => {
              if (!item.disabled) setActive(i);
            }}
            onClick={() => choose(item)}
          >
            <span className="flex-1">{item.label}</span>
          </div>
        ))}
      </div>
    </div>
  );
}
