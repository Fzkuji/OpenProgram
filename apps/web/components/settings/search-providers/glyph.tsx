"use client";

import styles from "../settings-page.module.css";

/* Small monogram circle for search providers — same shape as the LLM
   provider icons but using letters instead of brand SVGs. */
export function SearchProviderGlyph({ id }: { id: string }) {
  const letter = id.charAt(0).toUpperCase();
  const colors: Record<string, string> = {
    t: "#3b82f6",
    e: "#7c6fcd",
    d: "#f97316",
    b: "#10b981",
    g: "#84cc16",
  };
  const c = colors[letter.toLowerCase()] || "#6b7280";
  return (
    <div
      className={styles.providerIconLetter}
      style={{
        background: c + "22",
        color: c,
        border: `1px solid ${c}55`,
        width: 20,
        height: 20,
        display: "inline-flex",
        alignItems: "center",
        justifyContent: "center",
        borderRadius: 6,
        flexShrink: 0,
        fontWeight: 600,
        fontSize: 12,
      }}
    >
      {letter}
    </div>
  );
}
