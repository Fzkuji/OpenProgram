"use client";

/**
 * Favorite functions list — reads `window.availableFunctions` and
 * `window.programsMeta.{favorites,icons}` to produce a draggable list
 * with smooth FLIP-animated reorder (framer-motion `Reorder.Group`).
 *
 * Display order = `programsMeta.favorites` array order (NOT sorted).
 * Drag a row, other rows physically slide out of the way to make room;
 * release to commit the new order. Optimistic local update + persist
 * via POST /api/programs/meta.
 *
 * Clicking a favourite still opens the fn-form (chat route) or routes
 * to /chat first via the zustand store + Next router.
 */

import { useEffect, useState } from "react";
import { usePathname, useRouter } from "next/navigation";
import { Reorder } from "framer-motion";

import { useSessionStore, type AgenticFunction } from "@/lib/session-store";

import { useWindowGlobals } from "./use-window-globals";

const DEFAULT_ICON = "📦";

interface FunctionsMeta {
  favorites: string[];
  folders: Record<string, string[]>;
  icons: Record<string, string>;
}

async function persistMeta(meta: FunctionsMeta): Promise<void> {
  // Publish the new ref BEFORE the network round-trip so the rest of
  // the UI updates instantly. The fetch is fire-and-forget.
  (window as unknown as Record<string, unknown>).programsMeta = {
    favorites: [...meta.favorites],
    folders: Object.fromEntries(
      Object.entries(meta.folders).map(([k, v]) => [k, [...v]]),
    ),
    icons: { ...meta.icons },
  };
  window.dispatchEvent(new CustomEvent("wah:meta-changed"));
  try {
    await fetch("/api/programs/meta", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(meta),
    });
  } catch (err) {
    console.error("Save programs/meta failed:", err);
  }
}

export function FavoritesList(): React.ReactElement | null {
  const { availableFunctions, programsMeta } = useWindowGlobals();
  const openFnForm = useSessionStore((s) => s.openFnForm);
  const pathname = usePathname();
  const router = useRouter();

  // Local copy of the favorites order. Mirror it from programsMeta;
  // updates during drag are local-only, persisted on drag end.
  const [order, setOrder] = useState<string[]>(
    () => programsMeta.favorites || [],
  );
  // While a drag is happening (or just finished), suppress the click
  // handler — otherwise releasing a drag also fires onClick and opens
  // the fn-form. Cleared shortly after drag end so subsequent real
  // clicks still register.
  const [dragGuard, setDragGuard] = useState(false);
  useEffect(() => {
    // External changes (toggle from /functions page, server reload)
    // should re-sync our local order, unless we're mid-drag and the
    // arrays already match in length + members.
    const fav = programsMeta.favorites || [];
    const same =
      fav.length === order.length &&
      fav.every((n) => order.includes(n)) &&
      order.every((n) => fav.includes(n));
    if (!same) setOrder(fav);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [programsMeta.favorites]);

  const fnByName = new Map(
    (availableFunctions || []).map((f) => [f.name, f] as const),
  );
  // Use local `order` for rendering so drag updates feel instant.
  const items = order
    .map((n) => fnByName.get(n))
    .filter((f): f is AgenticFunction => Boolean(f));
  if (items.length === 0) return null;

  function onClick(name: string, category: string) {
    const fn = availableFunctions.find(
      (f: AgenticFunction) => f.name === name,
    );
    if (!fn) return;
    const onChat = pathname === "/chat" || pathname.startsWith("/s/");
    if (!onChat) {
      const w = window as unknown as {
        __pendingRunFunction?: { name: string; cat: string };
        __lastChatPath?: string;
      };
      w.__pendingRunFunction = { name, cat: category || "" };
      router.push(w.__lastChatPath || "/chat");
      return;
    }
    openFnForm(fn);
  }

  function handleReorder(next: AgenticFunction[]) {
    const nextNames = next.map((f) => f.name);
    setOrder(nextNames);
  }

  function commitOrder() {
    // Skip the round-trip when nothing actually moved.
    const cur = programsMeta.favorites || [];
    const same =
      cur.length === order.length && cur.every((n, i) => n === order[i]);
    if (same) return;
    void persistMeta({
      favorites: order,
      folders: programsMeta.folders || {},
      icons: programsMeta.icons || {},
    });
  }

  return (
    <Reorder.Group
      axis="y"
      values={items}
      onReorder={handleReorder}
      // Tighten internal layout: framer wraps in a <ul>, we want it to
      // behave like a vertical stack matching the legacy sidebar.
      className="flex flex-col gap-0 m-0 p-0 list-none"
    >
      {items.map((f) => {
        const cat = f.category || "user";
        const icon = programsMeta.icons?.[f.name] || DEFAULT_ICON;
        return (
          <Reorder.Item
            key={f.name}
            value={f}
            onDragStart={() => setDragGuard(true)}
            onDragEnd={() => {
              commitOrder();
              // Wait one frame for the click event that fires after
              // mouseup-on-drag to flush, then re-enable clicks.
              window.setTimeout(() => setDragGuard(false), 50);
            }}
            // The component renders <li> by default. Tweak to remove
            // bullet artefacts and apply our styling.
            className="list-none"
            // While dragging, raise the item visually.
            whileDrag={{
              scale: 1.02,
              boxShadow: "var(--shadow)",
              zIndex: 5,
            }}
            // Springy easing for the surrounding items as they slide
            // out of the way.
            transition={{ type: "spring", stiffness: 600, damping: 40 }}
          >
            <div
              className="flex h-[var(--ui-list-h)] shrink-0 cursor-grab items-center
                gap-[12px] overflow-hidden truncate rounded-[var(--ui-list-radius)]
                px-[8px] py-[6px] text-fs-base text-text-primary
                transition-colors duration-150 ease-out
                hover:bg-bg-hover active:cursor-grabbing select-none"
              onClick={(e) => {
                if (dragGuard) { e.preventDefault(); e.stopPropagation(); return; }
                onClick(f.name, cat);
              }}
              title={f.description || ""}
            >
              <span
                className="inline-flex size-[16px] flex-shrink-0 items-center
                  justify-center text-fs-base leading-none"
                aria-hidden="true"
              >
                {icon}
              </span>
              <span className="flex-1 truncate text-fs-base">{f.name}</span>
            </div>
          </Reorder.Item>
        );
      })}
    </Reorder.Group>
  );
}
