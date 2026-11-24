// What the author looks at above the conversation: one file large, the task's other files in a strip.
import { useEffect, useMemo, useRef, useState } from "react";
import { FileCode2, FileText, Globe, Image as ImageIcon, Sheet } from "lucide-react";
import { cn } from "@/lib/utils";
import { op } from "./api";
import { GROUPS, ago, baseName, type Artifact } from "./model";
import { esc, markdown } from "./text";

interface Page { page: number; width: number; height: number; jpeg: string }

// Read once per file version: switching back to a file draws it at once.
const images = new Map<string, string>();
const texts = new Map<string, string>();
const thumbs = new Map<string, string>();
const version = (a: Artifact) => `${a.path}@${a.mtime}`;

async function imageOf(a: Artifact) {
  const key = version(a);
  if (!images.has(key)) {
    const got = await op<{ mime: string; data: string }>("read_file", { path: a.path });
    images.set(key, `data:${got.mime};base64,${got.data}`);
  }
  return images.get(key)!;
}

async function textOf(a: Artifact) {
  const key = version(a);
  if (!texts.has(key)) texts.set(key, (await op<{ text: string }>("read_text", { path: a.path })).text);
  return texts.get(key)!;
}

async function thumbOf(a: Artifact) {
  const key = version(a);
  if (!thumbs.has(key)) {
    if (a.kind === "image") thumbs.set(key, await imageOf(a));
    else if (a.kind === "pdf") {
      const got = await op<{ pages: Page[] }>("pages", { path: a.path, start: 0, count: 1, zoom: 0.5 });
      thumbs.set(key, got.pages[0] ? `data:image/jpeg;base64,${got.pages[0].jpeg}` : "");
    }
  }
  return thumbs.get(key) || "";
}

function csvRows(text: string, limit = 300): string[][] {
  const sep = text.split("\n", 1)[0].includes("\t") ? "\t" : ",";
  const rows: string[][] = [];
  let row: string[] = [], cell = "", quoted = false;
  for (let i = 0; i < text.length && rows.length < limit; i++) {
    const c = text[i];
    if (quoted) {
      if (c === '"' && text[i + 1] === '"') { cell += '"'; i++; }
      else if (c === '"') quoted = false;
      else cell += c;
    } else if (c === '"') quoted = true;
    else if (c === sep) { row.push(cell); cell = ""; }
    else if (c === "\n") { row.push(cell.replace(/\r$/, "")); rows.push(row); row = []; cell = ""; }
    else cell += c;
  }
  if (cell || row.length) rows.push([...row, cell]);
  return rows;
}

const KIND_ICON = { image: ImageIcon, pdf: FileText, table: Sheet, markdown: FileText, html: Globe, text: FileCode2, other: FileText };

// The pages of a PDF read so far, per file version, and how many it has.
const pdfPages = new Map<string, { pages: Page[]; total: number }>();

/** The large view of one file. A PDF reads two pages, then more as the view nears its end. */
function Viewer({ artifact }: { artifact: Artifact }) {
  const [image, setImage] = useState("");
  const [text, setText] = useState<string | null>(null);
  const [pdf, setPdf] = useState<{ pages: Page[]; total: number }>({ pages: [], total: Infinity });
  const [want, setWant] = useState(2);
  const box = useRef<HTMLDivElement>(null);
  const end = useRef<HTMLDivElement>(null);

  useEffect(() => {
    let alive = true;
    setImage(images.get(version(artifact)) || ""); setText(texts.get(version(artifact)) ?? null);
    setPdf(pdfPages.get(version(artifact)) || { pages: [], total: Infinity }); setWant(2);
    (async () => {
      try {
        if (artifact.kind === "image") { const url = await imageOf(artifact); if (alive) setImage(url); }
        else if (artifact.kind !== "pdf" && artifact.kind !== "other") { const t = await textOf(artifact); if (alive) setText(t); }
      } catch (e) { console.error(e); }
    })();
    return () => { alive = false; };
  }, [artifact]);

  // Read the next pages only while fewer are shown than the view wants.
  useEffect(() => {
    if (artifact.kind !== "pdf" || pdf.pages.length >= Math.min(want, pdf.total)) return;
    let alive = true;
    const start = pdf.pages.length;
    op<{ total: number; pages: Page[] }>("pages", { path: artifact.path, start, count: 2, zoom: 1.4 })
      .then((got) => {
        if (!alive) return;
        const next = { pages: [...pdf.pages, ...got.pages], total: got.pages.length ? got.total : start };
        pdfPages.set(version(artifact), next);
        setPdf(next);
      }).catch(console.error);
    return () => { alive = false; };
  }, [artifact, pdf, want]);

  // Scrolling near the last page read asks for two more.
  useEffect(() => {
    const element = end.current;
    if (artifact.kind !== "pdf" || !element || !box.current) return;
    const observer = new IntersectionObserver((entries) => {
      if (entries.some((e) => e.isIntersecting)) setWant((w) => Math.max(w, pdf.pages.length + 2));
    }, { root: box.current, rootMargin: "600px" });
    observer.observe(element);
    return () => observer.disconnect();
  }, [artifact.kind, pdf.pages.length]);

  const body = useMemo(() => {
    if (text == null) return null;
    if (artifact.kind === "markdown") return <div className="md mx-auto max-w-[820px] px-6 py-5" dangerouslySetInnerHTML={{ __html: markdown(text) }} />;
    if (artifact.kind === "table") {
      const rows = csvRows(text);
      return (
        <table className="w-full border-collapse text-[12.5px]">
          <thead className="sticky top-0 bg-[var(--bg-secondary)]"><tr>{(rows[0] || []).map((c, i) => <th key={i} className="border-b border-border px-3 py-1.5 text-left font-medium text-text-bright">{c}</th>)}</tr></thead>
          <tbody>{rows.slice(1).map((r, i) => <tr key={i} className="border-b border-border/60">{r.map((c, j) => <td key={j} className="px-3 py-1 align-top font-mono text-text-primary">{c}</td>)}</tr>)}</tbody>
        </table>
      );
    }
    if (artifact.kind === "html") return <iframe title={artifact.path} sandbox="" srcDoc={text} className="h-full w-full border-0 bg-white" />;
    return <pre className="px-5 py-4 font-mono text-[12.5px] leading-[1.6] whitespace-pre-wrap break-words text-text-primary" dangerouslySetInnerHTML={{ __html: esc(text) }} />;
  }, [artifact.kind, artifact.path, text]);

  return (
    <div ref={box} className={cn("h-full overflow-auto rounded-xl border border-border",
      artifact.kind === "image" || artifact.kind === "pdf" ? "bg-[var(--bg-secondary)]" : "bg-card")}>
      {artifact.kind === "image" ? (image ? <img src={image} alt={artifact.path} className="mx-auto block h-full max-h-full w-auto max-w-full object-contain p-4" /> : null)
        : artifact.kind === "pdf" ? (
          <div className="flex flex-col items-center gap-3 p-4">
            {pdf.pages.map((p) => <img key={p.page} src={`data:image/jpeg;base64,${p.jpeg}`} alt={`第 ${p.page + 1} 页`}
              className="w-full max-w-[820px] rounded-sm bg-white shadow-sm" />)}
            <div ref={end} className="h-px w-full" />
          </div>
        ) : body}
    </div>
  );
}

function Thumb({ artifact, on, onPick }: { artifact: Artifact; on: boolean; onPick(): void }) {
  const [src, setSrc] = useState(() => thumbs.get(version(artifact)) || "");
  useEffect(() => {
    if (artifact.kind !== "image" && artifact.kind !== "pdf") return;
    let alive = true;
    thumbOf(artifact).then((url) => { if (alive) setSrc(url); }).catch(console.error);
    return () => { alive = false; };
  }, [artifact]);
  const Icon = KIND_ICON[artifact.kind];
  return (
    <button type="button" onClick={onPick} title={artifact.path}
      className="group flex w-[148px] shrink-0 flex-col gap-1 text-left">
      <span className={cn("flex h-[84px] w-full items-center justify-center overflow-hidden rounded-lg border bg-[var(--bg-secondary)] transition-colors",
        on ? "border-[var(--accent-blue)] ring-1 ring-[var(--accent-blue)]" : "border-border group-hover:border-[var(--text-dim)]")}>
        {src ? <img src={src} alt="" className={cn("h-full w-full", artifact.kind === "pdf" ? "object-cover object-top" : "object-contain p-1.5")} />
          : <Icon className="size-6 text-text-muted" />}
      </span>
      <span className={cn("truncate text-xs", on ? "text-text-bright" : "text-text-primary")}>{baseName(artifact.path)}</span>
      <span className="truncate text-[11px] text-text-muted">{GROUPS[artifact.group]} · {ago(new Date(artifact.mtime * 1000).toISOString())}</span>
    </button>
  );
}

const picked = new Map<string, string>();

/** One file large, the rest in a strip below it. The newest change leads; then the task's
 *  own files, the paper, what the last check wrote. */
export function ArtifactPanel({ scope, items }: { scope: string; items: Artifact[] }) {
  const [path, setPath] = useState(() => picked.get(scope) || "");
  const current = items.find((a) => a.path === path) || items[0];
  useEffect(() => { if (current) picked.set(scope, current.path); }, [scope, current]);
  if (!current) return null;
  return (
    <div className="flex min-h-0 flex-1 flex-col gap-2.5">
      <div className="flex items-baseline gap-2 text-xs text-text-muted">
        <span className="truncate font-mono text-text-primary">{current.path}</span>
        <span className="shrink-0">{GROUPS[current.group]} · {ago(new Date(current.mtime * 1000).toISOString())}</span>
      </div>
      <div className="min-h-0 flex-1"><Viewer artifact={current} /></div>
      {items.length > 1 ? (
        <div className="flex shrink-0 gap-3 overflow-x-auto pb-1">
          {items.map((a) => <Thumb key={a.path} artifact={a} on={a.path === current.path} onPick={() => setPath(a.path)} />)}
        </div>
      ) : null}
    </div>
  );
}

/** The files to show for `task` (or the project), read again when `changes` moves. */
export function useArtifacts(task: string, changes: number): Artifact[] {
  const [items, setItems] = useState<Artifact[]>([]);
  useEffect(() => {
    let alive = true;
    op<{ items: Artifact[] }>("artifacts", task ? { task } : {})
      .then((r) => { if (alive) setItems(r.items); }).catch(console.error);
    return () => { alive = false; };
  }, [task, changes]);
  return items;
}
