"""The ``openprogram setup`` wizard.

Config storage (``read_config``, ``update_config``, ``read_ui_prefs``) is
:mod:`openprogram.state.config` and :mod:`openprogram.state.preferences`;
this package is the interactive part:
    prompts.py  — questionary prompts with a plain ``input()`` fallback
    sections.py — providers/model/tools/agent/skills/ui/memory/profile/tts
    channels.py — channels section + per-channel account adders
    backend.py  — default environment section
    wizard.py   — orchestrator (intro/summary/_run_setup_inner/run_full_setup/
                  run_configure_menu) + QUICKSTART / ADVANCED section lists
"""
