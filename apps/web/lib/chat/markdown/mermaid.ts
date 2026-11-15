// ```mermaid fences render as diagrams inside their code card. The card
// keeps its header and copy button (which still copies the source), and a
// header button switches between the diagram and the source. A block that
// does not parse, including one still streaming, stays plain code.
// mermaid loads on first use, so chats without diagrams never fetch it.

import { translateText } from "@/lib/i18n";

type Mermaid = typeof import("mermaid")["default"];

const rendered = new Map<string, string | null>();
const RENDERED_MAX = 100;
let loader: Promise<Mermaid> | null = null;
let configuredFor = "";
let sequence = 0;

/** Paint a CSS colour into one pixel and read it back as hex: mermaid's
 *  colour maths cannot parse oklch() or color-mix(), which themes use. */
function toHex(color: string, fallback: string): string {
  try {
    const canvas = document.createElement("canvas");
    canvas.width = canvas.height = 1;
    const ctx = canvas.getContext("2d", { willReadFrequently: true });
    if (!ctx) return fallback;
    ctx.fillStyle = fallback;
    ctx.fillStyle = color;
    ctx.fillRect(0, 0, 1, 1);
    const [r, g, b] = ctx.getImageData(0, 0, 1, 1).data;
    return "#" + [r, g, b].map((v) => v.toString(16).padStart(2, "0")).join("");
  } catch {
    return fallback;
  }
}

/** Mermaid's base theme, coloured from the app's current theme tokens. */
function themeVariables() {
  const probe = document.createElement("span");
  probe.style.display = "none";
  document.body.appendChild(probe);
  const read = (token: string, fallback: string) => {
    probe.style.color = fallback;
    probe.style.color = `var(${token}, ${fallback})`;
    return toHex(getComputedStyle(probe).color, fallback);
  };
  const vars = {
    darkMode: getComputedStyle(document.documentElement).colorScheme.includes("dark"),
    background: read("--bg-primary", "#ffffff"),
    primaryColor: read("--bg-secondary", "#f4f4f5"),
    primaryTextColor: read("--text-primary", "#18181b"),
    primaryBorderColor: read("--border", "#d4d4d8"),
    lineColor: read("--text-secondary", "#52525b"),
    secondaryColor: read("--bg-tertiary", "#e4e4e7"),
    tertiaryColor: read("--bg-primary", "#ffffff"),
    fontFamily: getComputedStyle(document.body).fontFamily,
    fontSize: "14px",
  };
  probe.remove();
  return vars;
}

async function mermaid(): Promise<Mermaid> {
  loader ??= import("mermaid").then((m) => m.default);
  const instance = await loader;
  const vars = themeVariables();
  const key = JSON.stringify(vars);
  if (key !== configuredFor) {
    instance.initialize({ startOnLoad: false, securityLevel: "strict", theme: "base", themeVariables: vars });
    configuredFor = key;
    rendered.clear();
  }
  return instance;
}

async function renderSource(source: string): Promise<string | null> {
  const instance = await mermaid();
  if (rendered.has(source)) return rendered.get(source)!;
  let svg: string | null = null;
  try {
    if (await instance.parse(source, { suppressErrors: true })) {
      svg = (await instance.render(`op-mermaid-${++sequence}`, source)).svg;
    }
  } catch {
    svg = null;
  }
  if (rendered.size >= RENDERED_MAX) rendered.delete(rendered.keys().next().value!);
  rendered.set(source, svg);
  return svg;
}

function toggleLabel(showingCode: boolean) {
  return showingCode ? translateText("Diagram", "图表") : translateText("Code", "源码");
}

function attach(card: HTMLElement, svg: string) {
  const pre = card.querySelector<HTMLElement>(":scope > pre");
  const head = card.querySelector<HTMLElement>(":scope > .md-code-head");
  if (!pre || !head) return;
  const figure = document.createElement("div");
  figure.className = "md-mermaid";
  figure.setAttribute("role", "img");
  figure.setAttribute("aria-label", translateText("Mermaid diagram", "Mermaid 图表"));
  figure.innerHTML = svg;
  pre.before(figure);
  pre.hidden = true;
  const toggle = document.createElement("button");
  toggle.type = "button";
  toggle.className = "md-mermaid-toggle";
  toggle.textContent = toggleLabel(false);
  toggle.setAttribute("aria-pressed", "false");
  head.querySelector(".md-code-copy")?.before(toggle);
  card.dataset.mermaid = "diagram";
}

if (typeof document !== "undefined") {
  document.addEventListener("click", (event) => {
    const target = event.target as Element | null;
    const toggle = typeof target?.closest === "function" ? target.closest<HTMLElement>(".md-mermaid-toggle") : null;
    const card = toggle?.closest<HTMLElement>(".md-code");
    if (!toggle || !card) return;
    const showCode = card.dataset.mermaid !== "code";
    card.dataset.mermaid = showCode ? "code" : "diagram";
    card.querySelector<HTMLElement>(":scope > pre")!.hidden = !showCode;
    card.querySelector<HTMLElement>(":scope > .md-mermaid")!.hidden = showCode;
    toggle.textContent = toggleLabel(showCode);
    toggle.setAttribute("aria-pressed", String(showCode));
  });
}

/** Turn every not-yet-seen ```mermaid card under `root` into a diagram. */
export function renderMermaidIn(root: ParentNode | null | undefined): void {
  if (!root || typeof document === "undefined") return;
  root.querySelectorAll<HTMLElement>(".md-code:not([data-mermaid]) > pre > code.language-mermaid").forEach((code) => {
    const card = code.closest<HTMLElement>(".md-code");
    if (!card) return;
    card.dataset.mermaid = "pending";
    const source = code.textContent ?? "";
    void renderSource(source).then((svg) => {
      // Streaming may have replaced the card while mermaid worked.
      if (!card.isConnected || card.dataset.mermaid !== "pending") return;
      if (svg) attach(card, svg);
      else card.dataset.mermaid = "source";
    }, () => {
      if (card.dataset.mermaid === "pending") card.dataset.mermaid = "source";
    });
  });
}
