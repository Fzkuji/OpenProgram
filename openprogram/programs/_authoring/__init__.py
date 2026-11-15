"""What Program code imports, shared by built-in and user Programs.

- ``json_parsing``: ``parse_json`` pulls a JSON object out of a model reply.
- ``helpers``: argument coercion and availability checks for tool ``execute``
  implementations.
- ``providers``: the registry scaffolding for tools with pluggable backends.
- ``guides``: instructions a tool hands the model when a call needs them.
- ``prechecks``: argument checks that run before a call asks for approval.
- ``application_client``: call installed application operations.
- ``reports``: report evidence gates, local output and read-only WeChat
  sources.

User Programs import them by these paths, for example
``from openprogram.programs._authoring.reports.io import save_report``.
"""
