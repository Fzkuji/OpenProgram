"""Semble stays optional to import and reports the release repair action."""
import builtins
import sys

import pytest


def test_semble_tools_hint_when_package_absent(monkeypatch):
    import openprogram.programs.tools.code.semble.shared as tool_module

    monkeypatch.setattr(tool_module, "_index_cache", {})
    monkeypatch.delitem(sys.modules, "semble", raising=False)
    real_import = builtins.__import__

    def _no_semble(name, *args, **kwargs):
        if name == "semble" or name.startswith("semble."):
            raise ModuleNotFoundError("No module named 'semble'")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _no_semble)
    with pytest.raises(RuntimeError, match="complete OpenProgram release"):
        tool_module._get_or_build_index("/tmp")
