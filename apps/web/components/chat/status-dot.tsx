"use client";

/** WS connection state dot — green / yellow / red. */
export function StatusDot({ status }: { status: "connecting" | "open" | "closed" }) {
  const color =
    status === "open"
      ? "var(--accent-green)"
      : status === "connecting"
        ? "var(--accent-yellow)"
        : "var(--accent-red)";
  return (
    <span
      className="inline-flex items-center gap-1 text-[10px]"
      style={{ color: "var(--text-muted)" }}
      title={status}
    >
      <span className="h-1.5 w-1.5 rounded-full" style={{ background: color }} />
    </span>
  );
}
