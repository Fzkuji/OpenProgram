"use client";

/**
 * Favorite programs list — reads `window.availableFunctions` and
 * `window.programsMeta.favorites` to produce a filtered, category-
 * ordered list. Clicking a favourite opens the fn-form (chat route)
 * or routes to /chat first (other routes), via the zustand store +
 * Next router — no longer delegates to the legacy `clickFunction`
 * window global.
 */

import { usePathname, useRouter } from "next/navigation";

import { useSessionStore, type AgenticFunction } from "@/lib/session-store";

import { useLegacyGlobals } from "./use-legacy-globals";

const CAT_ORDER = ["app", "generated", "user", "meta", "builtin"] as const;
const CAT_ICONS: Record<string, string> = {
  app: "\u{1F4E6}",       // 📦
  meta: "\u{1F6E0}",      // 🛠
  builtin: "⚙",       // ⚙
  generated: "⚙",     // ⚙
  user: "✎",          // ✎
};

export function FavoritesList(): React.ReactElement | null {
  const { availableFunctions, programsMeta } = useLegacyGlobals();
  const openFnForm = useSessionStore((s) => s.openFnForm);
  const pathname = usePathname();
  const router = useRouter();
  const favSet = new Set(programsMeta.favorites || []);
  const filtered = (availableFunctions || []).filter((f) => favSet.has(f.name));
  // Stable category-first ordering (matches legacy renderFunctions).
  const ordered: typeof filtered = [];
  for (const cat of CAT_ORDER) {
    for (const f of filtered) {
      if ((f.category || "user") === cat) ordered.push(f);
    }
  }
  if (ordered.length === 0) return null;

  function onClick(name: string, category: string) {
    const fn = availableFunctions.find(
      (f: AgenticFunction) => f.name === name,
    );
    if (!fn) return;
    const onChat = pathname === "/chat" || pathname.startsWith("/s/");
    if (!onChat) {
      // Stash on window for init.js / page-shell hand-off effect to
      // pick up after the chat route mounts. (When init.js migrates
      // this becomes a `searchParams.get("run")` style hand-off.)
      const w = window as unknown as {
        __pendingRunFunction?: { name: string; cat: string };
      };
      w.__pendingRunFunction = { name, cat: category || "" };
      router.push("/chat");
      return;
    }
    openFnForm(fn);
  }

  return (
    <>
      {ordered.map((f) => {
        const cat = f.category || "user";
        const icon = CAT_ICONS[cat] || "✎";
        return (
          <div
            key={f.name}
            className="fav-item"
            onClick={() => onClick(f.name, cat)}
            title={f.description || ""}
          >
            <span className="fav-icon">{icon}</span>
            <span className="fav-name">{f.name}</span>
          </div>
        );
      })}
    </>
  );
}
