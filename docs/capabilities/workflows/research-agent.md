# Research Agent

An autonomous research agent: take a research topic and walk the full pipeline of literature survey → idea generation → experiments → writing → review → rebuttal / presentation, producing a submission-ready paper. It does not trust its own output — every citation is verified against four indexes (Crossref / OpenAlex / Semantic Scholar / arXiv), every number in the paper must trace back to an experiment's `run_record.json`, and the review can run on a different model (author and reviewer use different models to avoid self-grading).

## Install

```bash
openprogram programs install research
```

No extra dependencies — the harness uses only OpenProgram itself. Optional: install `pymupdf` for PDF parsing (needed by `load_paper` / wiki ingest), and install the wiki harness for knowledge-base integration (`openprogram programs install wiki`).

## Usage

The entry function is **`research_agent`**, registered as a tool (`as_tool=True`, toolset `research`). In chat, just describe a research task to trigger it, e.g. "Survey recent work on LLM uncertainty".

Run it directly from the command line:

```bash
openprogram programs run research_agent -a task="Survey recent work on LLM uncertainty"
```

Internally it is a two-level controller. At the first level, the LLM chooses which research stage to enter (literature / idea / experiment / writing / review / rebuttal / presentation / theory / knowledge / project; stages have dependency ordering, and missing prerequisites are filled in first). At the second level, within a stage, the LLM picks and runs that stage's functions one by one. About 89 functions across 10 stages; each function is an ordinary Python file whose docstring is the prompt, directly editable.

Hidden entry parameters (available to code / CLI callers):

| Parameter | Description |
|---|---|
| `review_runtime` | When provided, review functions run on a different model (cross-model review) |
| `work_dir` | Project working directory |
| `max_runtime_s` | Soft time budget: after the deadline no new work starts; running steps finish and wrap up normally |
| `stop_event` | Graceful stop signal (any object with `is_set()`); wraps up after the current step finishes |

The return value is a dict: `task`, `success`, `summary`, `stages_completed`, `history`.

## Dependency notes

- Citation verification, uncited-claim checks, citation-dump checks, and similar verifications are pure Python / regex — no extra tokens; `integrity_gate` uses a single bounded LLM call.
- Online retrieval (arXiv / Semantic Scholar, etc.) requires network access; LaTeX compilation requires a local TeX distribution.

Source and README: `openprogram/functions/agentics/Research-Agent-Harness/`, upstream repository [Fzkuji/Research-Agent-Harness](https://github.com/Fzkuji/Research-Agent-Harness).
