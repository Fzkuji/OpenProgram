"use client";

/**
 * Desktop main-menu overlay page. Rendered inside a dedicated top-layer
 * WebContentsView (apps/desktop/main.js openMainMenu) so it paints ABOVE the
 * native web-tab views that a DOM Radix menu can't cover.
 *
 * Hard constraint: the panel reuses the app's canonical menu styles
 * (menu-styles.ts: MENU_PANEL / itemCls / SHORTCUT / MENU_SEPARATOR) and
 * the same lucide icons / labels / shortcuts as
 * components/center-tabs/strip/main-menu.tsx — one component family, one
 * stylesheet, no hand-rolled HTML. Only the wiring differs: instead of
 * Radix onSelect, each row calls the preload bridge's mainMenu.choose(id)
 * and the main process routes the action back to the real UI window.
 */

import { Suspense, useEffect, useRef, useState } from "react";
import { useSearchParams } from "next/navigation";

import {
  itemCls,
  MENU_PANEL,
  MENU_SEPARATOR,
} from "@/components/ui/menu/menu-styles";
import { MenuRow, type MenuGlyph, type MenuKeys } from "@/components/ui/menu/menu-parts";
import { useTranslation } from "@/lib/i18n";
import { isThemeId } from "@/lib/prefs/appearance/theme-pref";

type ActionId = "new-tab" | "settings";

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

interface Row {
  id: ActionId;
  glyph: MenuGlyph;
  label: string;
  keys?: MenuKeys;
}

function MainMenuOverlayPage() {
  const params = useSearchParams();
  const { text } = useTranslation();
  const [active, setActive] = useState(0);
  const panelRef = useRef<HTMLDivElement>(null);

  // Theme comes from the opener (query) — this transient view can't wait
  // for the localStorage-based auto script race. menu-styles CSS vars key
  // off documentElement[data-theme].
  useEffect(() => {
    const theme = params.get("theme");
    if (isThemeId(theme)) {
      document.documentElement.dataset.theme = theme;
    }
    document.documentElement.style.background = "transparent";
    document.body.style.background = "transparent";
  }, [params]);

  // ponytail: New window is browser-only (no create-window IPC), so the
  // desktop overlay never shows it — matches main-menu.tsx's canOpenWindow.
  const separators = new Set([0]); // divider AFTER these indices
  const rows: Row[] = [
    { id: "new-tab", glyph: "add-square", label: text("New tab", "新标签页"), keys: { key: "T", mod: true } },
    { id: "settings", glyph: "settings", label: text("Settings", "设置"), keys: { key: ",", mod: true } },
  ];

  const choose = (id: ActionId) => {
    const bridge = mainMenuBridge();
    bridge?.choose(id);
  };
  const close = () => mainMenuBridge()?.close();

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.preventDefault();
        close();
      } else if (e.key === "ArrowDown") {
        e.preventDefault();
        setActive((i) => (i + 1) % rows.length);
      } else if (e.key === "ArrowUp") {
        e.preventDefault();
        setActive((i) => (i - 1 + rows.length) % rows.length);
      } else if (e.key === "Enter") {
        e.preventDefault();
        choose(rows[active].id);
      }
    };
    // Double safety beside main.js's blur close: a pointerdown outside the
    // panel closes the overlay.
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
  }, [active, rows.length]);

  return (
    // The view is GUTTER (24px) wider/taller than the panel on every side so
    // the drop shadow has room. Pin the panel into that inset box — top-right
    // aligned — so its right edge sits exactly GUTTER from the view's right
    // edge, which is what main.js's x math assumes (panel right = button right).
    <div
      style={{
        position: "absolute",
        top: 24,
        right: 24,
        bottom: 24,
        left: 24,
        display: "flex",
        justifyContent: "flex-end",
        alignItems: "flex-start",
      }}
    >
      <div ref={panelRef} className={MENU_PANEL} style={{ minWidth: 200, width: "100%" }} role="menu">
        {rows.map((row, i) => {
          return (
            <div key={row.id}>
              <div
                role="menuitem"
                tabIndex={-1}
                className={itemCls(i === active)}
                onMouseEnter={() => setActive(i)}
                onClick={() => choose(row.id)}
              >
                <MenuRow glyph={row.glyph} label={row.label} keys={row.keys} />
              </div>
              {separators.has(i) ? <div className={MENU_SEPARATOR} /> : null}
            </div>
          );
        })}
      </div>
    </div>
  );
}

// Static export: useSearchParams needs a Suspense boundary at prerender.
export default function Page() {
  return (
    <Suspense fallback={null}>
      <MainMenuOverlayPage />
    </Suspense>
  );
}
