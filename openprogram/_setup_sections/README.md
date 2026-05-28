# `openprogram/_setup_sections/`

> Setup-wizard section bodies, extracted from openprogram/setup.py.

## Overview

``openprogram/setup.py`` keeps the JSON-config storage (``_read_config``,
``_write_config``, ``read_*_prefs``) and the prompt helpers
(``_choose_one``, ``_confirm``, etc.) because external callers (channels/
setup.py, the runtime config readers) import those names directly.

Section bodies live here:
    sections.py — providers/model/tools/agent/skills/ui/memory/profile/tts
    channels.py — channels section + per-channel account adders
    backend.py  — terminal backend section
    wizard.py   — orchestrator (intro/summary/_run_setup_inner/run_full_setup/
                  run_configure_menu) + QUICKSTART / ADVANCED section lists

## Files in this directory

- **`backend.py`** — Terminal exec backend section
- **`channels.py`** — Channels section: list / add / edit / delete per-channel accounts
- **`sections.py`** — Per-section runners: providers / model / tools / agent / skills / ui /
- **`wizard.py`** — Setup wizard orchestrator: intro / mode select / linear walk / summary

_Auto-generated from `__init__.py` docstring — keep that as the source of truth; re-run `_gen_dir_readmes.py` to refresh._
