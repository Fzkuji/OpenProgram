// Markdown surfaces call this after every render. The diagram code (and
// mermaid itself) loads only when a ```mermaid block is actually present.

export function renderMermaidLazily(root: ParentNode): void {
  if (!root.querySelector(".md-code:not([data-mermaid]) > pre > code.language-mermaid")) return;
  void import("./mermaid").then(({ renderMermaidIn }) => renderMermaidIn(root));
}
