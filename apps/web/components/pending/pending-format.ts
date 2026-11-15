type Text = (en: string, zh: string) => string;

/** How long ago a report arrived: just now, 12 min, 3 h, 2 d. */
export function pendingWhen(at: number, text: Text, now = Date.now() / 1000): string {
  const seconds = Math.max(0, now - at);
  if (seconds < 60) return text("just now", "刚刚");
  if (seconds < 3600) { const m = Math.floor(seconds / 60); return text(`${m} min ago`, `${m} 分钟前`); }
  if (seconds < 86400) { const h = Math.floor(seconds / 3600); return text(`${h} h ago`, `${h} 小时前`); }
  const d = Math.floor(seconds / 86400);
  return text(`${d} d ago`, `${d} 天前`);
}
