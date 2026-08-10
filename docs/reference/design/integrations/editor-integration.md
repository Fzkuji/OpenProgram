# Editor integration: LSP tools and ACP server

Settled 2026-08-10 with the owner. Of the editor-integration gap rows in the
feature matrix, two are worth building, one is a matrix re-audit, five are
deliberate non-goals.

## LSP tools (first)

The beneficiary is the agent, not an editor. OpenProgram runs language
servers as background analysis processes and exposes their answers as agent
tools:

- `lsp_diagnostics(file)` — compiler-grade errors and warnings for a file,
  available the moment the agent finishes an edit, without running tests.
- `lsp_references(file, line, column)` — every real call site of a symbol;
  grep misses dynamic calls and false-positives on same-named strings.
- `lsp_definition(file, line, column)` — the symbol's true location across
  files and packages.

Scope: Python (pyright) and TypeScript (typescript-language-server) — the two
languages this repo actually contains. One server per language per workspace,
started on first use, cached, shut down with the session. A missing server
binary degrades to a clear "unavailable: install X" tool result, never a
crash; the tools stay registered so the model learns what is possible.

Relation to CodeGraph: CodeGraph is the pre-built symbol index for
understanding a codebase; LSP adds what an index cannot — live diagnostics
against the current unsaved state of the working tree.

## ACP server (second)

ACP (Agent Client Protocol) is the editor-agnostic standard for editors
driving an external agent. Implementing the server side maps three matrix
rows at once: editors like Zed drive OpenProgram sessions directly
(被IDE经ACP驱动), the editor ships selection and open-file context with each
request (编辑器选区与打开文件进上下文), and the editor becomes an entry
point without any self-built extension (官方IDE扩展, partially). One stdio
protocol adapter over the existing session/tool loop. Starts after LSP lands.

## Matrix re-audit

桌面GUI控制 is marked absent, but GUI-Agent-Harness is exactly that
capability, installed as a program. Re-audit the row against the matrix's
own evidence rules (installed-harness capability vs built-in).

## Non-goals

Self-built IDE extension (ACP replaces it), inline completion (a different
product), inline chat / CodeLens / status-bar actions (depend on a self-built
extension), terminal shortcut auto-configuration (return too small). These
rows stay open in the matrix by choice.

## Implementation status

LSP tools have landed. The client lives in `openprogram/lsp/` (JSON-RPC over
stdio, one server per language per workspace, started on first use and shut
down at exit); the three tools live in `openprogram/functions/tools/lsp/` and
are documented for users in [Language server tools](../../../capabilities/lsp.md).
Server processes go through the same sandbox-wrapping entry as every other
child, so `sandbox.mode` applies to them unchanged.

Still open, in order: ACP server, then the matrix re-audit of the GUI row.
