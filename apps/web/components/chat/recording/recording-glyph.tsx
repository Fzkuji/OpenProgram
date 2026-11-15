/** The record mark used by the composer tile, the card and the + menu. */
export function RecordingGlyph({ size = 16 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" aria-hidden="true">
      <circle cx="12" cy="12" r="9" stroke="currentColor" strokeWidth="2" />
      <circle cx="12" cy="12" r="4.5" fill="currentColor" />
    </svg>
  );
}
