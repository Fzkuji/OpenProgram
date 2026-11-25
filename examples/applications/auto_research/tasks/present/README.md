# present tasks

The `paper_agent` and `talk_prep` tasks of auto_research (`tasks/present/`). `paper_agent` wraps a paper's method as an MCP server whose tools are tested against the paper's own results, as Paper2Agent does. `talk_prep` lists the questions a talk's audience will ask with answers the paper backs, as talk and defense rehearsal tools do.

## paper_agent

| Item | Contract |
| --- | --- |
| Purpose | Another agent can call the paper's method through MCP, and each tool is shown to reproduce a result the paper reports. |
| Input | `paper_dir`: the project's Git repository. `code_dir`: the code repository; empty uses `rules.json`'s `code.repo` when it is a local folder, else `paper_dir`. `out_dir`: the output folder (default `<paper_dir>/drafts/paper_agent`). Not a Git repository, a `code_dir` that is not a folder, code without a public top-level function, or no result to cite (no kept run with a number in `workflow/results.tsv` and no `workflow/evidence.csv` row with a number) raises `ValueError`. |
| Gather | `experiment.md`'s Command and Metric, the first `.py` file the Command names (12k characters), the code's README (8k), and every public top-level function with a docstring (signature and docstring), up to 40k characters in all. Folders `tests`, `workflow`, `drafts`, virtualenvs, hidden folders and `test_*.py` are skipped. Citable results: `results:<commit[:12]>` for each kept run with a metric, `evidence:<id>` for each evidence row whose statement holds a number. |
| Check | A tool is dropped when its name is not lower_snake_case or repeats, its `module.function` is not a top-level (non-async) function of the code, a parameter is not the function's (unless it takes `**kwargs`), a default is not JSON, or a required parameter of the function is not exposed. A test is dropped (the tool kept) when its arguments are not a JSON object of exposed parameters with every required one, it cites an unlisted result, or its expected number is not a number of the cited result within the tolerance (experiment.md's Tolerance, else 1%). |
| Output | JSON with `status`, `report` (README.md), `server`, `run`, `tools`, `passed`, `failed`, `dropped`. `FAIL`: a test fails or errors, no tool is left, or a generated file does not compile. `PASS_WITH_NOTES`: the tests could not run (the modules do not import, a timeout), a tool or test was dropped, no test is left, or the interpreter lacks the MCP SDK. `PASS`: every tool's test reproduces its result. Files in `<out_dir>/<timestamp>/`: `server.py` (`FastMCP` from the official MCP Python SDK, one `@mcp.tool` per tool importing the wrapped function lazily, stdio), `tests/test_tools.py` (calls each wrapped function directly, picks `output_key` from the return value and compares with the expected value; runs as a script printing one JSON line, or under pytest), `tests/test_tools.log`, `tools.json`, `agent.json`, `README.md` (tools, `claude mcp add` command, generic `mcpServers` stdio JSON, test results, drops), `run.json`, `run.md`. |
| Effects | Runs the code's interpreter (`<code_dir>/.venv/bin/python` when present, else the host's) in subprocesses with timeouts: an import probe of the MCP SDK, an import probe of the wrapped modules (120 s), then the tests (900 s), all with the code folder as working directory. The code and the paper are never modified. |
| Model calls | `toolsmith`: one call. One to five tools with name, description, module, function, parameters (name, type, default as JSON text, description) and one test (arguments as JSON text, output_key, expected, source). |
| Failure | A reply that is not a list of one to five tools raises. Everything else is a drop or a note. Rerunning is safe. |
| Tests | `tests/present/test_agent.py`: a tiny code repository with one kept result and one evidence row; drops of each kind; the tests run and pass, fail on a changed result, and are skipped on a missing dependency; the server compiles and, when `mcp` is installed, lists and calls its tool. |
| Usage | `from auto_research.tasks.present import paper_agent` |

## talk_prep

| Item | Contract |
| --- | --- |
| Purpose | The speaker knows the 15 questions most likely to come, and which of their answers the paper already backs. |
| Input | `paper_dir`: the paper's Git repository; `workflow/rules.json` names the main file and the deliverables. `minutes`: the talk's length, empty or 1 to 180. `out_dir`: the output folder (default `<paper_dir>/drafts/talk_prep`). `audio`: `no` (default) or `yes`. A missing repository or main file, minutes out of range or another audio value raises `ValueError`. |
| Gather | The flattened LaTeX by section (100k characters), the weaknesses of the newest `drafts/paper_review/*/review.json`, the works of `workflow/watch.csv` judged `overlap` or `related` (overlap first, at most 20), the rows of `evidence.csv`, and the slides: the first existing pptx or pdf deliverable, one whose name or file name mentions slides first. A pptx is read slide by slide from `ppt/slides/slideN.xml` with zipfile; a pdf page by page with `pdftotext`. |
| Check | Questions: exactly 15, every kind (clarifying, methodological, comparison, limitations, so_what) at least once, a named work must be one of watch.csv's, and a comparison must name one when watch.csv has any; they are sorted by kind, keeping the asker's order within a kind, and numbered Q1 to Q15. Answers: every question once. An answer is flagged "needs your answer" when neither a quote of at least four words occurring in the paper (whitespace, `~` and case ignored) nor a known evidence id backs it, when its quote is not in the paper or its evidence id unknown, or when it has a number (two or more digits or a decimal point) that is not in the paper or the evidence. Over 75 words (30 seconds) is marked too long. With `minutes`: a note when the slide count is above `1.25 × minutes + 1` or below `0.6 × minutes`, and a list of slides with more than 50 words. |
| Output | JSON with `status`, `report` (qa.md), `audio`, `run`, `questions`, `needs_answer`, `too_long`, `slides`. `PASS_WITH_NOTES`: an answer is flagged or too long, a timing note, or another note (slides unreadable, no slides to time, no `say`). `PASS` otherwise. Files in `<out_dir>/<timestamp>/`: `qa.md` (Chinese headings, English questions and answers by kind, each with its quote and section or evidence row, and the flags), `qa.json`, `qa.aiff` when asked, `run.json`, `run.md`. |
| Effects | Reads the paper, the newest review, watch.csv, evidence.csv and the slides. `pdftotext` and `say` run with timeouts. With `audio=yes` and macOS `say`, the questions alone ("Question n. ...", 8 seconds of silence after each) go to `qa.aiff`; without `say` it is a note. The paper sources are never modified. |
| Model calls | `asker`: one call, 15 questions with kind and the competing work's id. `answerer`: one call, an answer of at most 75 words for each with a quote and an evidence id. |
| Failure | A malformed reply, the wrong number of questions, a missing kind, an unknown work, a comparison without a work, or a question answered twice or not at all raises. Rerunning is safe. |
| Tests | `tests/present/test_talk.py`: scripted roles, a pptx fixture built with zipfile, each flag, the ordering, the timing, the audio with `say` replaced and missing, and a pdf read by `pdftotext` when installed. |
| Usage | `from auto_research.tasks.present import talk_prep` |

## Not covered

- Registering the two tasks in the program's task list and app.
- Tools whose wrapped function is async, or whose output is not a number at a key (the test compares one number).
- Making or editing slides; checking the claims of an answer that has no number beyond its citation.
