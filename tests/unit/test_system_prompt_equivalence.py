"""Step 1 of the context refactor: the registry-based
``components.build_system_prompt`` must be byte-for-byte equivalent to the
legacy ``system_prompt.build_system_prompt`` for the same agent input.

Both read the same global state (workspace files / skills / memory) in-process,
so for any given input their outputs must be identical.
"""
from openprogram.context import system_prompt as legacy
from openprogram.context import components as new


_CASES = [
    {},                                                    # empty dict
    {"id": "main"},                                        # id only
    {"id": "main", "name": "research-agent"},              # name
    {"id": "main", "system_prompt": "Be concise."},        # inline
    {"id": "", "name": "x", "system_prompt": "  hi  "},     # whitespace inline
    {"id": "main", "identity": {"name": "Bot",
                                "mention_patterns": ["@bot", "bot:"]}},  # mentions
    {"id": "main", "name": "a", "system_prompt": "P",
     "skills": {"disabled": ["foo"]}},                     # full-ish
]


def test_build_system_prompt_byte_equivalent():
    for case in _CASES:
        assert new.build_system_prompt(case) == legacy._compose(case), (
            f"mismatch for case {case!r}"
        )


class _SpecLike:
    """Object form (AgentSpec-ish): attribute access, not dict."""
    def __init__(self, **kw):
        for k, v in kw.items():
            setattr(self, k, v)


def test_build_system_prompt_object_form_equivalent():
    obj = _SpecLike(id="main", name="research-agent",
                    system_prompt="Be concise.", identity=None, skills=None)
    assert new.build_system_prompt(obj) == legacy._compose(obj)
