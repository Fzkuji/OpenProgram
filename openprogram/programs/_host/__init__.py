"""How the host loads and runs Programs. Program code does not import this.

- ``runtime``: the ``@function`` decorator, the tool registry and tiers.
- ``execution``: the execution wrappers ``@function`` and Agent methods share.
- ``context``: ``ProgramContext``, the one object every Program registers through.
- ``services``: named services Programs and plugins offer each other.
- ``registry``: loads the built-in tools and applications, the user's
  Programs and the installed harnesses.
- ``packages``: Program packages, their ``PROGRAM_ENTRIES`` and load state.
- ``source_loader``: definition-time call scopes for authorized Program sources.
- ``sources``: the owner-recorded program sources (``program-sources.json``).
- ``check``: ``openprogram programs check``.
- ``watcher``: picks up Programs installed or changed while the host runs.
- ``meta_storage``: profile-scoped tool profiles and Functions page metadata.
- ``identity``: the title, one-sentence description and icon every surface
  shows for an application or package.
- ``ui``: applications with a UI: catalog, loader, served pages, the
  operation runner and service.
"""
