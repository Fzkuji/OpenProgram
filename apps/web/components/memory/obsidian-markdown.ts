type MarkdownParser = (source: string) => string;

type Footnote = {
  content: string;
  number: number;
  referenceIds: string[];
};

type Fence = {
  containerDepth: number;
  indent: number;
  listItem: boolean;
  marker: string;
  trailing: string;
};

function indentWidth(value: string): number {
  let width = 0;
  for (const character of value) {
    width = character === "\t" ? width + 4 - (width % 4) : width + 1;
  }
  return width;
}

function matchFence(line: string): Fence | null {
  let rest = line;
  let containerDepth = 0;
  while (true) {
    const blockquote = rest.match(/^ {0,3}>[ \t]?/);
    if (!blockquote) break;
    rest = rest.slice(blockquote[0].length);
    containerDepth += 1;
  }

  const direct = rest.match(/^([ \t]*)(`{3,}|~{3,})(.*)$/);
  let indent: number;
  let listItem: boolean;
  let marker: string;
  let trailing: string;
  if (direct) {
    indent = indentWidth(direct[1]);
    listItem = false;
    marker = direct[2];
    trailing = direct[3];
  } else {
    const list = rest.match(/^ {0,3}(?:[*+-]|\d{1,9}[.)])[ \t]+/);
    if (!list) return null;
    const nested = rest.slice(list[0].length).match(/^(`{3,}|~{3,})(.*)$/);
    if (!nested) return null;
    listItem = true;
    indent = indentWidth(list[0]);
    marker = nested[1];
    trailing = nested[2];
  }
  if (marker[0] === "`" && trailing.includes("`")) return null;
  return {
    containerDepth,
    indent,
    listItem,
    marker,
    trailing,
  };
}

function stashCode(source: string): { source: string; restore: (value: string) => string } {
  const code: string[] = [];
  let nonce = 0;
  let prefix = "";
  do {
    prefix = `\uE000OPENPROGRAM-MEMORY-${nonce}:`;
    nonce += 1;
  } while (source.includes(prefix));
  const token = (index: number) => `${prefix}${index}\uE001`;
  const stash = (value: string) => {
    code.push(value);
    return token(code.length - 1);
  };
  const lines = source.split("\n");
  const withoutFences: string[] = [];
  for (let index = 0; index < lines.length; index += 1) {
    if (/^(?: {4}|\t)/.test(lines[index])) {
      let end = index + 1;
      let lastCode = index;
      while (end < lines.length) {
        if (/^(?: {4}|\t)/.test(lines[end])) {
          lastCode = end;
          end += 1;
          continue;
        }
        if (/^[ \t]*$/.test(lines[end])) {
          end += 1;
          continue;
        }
        break;
      }
      withoutFences.push(stash(lines.slice(index, lastCode + 1).join("\n")));
      index = lastCode;
      continue;
    }
    const opening = matchFence(lines[index]);
    if (!opening || (!opening.listItem && opening.indent > 3)) {
      withoutFences.push(lines[index]);
      continue;
    }
    let end = index + 1;
    while (end < lines.length) {
      const closing = matchFence(lines[end]);
      if (
        closing
        && closing.containerDepth === opening.containerDepth
        && !closing.listItem
        && closing.indent >= (opening.listItem ? opening.indent : 0)
        && closing.indent <= (opening.listItem ? opening.indent + 3 : 3)
        && closing.marker[0] === opening.marker[0]
        && closing.marker.length >= opening.marker.length
        && closing.trailing.trim() === ""
      ) break;
      end += 1;
    }
    withoutFences.push(stash(lines.slice(index, Math.min(end + 1, lines.length)).join("\n")));
    index = end;
  }

  const fenced = withoutFences.join("\n");
  let stashed = "";
  for (let index = 0; index < fenced.length;) {
    if (fenced[index] !== "`") {
      stashed += fenced[index];
      index += 1;
      continue;
    }
    let runEnd = index + 1;
    while (fenced[runEnd] === "`") runEnd += 1;
    const delimiter = fenced.slice(index, runEnd);
    let closing = fenced.indexOf(delimiter, runEnd);
    while (closing >= 0 && (fenced[closing - 1] === "`" || fenced[closing + delimiter.length] === "`")) {
      closing = fenced.indexOf(delimiter, closing + 1);
    }
    if (closing < 0) {
      stashed += delimiter;
      index = runEnd;
      continue;
    }
    const codeEnd = closing + delimiter.length;
    stashed += stash(fenced.slice(index, codeEnd));
    index = codeEnd;
  }
  return {
    source: stashed,
    restore: (value) => code.reduce(
      (restored, item, index) => restored.split(token(index)).join(item),
      value,
    ),
  };
}

function isEscaped(source: string, offset: number): boolean {
  let slashes = 0;
  for (let index = offset - 1; index >= 0 && source[index] === "\\"; index -= 1) {
    slashes += 1;
  }
  return slashes % 2 === 1;
}

function extractFootnotes(source: string): { body: string; definitions: Map<string, string> } {
  const definitions = new Map<string, string>();
  const body: string[] = [];
  const lines = source.split(/\r?\n/);

  for (let index = 0; index < lines.length; index += 1) {
    const definition = lines[index].match(/^\[\^([A-Za-z0-9][\w-]*)\]:[ \t]*(.*)$/);
    if (!definition) {
      body.push(lines[index]);
      continue;
    }

    const content = [definition[2]];
    while (index + 1 < lines.length) {
      const continuation = lines[index + 1].match(/^(?: {2,}|\t)(.*)$/);
      if (!continuation) break;
      content.push(continuation[1]);
      index += 1;
    }
    if (!definitions.has(definition[1])) {
      definitions.set(definition[1], content.join("\n").trimEnd());
    }
  }

  return { body: body.join("\n").trimEnd(), definitions };
}

function hideBlockIds(source: string): string {
  return source.replace(
    /(^|[ \t]+)((?:\^[A-Za-z0-9][A-Za-z0-9-]*[ \t]*)+)$/gm,
    (_match, prefix, value) => {
      const anchors = value.match(/\^[A-Za-z0-9][A-Za-z0-9-]*/g)?.map(
        (id: string) => `<span id="${id}" data-block-id aria-hidden="true"></span>`,
      ).join("") ?? "";
      return `${prefix ? " " : ""}${anchors}`;
    },
  );
}

/** Render the fixed Memory/Obsidian evidence format around an existing parser.
 * The caller remains responsible for sanitizing the returned HTML. */
export function renderObsidianMarkdown(source: string, parse: MarkdownParser): string {
  const stashed = stashCode(source);
  const { body, definitions } = extractFootnotes(stashed.source);
  const ordered: Footnote[] = [];
  const byLabel = new Map<string, Footnote>();

  const addReference = (footnote: Footnote) => {
    const occurrence = footnote.referenceIds.length + 1;
    const referenceId = occurrence === 1
      ? `fnref-${footnote.number}`
      : `fnref-${footnote.number}-${occurrence}`;
    footnote.referenceIds.push(referenceId);
    return `<sup id="${referenceId}"><a href="#fn-${footnote.number}" data-footnote-ref aria-label="Footnote ${footnote.number}">[${footnote.number}]</a></sup>`;
  };

  const withReferences = body.replace(
    /\[\^([A-Za-z0-9][\w-]*)\]|\^\[([^\]\r\n]+)\]/g,
    (
      raw,
      label: string | undefined,
      inline: string | undefined,
      offset: number,
    ) => {
      if (isEscaped(body, offset)) return raw;
      if (inline !== undefined) {
        const footnote = { content: inline, number: ordered.length + 1, referenceIds: [] };
        ordered.push(footnote);
        return addReference(footnote);
      }
      if (!label) return raw;
      const content = definitions.get(label);
      if (content === undefined) return raw;
      let footnote = byLabel.get(label);
      if (!footnote) {
        footnote = { content, number: ordered.length + 1, referenceIds: [] };
        ordered.push(footnote);
        byLabel.set(label, footnote);
      }
      return addReference(footnote);
    },
  );

  const main = parse(stashed.restore(hideBlockIds(withReferences)));
  if (ordered.length === 0) return main;

  const items = ordered.map((footnote) => {
    const backlinks = footnote.referenceIds.map((referenceId, index) => (
      `<a href="#${referenceId}" data-footnote-backref aria-label="Back to reference ${footnote.number}${index ? `.${index + 1}` : ""}">↩︎</a>`
    )).join(" ");
    const rendered = parse(stashed.restore(footnote.content));
    const withBacklinks = /<\/p>\s*$/.test(rendered)
      ? rendered.replace(/<\/p>\s*$/, `${backlinks}</p>`)
      : `${rendered}<p>${backlinks}</p>`;
    return `<li id="fn-${footnote.number}">${withBacklinks}</li>`;
  }).join("");

  return `${main}<section data-footnotes aria-label="Footnotes"><hr><ol>${items}</ol></section>`;
}
