"""Cached-prefix token parsing on the completions API path.

Regression for the bug where openai_completions read only
prompt_tokens/completion_tokens, so DeepSeek sessions recorded
cache_read=0 while the API served ~99% of the prompt from cache.
"""
from types import SimpleNamespace

from openprogram.providers.openai_completions.openai_completions import _usage_from_chunk


def test_plain_usage_no_cache():
    u = _usage_from_chunk(SimpleNamespace(
        prompt_tokens=100, completion_tokens=20, total_tokens=120,
    ))
    assert (u.input, u.output, u.cache_read) == (100, 20, 0)


def test_openai_style_cached_tokens_split_out_of_input():
    u = _usage_from_chunk(SimpleNamespace(
        prompt_tokens=10175, completion_tokens=106, total_tokens=10281,
        prompt_tokens_details=SimpleNamespace(cached_tokens=10048),
    ))
    assert u.cache_read == 10048
    assert u.input == 10175 - 10048


def test_deepseek_style_prompt_cache_hit_tokens():
    u = _usage_from_chunk(SimpleNamespace(
        prompt_tokens=10412, completion_tokens=150, total_tokens=10562,
        prompt_cache_hit_tokens=10240, prompt_cache_miss_tokens=172,
    ))
    assert u.cache_read == 10240
    assert u.input == 172


def test_openai_details_win_over_deepseek_field():
    u = _usage_from_chunk(SimpleNamespace(
        prompt_tokens=1000, completion_tokens=10, total_tokens=1010,
        prompt_tokens_details=SimpleNamespace(cached_tokens=600),
        prompt_cache_hit_tokens=999,
    ))
    assert u.cache_read == 600
    assert u.input == 400


def test_cached_never_drives_input_negative():
    u = _usage_from_chunk(SimpleNamespace(
        prompt_tokens=50, completion_tokens=5, total_tokens=55,
        prompt_cache_hit_tokens=80,
    ))
    assert u.input == 0
    assert u.cache_read == 80


def test_reasoning_tokens_split_out_of_output():
    u = _usage_from_chunk(SimpleNamespace(
        prompt_tokens=100, completion_tokens=50, total_tokens=150,
        completion_tokens_details=SimpleNamespace(reasoning_tokens=30),
    ))
    assert u.output == 20
