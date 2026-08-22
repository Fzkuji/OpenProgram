"use client";

/**
 * Desktop generic context-menu overlay page. Same mechanism as the
 * main-menu overlay (apps/desktop/main.js openMainMenu): a dedicated
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

import { Suspense, useEffect, useMemo, useRef, useState } from "react";
import { useSearchParams } from "next/navigation";
import * as DropdownMenuPrimitive from "@radix-ui/react-dropdown-menu";
import { Bookmark, Check, ChevronRight, Folder } from "lucide-react";

import { itemCls, MENU_PANEL, MENU_SEPARATOR } from "@/components/chat/top-bar/menu-styles";
import { isThemeId } from "@/lib/prefs/theme-pref";

interface ContextMenuItem {
  id: string;
  label: string;
  iconUrl?: string;
  icon?: "folder";
  disabled?: boolean;
  checked?: boolean;
  separatorBefore?: boolean;
  children?: ContextMenuItem[];
}

interface MainMenuBridge {
  choose(id: string): void;
  close(): void;
  scheduleClose?(delay?: number): void;
  cancelClose?(): void;
  resize?(size: { width: number; height: number }): void;
  onUpdate?(cb: (state: MenuState) => void): () => void;
}

interface MenuState {
  items: ContextMenuItem[];
  x: number;
  y: number;
  theme?: string;
  width?: number;
}

function mainMenuBridge(): MainMenuBridge | null {
  const api = (
    window as unknown as {
      openprogramDesktop?: { mainMenu?: MainMenuBridge };
    }
  ).openprogramDesktop?.mainMenu;
  return api ?? null;
}

const NESTED_MENU_WIDTH = 280;
const cancelHoverClose = () => mainMenuBridge()?.cancelClose?.();
const scheduleHoverClose = () => mainMenuBridge()?.scheduleClose?.(120);

function parseItems(raw: string | null): ContextMenuItem[] {
  try {
    const parsed = JSON.parse(raw ?? "[]") as unknown;
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
}

function ItemIcon({ item }: { item: ContextMenuItem }) {
  const [broken, setBroken] = useState(false);
  if (item.checked) return <Check size={13} aria-hidden="true" />;
  if (item.icon === "folder") return <Folder size={13} fill="currentColor" aria-hidden="true" />;
  if (!item.iconUrl) return null;
  return broken ? <Bookmark size={13} aria-hidden="true" /> : (
    // eslint-disable-next-line @next/next/no-img-element
    <img src={item.iconUrl} alt="" width={13} height={13} onError={() => setBroken(true)} />
  );
}

function hasItemIcon(item: ContextMenuItem) {
  return Boolean(item.checked || item.icon === "folder" || item.iconUrl);
}

function NestedMenuItems({ items }: { items: ContextMenuItem[] }) {
  const choose = (item: ContextMenuItem) => {
    if (!item.disabled) mainMenuBridge()?.choose(item.id);
  };
  return items.map((item) => (
    <div key={item.id}>
      {item.separatorBefore ? <div className={MENU_SEPARATOR} /> : null}
      {item.children?.length ? (
        <DropdownMenuPrimitive.Sub>
          <DropdownMenuPrimitive.SubTrigger
            disabled={item.disabled}
            className={`${itemCls(false)} w-full min-w-0 outline-none data-[highlighted]:bg-bg-hover data-[highlighted]:text-text-bright data-[state=open]:bg-bg-hover data-[state=open]:text-text-bright`}
            title={item.label}
          >
            {hasItemIcon(item) ? (
              <span className="inline-flex w-[14px] shrink-0 items-center justify-center">
                <ItemIcon item={item} />
              </span>
            ) : null}
            <span className="min-w-0 flex-1 truncate text-left">{item.label}</span>
            <ChevronRight size={13} className="ml-auto shrink-0" aria-hidden="true" />
          </DropdownMenuPrimitive.SubTrigger>
          <DropdownMenuPrimitive.Portal>
            <DropdownMenuPrimitive.SubContent
              sideOffset={2}
              alignOffset={-6}
              collisionPadding={8}
              className={`${MENU_PANEL} w-[280px] max-w-[calc(100vw-16px)] outline-none`}
              onPointerEnter={cancelHoverClose}
              onPointerLeave={scheduleHoverClose}
            >
              <NestedMenuItems items={item.children} />
            </DropdownMenuPrimitive.SubContent>
          </DropdownMenuPrimitive.Portal>
        </DropdownMenuPrimitive.Sub>
      ) : (
        <DropdownMenuPrimitive.Item
          disabled={item.disabled}
          className={`${itemCls(false)} w-full min-w-0 outline-none data-[highlighted]:bg-bg-hover data-[highlighted]:text-text-bright data-[disabled]:pointer-events-none data-[disabled]:opacity-55`}
          title={item.label}
          onSelect={() => choose(item)}
        >
          {hasItemIcon(item) ? (
            <span className="inline-flex w-[14px] shrink-0 items-center justify-center">
              <ItemIcon item={item} />
            </span>
          ) : null}
          <span className="min-w-0 flex-1 truncate">{item.label}</span>
        </DropdownMenuPrimitive.Item>
      )}
    </div>
  ));
}

function NestedContextMenu({
  items,
  x,
  y,
}: {
  items: ContextMenuItem[];
  x: number;
  y: number;
}) {
  const close = () => mainMenuBridge()?.close();
  return (
    <DropdownMenuPrimitive.Root open modal={false} onOpenChange={(open) => { if (!open) close(); }}>
      <DropdownMenuPrimitive.Trigger asChild>
        <button
          type="button"
          tabIndex={-1}
          aria-hidden="true"
          style={{ position: "fixed", left: x, top: y, width: 1, height: 1, opacity: 0 }}
        />
      </DropdownMenuPrimitive.Trigger>
      <DropdownMenuPrimitive.Portal>
        <DropdownMenuPrimitive.Content
          side="bottom"
          align="start"
          sideOffset={0}
          collisionPadding={8}
          className={`${MENU_PANEL} w-[280px] max-w-[calc(100vw-16px)] outline-none`}
          style={{ width: NESTED_MENU_WIDTH }}
          onEscapeKeyDown={close}
          onPointerDownOutside={close}
          onPointerEnter={cancelHoverClose}
          onPointerLeave={scheduleHoverClose}
          onCloseAutoFocus={(event) => event.preventDefault()}
        >
          <NestedMenuItems items={items} />
        </DropdownMenuPrimitive.Content>
      </DropdownMenuPrimitive.Portal>
    </DropdownMenuPrimitive.Root>
  );
}

function ContextMenuOverlayPage() {
  const params = useSearchParams();
  const panelRef = useRef<HTMLDivElement>(null);
  const initialState = useMemo<MenuState>(() => ({
    items: parseItems(params.get("items")),
    x: Number(params.get("x")) || 0,
    y: Number(params.get("y")) || 0,
    theme: params.get("theme") || undefined,
    width: Math.max(0, Number(params.get("width")) || 0),
  }), [params]);
  const [menuState, setMenuState] = useState(initialState);
  const items = menuState.items;

  const firstEnabled = items.findIndex((item) => !item.disabled);
  const [active, setActive] = useState(firstEnabled < 0 ? 0 : firstEnabled);
  const nested = params.get("cascade") === "1"
    || items.some((item) => Boolean(item.children?.length));
  const requestedWidth = menuState.width || 0;

  useEffect(() => mainMenuBridge()?.onUpdate?.((state) => {
    setMenuState({
      items: Array.isArray(state.items) ? state.items : [],
      x: Number(state.x) || 0,
      y: Number(state.y) || 0,
      theme: state.theme,
      width: Math.max(0, Number(state.width) || 0),
    });
  }), []);

  useEffect(() => {
    setActive(firstEnabled < 0 ? 0 : firstEnabled);
  }, [firstEnabled, items]);

  // Theme comes from the opener (query) — same contract as the
  // main-menu overlay page.
  useEffect(() => {
    const theme = menuState.theme;
    if (isThemeId(theme)) {
      document.documentElement.dataset.theme = theme;
    }
    document.documentElement.style.background = "transparent";
    document.body.style.background = "transparent";
  }, [menuState.theme]);

  // main.js can only guess the overlay's size before this document exists
  // (it has no font metrics), so it sizes the host view from a row-count
  // estimate and we correct it here: measure the laid-out panel — which is
  // `max-content` wide with nowrap rows, so it is exactly as wide as the
  // longest label — and report it back. Without this, long labels wrapped
  // inside the 200px guess and tall menus were clipped.
  useEffect(() => {
    if (nested) return;
    const panel = panelRef.current;
    if (!panel) return;
    const report = () => {
      const rect = panel.getBoundingClientRect();
      if (rect.width > 0 && rect.height > 0) {
        // `maxHeight: calc(100vh - 48px)` is relative to this overlay's
        // current WebContentsView. Reporting only rect.height makes every
        // host resize reduce the next measurement by another 48px until the
        // menu becomes an unusable strip. scrollHeight/scrollWidth retain the
        // intrinsic menu size even while the current host clips it.
        mainMenuBridge()?.resize?.({
          width: Math.max(rect.width, panel.scrollWidth),
          height: Math.max(rect.height, panel.scrollHeight),
        });
      }
    };
    report();
    // Web fonts can land after first paint and change the intrinsic width.
    const observer = new ResizeObserver(report);
    observer.observe(panel);
    return () => observer.disconnect();
  }, [items, nested, requestedWidth]);

  const choose = (item: ContextMenuItem) => {
    if (item.disabled) return;
    mainMenuBridge()?.choose(item.id);
  };
  const close = () => mainMenuBridge()?.close();

  useEffect(() => {
    if (nested) return;
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
  }, [active, items, nested]);

  if (nested) {
    return (
      <NestedContextMenu
        items={items}
        x={menuState.x}
        y={menuState.y}
      />
    );
  }

  return (
    // The view is 24px (gutter) wider/taller than the panel on every side
    // for the drop shadow. Pin the panel top-LEFT at the gutter offset —
    // main.js places the view so the panel's top-left lands on the anchor.
    // Deliberately no right/bottom: the box must not constrain the panel's
    // intrinsic size, since that size is what gets measured and reported.
    <div
      style={{
        position: "absolute",
        top: 24,
        left: 24,
        display: "flex",
        justifyContent: "flex-start",
        alignItems: "flex-start",
      }}
    >
      {/* Generic menus keep their intrinsic width unless the caller supplies
         a finite width. Bookmark folders use the finite form so imported
         titles truncate instead of expanding the overlay. */}
      <div
        ref={panelRef}
        className={MENU_PANEL}
        style={{
          width: requestedWidth || "max-content",
          minWidth: 180,
          maxWidth: requestedWidth ? "calc(100vw - 48px)" : undefined,
          maxHeight: "calc(100vh - 48px)",
          overflowY: "auto",
        }}
        role="menu"
      >
        {items.map((item, i) => (
          <div key={item.id}>
            {item.separatorBefore ? <div className={MENU_SEPARATOR} /> : null}
            <div
              role="menuitemcheckbox"
              aria-checked={item.checked || undefined}
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
              {hasItemIcon(item) ? (
                <span className="inline-flex w-[14px] shrink-0 items-center justify-center">
                  <ItemIcon item={item} />
                </span>
              ) : null}
              <span
                className={requestedWidth
                  ? "min-w-0 flex-1 truncate"
                  : "flex-1 whitespace-nowrap"}
                title={item.label}
              >
                {item.label}
              </span>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

// Static export: useSearchParams needs a Suspense boundary at prerender.
export default function Page() {
  return (
    <Suspense fallback={null}>
      <ContextMenuOverlayPage />
    </Suspense>
  );
}
