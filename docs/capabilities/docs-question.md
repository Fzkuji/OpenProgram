# Asking about OpenProgram itself

"Can OpenProgram do X?" and "how do I configure Y?" are questions this documentation site already answers, and a model answering them from memory of some other agent product is how a confident wrong answer reaches you. `run_docs_question` answers such a question by reading these pages: it spawns one agent that can only read, works only inside the repository's `docs/` tree, and reports which pages the answer came from.

Run it as `run_docs_question` in the Programs panel, or from Python:

```python
from openprogram.programs.functions.agentic.docs_question import run_docs_question

result = run_docs_question("Can I keep a session working until a condition holds?")
```

## What it reads

The whole documentation tree, and nothing else. The agent gets four tools — `read`, `grep`, `glob`, `list` — so it cannot write, edit, or run a shell, and its prompt pins its working scope to the `docs/` directory it is given. It also cannot see the conversation it was spawned from: the question is its entire brief, so the answer comes from the pages rather than from what the session happened to be discussing.

It does not start by opening files. The prompt carries a listing of every English page with its title, so the agent picks candidates by path and title first and only then reads them. English pages (`xxx.md`) are authoritative; the `xxx.zh.md` beside each one is consulted for a Chinese question or an ambiguous sentence, and when the two disagree the English page is what gets reported.

Generated reference pages (`reference/cli/`, `reference/config-keys.md`, `reference/provider-registry.md`) are build output, so a checkout that has not built the site yet simply has fewer pages in the listing. Nothing fails because of it.

## Three answers, not two

The question "does OpenProgram support X?" has three honest answers, and they are kept apart:

| `covered` | The answer |
|---|---|
| `true` | The documentation answers the question. The answer quotes the command, setting key, or path as the page writes it. |
| `true` | The documentation says the thing is **not** supported, or documents behaviour different from what the question assumed. That is a documented fact, cited like any other. |
| `false` | The documentation does not mention it at all. The answer says so, and points at the closest related pages so you know where the topic would live. |

The third case is the one worth having. "The documentation does not cover this" is a real answer; guessing whether a feature exists is not, and the prompt forbids filling that gap from general knowledge about agent products.

## What comes back

```python
{"answer": "…", "sources": ["capabilities/goal.md"], "covered": True}
```

`sources` are paths relative to `docs/`, deduplicated, in the order cited. A cited path that is not an existing page is dropped, so an invented page name never reaches you as a citation. When `covered` is `false`, `sources` holds the nearest related pages the agent did find rather than the pages that answered the question.

An empty question is rejected before any agent runs, and a reply the agent cannot express as this structure raises rather than returning a half-answer.

## Where the answers come from

Everything under [Overview](README.md) and the other tabs of this site. If an answer is wrong, the page it cites is what needs fixing — the function reports the documentation, it does not read the code.
