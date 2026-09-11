# Open applications from the new tab page

An installed application appears beside Files, New chat, Browser and Terminal.
Click its name to open its own interface in a tab. Opening it again selects the
same instance. Closing the tab does not cancel its operations.

Applications contain standard HTML, CSS and JavaScript, optionally backed by
Python operations. Their interface does not have to use OpenProgram components.
Bundle frontend dependencies locally and use relative asset URLs, including
relative JavaScript module imports. Remote scripts, direct owner API access,
nested frames and form navigation are blocked. Web UI code runs in a sandboxed
iframe with an opaque origin. A resource URL authorizes reads of only that
application version's UI directory; it is not an owner credential.

## Install and open

With the local OpenProgram worker running:

```bash
openprogram programs apps install /absolute/path/to/application
openprogram programs apps list
```

Python backends execute trusted local code with your operating-system account.
Review their source and dependencies, then pass `--trust` to authorize them:

```bash
openprogram programs apps install /absolute/path/to/application --trust
```

The new tab page refreshes its application list when focused and every ten
seconds. Project-scoped applications use the current selected project when
opened; changing the conversation project does not rebind an existing instance.
With no current project, a single saved binding is reused. First use or multiple
saved bindings opens a project chooser, which also accepts a new folder.
Global applications share one instance for the current owner. Project instances
have separate business data.

See the [calculator](https://github.com/Fzkuji/OpenProgram/tree/main/examples/applications/calculator),
[paper reader](https://github.com/Fzkuji/OpenProgram/tree/main/examples/applications/reader)
and [file analysis](https://github.com/Fzkuji/OpenProgram/tree/main/examples/applications/file-analysis)
examples. The reader uses your configured model only when Summarize is clicked.
Saving notes does not require a model.

## Application package

Place `application.json` in the package root:

```json
{
  "id": "local.notes",
  "title": "Notes",
  "version": "1.0.0",
  "scope": "global",
  "dataSchema": 1,
  "ui": {"root": "ui", "entry": "index.html"},
  "capabilities": ["storage.app", "model.invoke"],
  "backend": {"kind": "python", "entry": "backend:operations"},
  "operations": {
    "summarize": {
      "agent": true,
      "input": {"type": "object"},
      "output": {"type": "string"}
    }
  }
}
```

`ui.root` is relative to the package; `ui.entry` is relative to that directory.
Keep Python and private configuration outside `ui.root`. The optional backend
exports a dictionary of callables. Each callable receives `(input, context)` and
returns a JSON value, synchronously or asynchronously. Input and output use JSON
Schema. Only operations with `agent: true` are callable through the Program client.

Supported capabilities are `storage.app`, `model.invoke`, and
`files.project.read`. They restrict the host bridge, not the operating-system
permissions of trusted Python code. Exact pinned requirements can be listed in
`backend.dependencies`, such as `["package-name==1.2.3"]`. Each content version
gets its own Python environment; it also uses the installed host framework and
its dependencies. Python backends are checked in a separate process before
registration. Merely listing applications does not import their code.

## Browser and Python APIs

The host provides `window.openprogramApp` inside the application page:

- `load()` returns `{value, version}`; `save(value, version)` saves only if that
  version is still current. Reload after a concurrent-write error.
- `run(operation, input, requestKey)` returns a durable run record. Reuse the same
  request key when retrying the same submission; different input with that key
  is rejected.
- `runs()` lists recent runs; `status(id, after)` returns status, result, error,
  pending question and events after a sequence cursor.
- `cancel(id)` cancels execution; `answer(id, requestId, answer)` answers its
  current question. Runs from another instance are inaccessible through this bridge.

Python operations receive `context.load()`, `context.save(value,
expected_version=...)`, `context.progress(value)`, `context.ask(question)`, and
`context.read_file(relative_path)`. File reads require the declared capability
and a bound project. Model-enabled operations can call the existing `llm()` and
`agent()` APIs with an ambient Runtime; model credentials stay in the backend.
Use `context.ask()` for application questions. Runtime workflow interactions
that require predeclared durable waits retain their existing requirements.

Programs use the same operations:

```python
from openprogram.programs.application_client import run, status

job = run("local.notes", "summarize", {"text": "..."}, request_key="summary-1")
print(status(job["id"]))
```

Equivalent CLI entry points are `programs apps run APP OPERATION --input JSON`
(with `--project ID` for project scope), `programs apps status RUN_ID`, and
`programs apps cancel RUN_ID`.

## Persistence and lifecycle

Code is copied to a content-addressed version in the profile's application
storage. Editing the source directory does not change an installed version.
Use `install --replace` (and `--trust` for Python) to activate a new version.
Already-running operations retain their original code. Reopen an older page
before submitting operations against the new version.

Business state is stored separately from code and conversations. Each instance
has its own SQLite state, with a 4 MiB JSON value limit. Events have a 1 MiB limit.
Execution status uses the existing execution store; model call records use the
existing session-node writer under application run storage.

Stopping the worker interrupts an unfinished Python function; a restart reports
it as interrupted. Arbitrary Python stacks are not automatically resumed.
Persist incremental progress and explicitly start another operation after
reviewing the interruption. A schema or scope change is rejected before
activation; automatic data migrations are not supported in this version.

```bash
openprogram programs apps uninstall local.notes
```

Uninstall removes the menu entry and cancels active operations, retaining saved
data, previous code versions and their schema/scope compatibility identity.
Reinstalling a compatible package restores that data; incompatible reinstallation
is rejected before activation. The owner API also supports `enabled`, `hidden`
and `display_title` through `PATCH /api/applications/{id}`. These are distinct:
hiding an entry does not cancel a task; disabling it does.

Standalone deployment, non-Python backend adapters, MCP Apps compatibility and
embedding native operating-system GUI windows are not implemented.
