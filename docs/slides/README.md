# OpenProgram intro deck

`openprogram-intro.html` — a self-contained 9-slide introduction to OpenProgram.

Built on the [Oh My PPT](https://github.com/arcsin1/oh-my-ppt) layout conventions
(16:9 HTML slides, one message per slide, grid/flex flow, varied title placement),
hand-authored as a single file so it runs anywhere with no toolchain.

## View it

Just open the file in a browser:

```bash
open docs/slides/openprogram-intro.html        # macOS
```

Image references are relative (`../images/…`), so opening over `file://` works too —
if a browser blocks local images, serve the repo root instead:

```bash
python3 -m http.server 8000
# → http://localhost:8000/docs/slides/openprogram-intro.html
```

## Controls

| Key | Action |
|---|---|
| `→` / `Space` / click right half | Next slide |
| `←` / click left half | Previous slide |
| `Home` / `End` | First / last slide |
| `F` | Toggle fullscreen |
| `P` | Print → export to PDF (one slide per page) |

The URL hash tracks the current slide (`…#5`), so you can deep-link or refresh in place.

## Slides

1. Cover — OpenProgram, any LLM / any platform / self-evolving
2. The problem — LLM is flexible, code is deterministic; why a harness
3. The idea — Agentic Programming
4. Five core capabilities
5. Skills vs Agentic Workflow (the README diagram)
6. Quick start — four steps
7. The harness suite — GUI / Research / Wiki
8. Web UI — conversation as a git DAG
9. Closing + links

## Export to PPTX

This deck is HTML. To get an editable `.pptx`, open it in the Oh My PPT desktop app
(File → import HTML) and use its export, or print to PDF (`P`) and convert the PDF.
