/**
 * Live-preview model for the composer (Obsidian-style): the document is
 * the literal markdown, formatting is drawn on top, and syntax markers are
 * hidden unless the caret touches the construct they belong to. Pure
 * state → ranges so it can be tested without a DOM; the editor turns the
 * ranges into CodeMirror decorations.
 */
import { Language, defineLanguageFacet, ensureSyntaxTree, syntaxTree } from "@codemirror/language";
import type { EditorState } from "@codemirror/state";
import { GFM, parser as baseParser } from "@lezer/markdown";

const facet = defineLanguageFacet({});
export const composerMarkdown = new Language(facet, baseParser.configure(GFM), [], "markdown");

export type PreviewKind =
  | "strong" | "em" | "strike" | "code" | "link" | "url"
  | "h1" | "h2" | "h3" | "quote" | "fence" | "list";

/** Atomic inline chips drawn over composer tokens. */
export interface PreviewChip {
  from: number;
  to: number;
  kind: "paste" | "file";
  label: string;
  /** Paste-store id for paste chips. */
  pasteId?: number;
}

const PASTE_TOKEN = /\[Pasted #(\d+) \+(\d+) lines\]/g;
// A mention becomes a chip once it is finished (whitespace after it), so
// the one being typed stays editable text for the @file menu.
const FILE_MENTION = /(^|\s)@([^\s@]+)(?=\s)/g;

export function inlineChips(text: string): PreviewChip[] {
  const chips: PreviewChip[] = [];
  for (const m of text.matchAll(PASTE_TOKEN)) {
    const lines = Number(m[2]);
    chips.push({
      from: m.index, to: m.index + m[0].length, kind: "paste", pasteId: Number(m[1]),
      label: `Pasted · ${lines} ${lines === 1 ? "line" : "lines"}`,
    });
  }
  for (const m of text.matchAll(FILE_MENTION)) {
    const from = m.index + m[1].length;
    const path = m[2];
    const parts = path.replace(/\/$/, "").split("/");
    const name = parts.length > 2 ? `…/${parts.slice(-2).join("/")}` : path.replace(/\/$/, "");
    chips.push({ from, to: from + 1 + path.length, kind: "file", label: name + (path.endsWith("/") ? "/" : "") });
  }
  return chips.sort((a, b) => a.from - b.from);
}

export interface PreviewRanges {
  /** Inline styling over [from, to). */
  marks: { from: number; to: number; kind: PreviewKind }[];
  /** Line styling, keyed by the line's start offset. */
  lines: { from: number; kind: PreviewKind }[];
  /** Marker text to hide: [from, to). */
  hidden: { from: number; to: number }[];
}

const INLINE: Record<string, PreviewKind> = {
  StrongEmphasis: "strong",
  Emphasis: "em",
  Strikethrough: "strike",
  InlineCode: "code",
  Link: "link",
};
const MARKS = new Set(["EmphasisMark", "StrikethroughMark", "CodeMark", "LinkMark"]);

function touches(state: EditorState, from: number, to: number): boolean {
  return state.selection.ranges.some((r) => r.from <= to && r.to >= from);
}

export function livePreviewRanges(state: EditorState): PreviewRanges {
  const out: PreviewRanges = { marks: [], lines: [], hidden: [] };
  const doc = state.doc;
  // Composer text is short: parse it fully rather than to the viewport.
  (ensureSyntaxTree(state, state.doc.length, 50) ?? syntaxTree(state)).iterate({
    enter(node) {
      const name = node.name;
      const inline = INLINE[name];
      if (inline) {
        out.marks.push({ from: node.from, to: node.to, kind: inline });
        if (!touches(state, node.from, node.to)) {
          const cursor = node.node.cursor();
          if (cursor.firstChild()) {
            do {
              if (MARKS.has(cursor.name)) out.hidden.push({ from: cursor.from, to: cursor.to });
              if (name === "Link" && cursor.name === "URL") {
                out.hidden.push({ from: cursor.from, to: cursor.to });
              }
            } while (cursor.nextSibling());
          }
        }
        // Inline code contents are literal; don't descend into them.
        return name !== "InlineCode";
      }
      const heading = /^ATXHeading([1-6])$/.exec(name);
      if (heading) {
        const level = Number(heading[1]);
        const kind: PreviewKind = level === 1 ? "h1" : level === 2 ? "h2" : "h3";
        out.lines.push({ from: doc.lineAt(node.from).from, kind });
        if (!touches(state, node.from, node.to)) {
          const mark = node.node.getChild("HeaderMark");
          if (mark) {
            const after = doc.sliceString(mark.to, mark.to + 1) === " " ? mark.to + 1 : mark.to;
            out.hidden.push({ from: mark.from, to: after });
          }
        }
        return true;
      }
      if (name === "Blockquote") {
        for (let pos = node.from; pos <= node.to;) {
          const line = doc.lineAt(pos);
          out.lines.push({ from: line.from, kind: "quote" });
          pos = line.to + 1;
        }
        return true;
      }
      if (name === "FencedCode") {
        for (let pos = node.from; pos <= node.to;) {
          const line = doc.lineAt(pos);
          out.lines.push({ from: line.from, kind: "fence" });
          pos = line.to + 1;
        }
        return false;
      }
      if (name === "ListMark") {
        out.marks.push({ from: node.from, to: node.to, kind: "list" });
        return false;
      }
      if (name === "QuoteMark" || name === "URL") {
        out.marks.push({ from: node.from, to: node.to, kind: "url" });
      }
      return true;
    },
  });
  out.hidden.sort((a, b) => a.from - b.from || a.to - b.to);
  return out;
}
