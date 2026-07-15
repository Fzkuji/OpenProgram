# Wiki Agent

A personal knowledge-base agent: it pours sessions and notes into a template-driven HTML wiki. Pages are real HTML files — open them in any browser, host them statically, version them with git. The agent only fills named slots in fixed templates and never hand-writes HTML / CSS. It ships with full-text search, automatic folder indexing (each folder's `README.html` is regenerated from its current contents), and a curating ingest pipeline.

## Install

```bash
openprogram programs install wiki
```

Dependencies are minimal: Jinja2 + PyYAML. The template shell uses Jinja2 + Bootstrap 5 (loaded from a CDN) — zero build steps, no Node required.

## Usage

The entry function is **`wiki_agent`**. In chat, describe what you want done to the wiki in natural language, for example:

- "ingest these notes about transformers" (pour the conversation above into the wiki)
- "enrich the Methodology landing page"
- "browse the vault"
- "check for broken links"
- "find pages about distillation"

Run it directly from the command line:

```bash
openprogram programs run wiki_agent -a task="Ingest the notes above into the wiki"
```

Function signature: `wiki_agent(task, vault="", purpose="", audience="", ...)`. It is a dispatcher: using a next-step decision (`decision.make`) it routes the task to one of five internal operations — **ingest / enrich / browse / lint / search** — where each branch handler is plain Python calling `wiki_agent_harness.Wiki`. `vault` (the knowledge-base root), `purpose`, and `audience` are hidden parameters that do not appear in the chat tool form; when `vault` is not given it falls back to the runtime's working directory.

The harness itself stays deliberately loose: it provides only five things — the rendering layer, slot primitives, full-text search, automatic folder indexing, and the ingest pipeline. Everything else (moving / deleting / grepping / editing) the agent does with ordinary shell and file tools. A slot is a region of a page delimited by HTML comments; the agent edits one slot at a time, so repeated ingests accumulate cleanly without rewriting whole pages.

## Dependency notes

- Works without openprogram: with OpenProgram not installed, the pure-Python `Wiki` class and the CLI still work as usual — you just lose the `wiki_agent` chat entry point.
- Downstream projects (paper surveys, memory stores, CRMs, etc.) specialize the ingest pipeline by supplying their own templates and prompts — no fork needed.

Source and README: `openprogram/functions/agentics/Wiki-Agent-Harness/`, upstream repository [Fzkuji/Wiki-Agent-Harness](https://github.com/Fzkuji/Wiki-Agent-Harness).
