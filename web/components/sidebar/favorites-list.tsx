"use client";

/**
 * Favorite programs list — reads `window.availableFunctions` and
 * `window.programsMeta.favorites` to produce a filtered, category-
 * ordered list. Clicking a favourite delegates to the legacy
 * `clickFunction(name, category)` which opens the fn form via the
 * shared zustand `openFnForm` action (or routes to /chat first if
 * we're elsewhere).
 */

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
    const w = window as unknown as {
      clickFunction?: (name: string, category: string) => void;
    };
    if (typeof w.clickFunction === "function") w.clickFunction(name, category);
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
