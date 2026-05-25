"use client";

/**
 * Favorite functions list — reads `window.availableFunctions` and
 * `window.programsMeta.{favorites,icons}` to produce a draggable list
 * of starred functions with their user-chosen emoji icon (or a default
 * box). Clicking a favourite opens the fn-form (chat route) or routes
 * to /chat first (other routes), via the zustand store + Next router —
 * no longer delegates to the legacy `clickFunction` window global.
 *
 * Drag-and-drop reorder: rows are HTML5 draggable. Drop persists the
 * new order back through POST /api/programs/meta and also updates
 * window.programsMeta with a fresh object reference so the legacy
 * polling layer + this component re-render immediately.
 *
 * Display order = `programsMeta.favorites` array order. NOT alphabetised.
 */

import { useState } from "react";
import { usePathname, useRouter } from "next/navigation";

import { useSessionStore, type AgenticFunction } from "@/lib/session-store";

import { useWindowGlobals } from "./use-window-globals";

const DEFAULT_ICON = "📦";

interface FunctionsMeta {
  favorites: string[];
  folders: Record<string, string[]>;
  icons: Record<string, string>;
}

async function persistMeta(meta: FunctionsMeta): Promise<void> {
  // Publish the new ref BEFORE the network round-trip so the UI feels
  // instant. The fetch is fire-and-forget; failure is logged but the
  // local state is what the user sees.
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
  const [draggingName, setDraggingName] = useState<string | null>(null);
  const [overName, setOverName] = useState<string | null>(null);

  // Preserve user order from the favorites array; only include names
  // that still exist in availableFunctions.
  const fnByName = new Map(
    (availableFunctions || []).map((f) => [f.name, f] as const),
  );
  const ordered = (programsMeta.favorites || [])
    .map((n) => fnByName.get(n))
    .filter((f): f is AgenticFunction => Boolean(f));
  if (ordered.length === 0) return null;

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

  function reorder(from: string, to: string): string[] {
    const arr = [...(programsMeta.favorites || [])];
    const fromIdx = arr.indexOf(from);
    const toIdx = arr.indexOf(to);
    if (fromIdx < 0 || toIdx < 0 || fromIdx === toIdx) return arr;
    arr.splice(fromIdx, 1);
    // Insert before `to`. If we removed something before `to`, the
    // target index shifted down by 1.
    const newToIdx = fromIdx < toIdx ? toIdx - 1 : toIdx;
    arr.splice(newToIdx, 0, from);
    return arr;
  }

  function handleDrop(target: string) {
    if (!draggingName || draggingName === target) {
      setDraggingName(null);
      setOverName(null);
      return;
    }
    const newFavorites = reorder(draggingName, target);
    void persistMeta({
      favorites: newFavorites,
      folders: programsMeta.folders || {},
      icons: programsMeta.icons || {},
    });
    setDraggingName(null);
    setOverName(null);
  }

  return (
    <>
      {ordered.map((f) => {
        const cat = f.category || "user";
        const icon = programsMeta.icons?.[f.name] || DEFAULT_ICON;
        const isDragging = draggingName === f.name;
        const isOver = overName === f.name && draggingName !== f.name;
        return (
          <div
            key={f.name}
            draggable
            onDragStart={(e) => {
              setDraggingName(f.name);
              e.dataTransfer.effectAllowed = "move";
              // Some browsers need data set to start the drag.
              try { e.dataTransfer.setData("text/plain", f.name); } catch {}
            }}
            onDragEnd={() => { setDraggingName(null); setOverName(null); }}
            onDragOver={(e) => {
              if (!draggingName || draggingName === f.name) return;
              e.preventDefault();
              e.dataTransfer.dropEffect = "move";
              setOverName(f.name);
            }}
            onDragLeave={() => {
              setOverName((cur) => (cur === f.name ? null : cur));
            }}
            onDrop={(e) => {
              e.preventDefault();
              handleDrop(f.name);
            }}
            className={[
              // Same layout as before; opacity + outline give live
              // feedback while dragging.
              "flex h-[32px] shrink-0 cursor-grab items-center gap-[12px]",
              "overflow-hidden truncate rounded-[6px] px-[8px] py-[6px]",
              "text-fs-base text-text-primary",
              "transition-[background-color,opacity,box-shadow] duration-150 ease-out",
              "hover:bg-bg-hover active:cursor-grabbing",
              isDragging ? "opacity-40" : "",
              isOver
                ? "outline outline-2 -outline-offset-2 outline-accent-primary"
                : "",
            ].filter(Boolean).join(" ")}
            onClick={() => onClick(f.name, cat)}
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
        );
      })}
    </>
  );
}
