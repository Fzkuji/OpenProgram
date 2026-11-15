# `openprogram/programs/`

Everything the Agent can call is a Program. Importing `openprogram.programs`
registers every Program and exposes the resolution API other code uses:
presets (`TOOLSETS`, `DEFAULT_TOOLS`), the resident/deferred split, and the
policy chain (`agent_tools`, `apply_tool_policy`). The package root holds only
`__init__.py` (that API), this README and the four directories below.

`_host/lower_layers.py` fills the ports models and state own for what only a
Program has (web-search backends and Agent tool names for settings, the Goal
tool for usage metering and the session graph); `__init__.py` calls it at import, so
those layers never import Programs.

## `tools/`

The built-in tools: what the Agent cannot work without (files, shell, search,
planning, asking the user, memory, jobs, web, computer use). They ship with
the core, are always given to the Agent and cannot be disabled one by one.
Most register with `@function` on import; a tool package with
`PROGRAM_ENTRIES` registers through its own `ProgramContext`.

## `applications/`

The built-in applications are `browser` (the web browser the Agent operates
through `computer_use_agent`),
`workflow_recording` (the demonstration recorder behind the composer's
**Record a workflow**), `office_editor` (the ONLYOFFICE editor behind Word,
Excel and PowerPoint documents in the document window) and `meeting` (call
audio recorded on this computer, transcribed and summarized). Each loads through a
`ProgramContext` owned by `application:<name>`, provides the service the core
reaches it through (`browser`, `workflow_recording`, `office_editor`,
`meeting`) and can
be enabled and disabled from the Applications page. Only built-in applications live
here; applications and harnesses the user installs live in
`~/.openprogram/programs/<name>/` and load from that source directory.

## `_authoring/`

What Program code itself imports, shared by built-in and user Programs:
`errors`, `json_parsing`, `reports/` (evidence gates, local output, WeChat
sources), `helpers` (argument coercion for tool `execute` implementations),
`providers` (registries for tools with pluggable backends), `guides`
(instructions a tool hands the model), `prechecks` (argument checks before
approval), `application_client` (calling installed application
operations) and `testing` (scripted role replies for Program behaviour tests). User Programs depend on these import paths.

## `_host/`

How the host loads and runs Programs; Program code does not import it.
`runtime` is the `@function` decorator and the shared tool registry,
`execution` the wrappers every call runs through, `context` the
`ProgramContext` every Program registers through, `services` the named
services Programs share, `registry` the loader for built-in, user and
harness Programs, `packages` the Python Program packages and their load
state, `source_loader` the definition-time call scopes, `sources` the harness
catalogue and the owner-recorded program sources, `check` the
`openprogram programs check` command, `watcher` the background rescan,
`meta_storage` the Functions page metadata and `identity` the title,
description and icon every surface shows for an application. `ui/` runs applications with a
UI: catalog, loader, served pages, the operation runner and service.

See [Program Model](https://github.com/FuzzyKuji/OpenProgram/blob/main/docs/reference/design/function/program-model.html)
for the design.
