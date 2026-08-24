# Related projects

Writing agents as ordinary typed Python — where the **docstring is the prompt** and the **signature is the contract** — is an idea several groups have arrived at independently. We think that convergence is the strongest evidence the direction is right, and the differences between these designs are where the interesting questions live.

| Project | The shared intuition | Where it goes its own way |
|---|---|---|
| [**NVIDIA NOOA**](https://github.com/NVIDIA-NeMo/labs-OO-Agents) (Apache-2.0) | Agents are Python objects; methods with `...` bodies are LLM-implemented, docstrings are prompts, type annotations are contracts. | Object-oriented: state lives on `self`, and the model **acts by writing Python into a Jupyter-style REPL** (CodeAct). OpenProgram keeps functions module-level and has the model **choose among registered functions** instead of emitting code — a narrower action space that's easier to sandbox and replay. |
| [**DSPy**](https://github.com/stanfordnlp/dspy) (MIT) | A typed **Signature** replaces the hand-written prompt; the framework compiles it. | Optimizes the prompt itself against a metric. We leave prompts fixed and readable, and put the effort into execution structure — the DAG, retries, and context scoping. The two are complementary. |
| [**Marvin**](https://github.com/PrefectHQ/marvin) (Apache-2.0) · [**Mirascope**](https://github.com/Mirascope/mirascope) (MIT) | Decorate a Python function, let the docstring and return annotation drive a structured LLM call. | Focused on the single well-typed call. OpenProgram adds what happens **across** calls: a shared execution DAG, `spawn`, forking, and per-call context budgets. |
| [**LangGraph**](https://github.com/langchain-ai/langgraph) (MIT) | Agent runs should be an inspectable graph with checkpoints, not an opaque loop. | The graph is declared up front as nodes and edges. Ours is **recorded from the call stack** — you write plain Python, and the DAG is the trace of what actually ran. See also [OpenProgram vs LangGraph, AutoGen, and CrewAI](ai-agent-frameworks.md). |
| [**smolagents**](https://github.com/huggingface/smolagents) (Apache-2.0) | Let the model act through code rather than rigid tool JSON. | Code-writing agents in a sandbox, like NOOA. We take the same "code is the action language" premise but bind it at **authoring** time via `@agentic_function`, so the deterministic parts are reviewable before anything runs. |
| [**Scriptorium**](https://github.com/Fzkuji/Scriptorium) | Agent memory you can read; Markdown notes; facts cited to source messages; MCP for Claude Code. | A memory the model writes as ordinary files, so you can open, diff, and trace every fact back to the message it came from. |

If you're building in this space and we've mischaracterized your project — or missed it — please open a PR or an issue. We're happy to be corrected.

## Acknowledgements

OpenProgram stands on shoulders. The tool framework, provider abstraction, and
several tool implementations were ported or adapted from the projects below —
each under its own license. Enormous thanks to their authors.

- [**OpenClaw**](https://github.com/openclaw/openclaw) (MIT) — layout of the
  tool registry (`name / description / parameters / execute`), provider
  abstraction with `check_fn` + `requires_env` gating, `TOOLSETS` presets,
  skill loading via SKILL.md frontmatter + late-bound `read`. Our full clone
  lives under `references/openclaw/` (gitignored) for browsing.
- [**hermes-agent**](https://github.com/himanshuishere/hermes-agent)
  (MIT) — starting point for `execute_code` (we trimmed the
  Docker / Modal layers), `mixture_of_agents`, and the general shape of the
  multi-provider `web_search` / `image_generate` / `image_analyze` tools.
- [**pi-coding-agent**](https://github.com/mariozechner/pi-coding-agent)
  (MIT) — via OpenClaw's import, the canonical AgentSkill shape
  (`<available_skills>` XML formatter, name / description / location).
- [**Claude Code**](https://www.anthropic.com/claude-code) — overall ergonomics
  of the `DEFAULT_TOOLS` set (bash + read / write / edit + glob / grep / list
  + apply_patch + the todo planning board) and the todo tools' JSON schema.
- **Anthropic / OpenAI / Google SDKs** — the wire contracts, and the clients
  the first-party providers stream through. All three ship as base
  dependencies; the CLI-backed and OAuth providers talk raw HTTP instead.

Individual tool files call out their direct inspirations in file-level
docstrings where the lineage is more specific. These MIT-licensed components
keep their original MIT terms; the combined work is distributed under
AGPL-3.0.

## Contributing

This is a **paradigm proposal** with a reference implementation. We welcome
discussions, alternative implementations in other languages, use cases that
validate or challenge the approach, and bug reports.

Setup, tests, and pull-request expectations live in
[CONTRIBUTING.md](https://github.com/Fzkuji/OpenProgram/blob/main/.github/CONTRIBUTING.md).
