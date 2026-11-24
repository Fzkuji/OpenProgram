// Markdown reports and file diffs as escaped HTML.
export const esc = (s: unknown) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]!));

export function markdown(text: string) {
  const out: string[] = []; const lines = text.split("\n"); let i = 0;
  const inline = (s: string) => esc(s).replace(/`([^`]+)`/g, "<code>$1</code>").replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
  while (i < lines.length) {
    const l = lines[i];
    if (l.startsWith("```")) { const buf: string[] = []; i++; while (i < lines.length && !lines[i].startsWith("```")) buf.push(lines[i++]); i++; out.push(`<pre>${esc(buf.join("\n"))}</pre>`); continue; }
    if (/^\|/.test(l)) {
      const rows: string[] = []; while (i < lines.length && /^\|/.test(lines[i])) rows.push(lines[i++]);
      const cells = (r: string) => r.replace(/^\||\|$/g, "").split(/(?<!\\)\|/).map((c) => inline(c.trim().replace(/\\\|/g, "|")));
      const body = rows.filter((r) => !/^\|[\s:|-]+\|$/.test(r));
      out.push(`<table>${body.map((r, n) => `<tr>${cells(r).map((c) => (n ? `<td>${c}</td>` : `<th>${c}</th>`)).join("")}</tr>`).join("")}</table>`); continue;
    }
    const h = l.match(/^(#{1,4})\s+(.*)/);
    if (h) { out.push(`<h${h[1].length + 1}>${inline(h[2])}</h${h[1].length + 1}>`); i++; continue; }
    if (/^\s*[-*]\s+/.test(l)) { const items: string[] = []; while (i < lines.length && /^\s*[-*]\s+/.test(lines[i])) items.push(`<li>${inline(lines[i++].replace(/^\s*[-*]\s+/, ""))}</li>`); out.push(`<ul>${items.join("")}</ul>`); continue; }
    if (l.trim()) out.push(`<p>${inline(l)}</p>`);
    i++;
  }
  return out.join("");
}

/** A unified diff, with the changed words of each removed/added line pair marked. */
export function diffHtml(text: string) {
  const lines = String(text || "").split("\n"), out: string[] = [];
  for (let i = 0; i < lines.length; i++) {
    const l = lines[i];
    if (l.startsWith("+++") || l.startsWith("---")) continue;
    if (l.startsWith("@@")) { out.push(`<div class="h">${esc(l)}</div>`); continue; }
    if (l.startsWith("-")) {
      const dels: string[] = []; while (i < lines.length && lines[i].startsWith("-") && !lines[i].startsWith("---")) dels.push(lines[i++].slice(1));
      const adds: string[] = []; while (i < lines.length && lines[i].startsWith("+") && !lines[i].startsWith("+++")) adds.push(lines[i++].slice(1));
      i--;
      for (let k = 0; k < Math.max(dels.length, adds.length); k++) {
        if (k < dels.length && k < adds.length) { const [a, b] = wordDiff(dels[k], adds[k]); out.push(`<div class="d">${a}</div>`, `<div class="a">${b}</div>`); }
        else if (k < dels.length) out.push(`<div class="d">${esc(dels[k])}</div>`);
        else out.push(`<div class="a">${esc(adds[k])}</div>`);
      }
      continue;
    }
    out.push(`<div class="${l.startsWith("+") ? "a" : ""}">${esc(l.startsWith("+") || l.startsWith(" ") ? l.slice(1) : l) || " "}</div>`);
  }
  return out.join("");
}

function wordDiff(a: string, b: string): [string, string] {
  const x = a.split(/(\s+)/), y = b.split(/(\s+)/);
  if (x.length * y.length > 400000) return [esc(a), esc(b)];
  const n = x.length, m = y.length, L = Array.from({ length: n + 1 }, () => new Uint16Array(m + 1));
  for (let i = n - 1; i >= 0; i--) for (let j = m - 1; j >= 0; j--) L[i][j] = x[i] === y[j] ? L[i + 1][j + 1] + 1 : Math.max(L[i + 1][j], L[i][j + 1]);
  let i = 0, j = 0, left = "", right = "";
  while (i < n || j < m) {
    if (i < n && j < m && x[i] === y[j]) { left += esc(x[i]); right += esc(y[j]); i++; j++; }
    else if (j < m && (i >= n || L[i][j + 1] >= L[i + 1][j])) right += `<ins>${esc(y[j++])}</ins>`;
    else left += `<del>${esc(x[i++])}</del>`;
  }
  return [left, right];
}
