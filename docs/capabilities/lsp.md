# Language server tools

Three tools let the model ask a language server what a type checker knows: which errors a file has right now, where a symbol is really defined, and every place it is actually used. The answers come from the working tree as it currently stands, so they are correct the moment an edit lands, without running the test suite.

## Install a server

The tools are registered whether or not a server is installed. Without one they answer `unavailable: install …` and name the command, so nothing crashes and the model learns what is possible.

```bash
npm install -g pyright                                    # Python
npm install -g typescript-language-server typescript      # TypeScript / JavaScript
```

| Language | File extensions | Server binary |
|---|---|---|
| Python | `.py`, `.pyi` | `pyright-langserver` |
| TypeScript / JavaScript | `.ts`, `.tsx`, `.js`, `.jsx`, `.mts`, `.cts` | `typescript-language-server` |

A file whose extension is in neither row gets a result saying so.

## The tools

| Tool | Arguments | Answers |
|---|---|---|
| `lsp_diagnostics` | `file` | Errors and warnings for that file |
| `lsp_references` | `file`, `line`, `column` | Every use of the symbol at that position |
| `lsp_definition` | `file`, `line`, `column` | Where that symbol is declared |

`file` is an absolute path. `line` and `column` are 1-based, matching what `read` prints and what a traceback shows, and must point at the symbol itself rather than at the start of the line.

`lsp_diagnostics` returns one finding per line:

```
runner.py: 2 diagnostics
14:5 warning: "requests" is not accessed [pyright]
27:12 error: "widget" is not defined [pyright]
```

`lsp_references` and `lsp_definition` return `path:line:column` followed by the source text, with paths relative to the workspace root:

```
3 references
openprogram/agent/loop.py:88:9   result = dispatch(call)
openprogram/agent/loop.py:140:5  dispatch(retry)
tests/unit/test_loop.py:31:11    monkeypatch.setattr(loop, "dispatch", fake)
```

Long results stop at 50 entries with a count of what was left out; narrow the question rather than paging through.

## How servers run

One server per language per workspace. The workspace is the nearest ancestor directory holding `pyproject.toml`, `setup.py`, `package.json`, `tsconfig.json` or `.git`, and two files under the same root share one server process.

A server starts on first use, stays cached for the life of the process, and shuts down when OpenProgram exits. Server processes start through the same path as every other child OpenProgram runs, so the configured [sandbox](../reference/design/runtime/sandbox.md) applies to them: under the default `workspace-write` mode a language server reads the workspace and writes nothing outside it, which is all it needs.

## Compared to grep and CodeGraph

`grep` matches text, so it reports the same-named string in a comment and misses a call that goes through an alias. `lsp_references` resolves symbols and returns neither kind of mistake.

CodeGraph is a pre-built index of a whole codebase, best for reading unfamiliar code from the top down. Language servers add what an index cannot: live diagnostics against the file as it sits on disk right now.
