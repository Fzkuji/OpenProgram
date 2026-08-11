# OpenProgram Homepage Optimization Design

## Goal

Turn the existing dark landing page into a concise product homepage that explains Agentic Programming, shows the real OpenProgram interfaces, and directs developers to installation, documentation, GitHub, and the paper.

## Audience and primary action

The audience is Python developers evaluating agent harnesses. The primary action is starting the documented installation flow; documentation and GitHub are secondary actions.

## Visual direction

Keep the selected near-black identity and teal-to-violet accent. Use the bundled Inter font for prose and the system monospace stack for code. The signature element remains the annotated `@agentic_function` example; real Web, code, and TUI screenshots provide product evidence below it. Decoration stays limited to the hero ambient gradient and code highlighting.

## Information structure

1. Sticky navigation with Docs, GitHub, Paper, and Install.
2. Hero with the existing thesis, accurate installer command, and annotated code.
3. A compact evidence strip for open source, Python version, platforms, and workshop paper.
4. “How Agentic Programming works” mapping docstring, parameters, `runtime.exec`, and return values to their runtime roles.
5. Three mechanism cards for DAG Context, Agentic Workflow, and Event Infrastructure.
6. Real product surfaces using existing Web UI, code, and TUI screenshots.
7. Installation block using the documented installer, followed by Docs and GitHub links.
8. Footer with license, paper, repository, and sitemap links.

## Search and sharing

- Keep the canonical URL and build-generated sitemap.
- Add favicon, Open Graph image, image dimensions, Twitter metadata, theme color, and `SoftwareSourceCode` JSON-LD.
- Use specific headings and prose for Python agent harness, agentic programming, DAG context, multi-agent workflows, providers, tools, skills, memory, Web UI, and terminal UI.
- Keep claims bounded to the current README and documentation; retain the theoretical label on token-complexity text.

## Accessibility and resilience

- Content is never hidden by JavaScript or CSS; motion is limited to the ambient hero gradient.
- Preserve keyboard focus, semantic landmarks, descriptive image alt text, and reduced-motion behavior.
- Mobile navigation wraps cleanly, screenshots remain legible, and code regions scroll horizontally.
- No external font, analytics, animation, or frontend dependencies.

## Verification

- A stdlib check validates required metadata, sections, documented install command, visible content, and image references.
- The docs build and link checker remain green.
- Desktop and mobile screenshots are reviewed from the built local page.
- After deployment, root HTML, metadata, screenshots, sitemap, and Google verification are fetched from the live site.
