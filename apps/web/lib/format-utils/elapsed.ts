/** A duration in seconds as "42s", "2m 14s" or "1h 5m"; with the interface's
 *  `text`, in its language: "42 秒", "2 分 14 秒", "4 分钟", "1 小时 5 分". */
export function formatElapsed(seconds: number | undefined, text?: (en: string, zh: string) => string): string {
  const total = Math.max(0, Math.round(seconds ?? 0));
  const hours = Math.floor(total / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  const rest = total % 60;
  const en = hours ? `${hours}h ${minutes}m` : minutes ? `${minutes}m ${rest}s` : `${rest}s`;
  if (!text) return en;
  const zh = hours ? (minutes ? `${hours} 小时 ${minutes} 分` : `${hours} 小时`)
    : minutes ? (rest ? `${minutes} 分 ${rest} 秒` : `${minutes} 分钟`)
    : `${rest} 秒`;
  return text(en, zh);
}
