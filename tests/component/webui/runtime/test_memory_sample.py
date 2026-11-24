"""A Worker asked for a memory sample writes what its memory holds."""
from __future__ import annotations

import sys
import types

from openprogram.interfaces.server.server_runtime import memory_sample, thread_sample


def test_a_request_file_is_answered_with_the_memory_sample(tmp_path):
    requests = thread_sample.SampleRequests(
        tmp_path, sample=lambda: "holds that\n",
        request_name=memory_sample.REQUEST_NAME, result_name=memory_sample.RESULT_NAME)
    (tmp_path / memory_sample.REQUEST_NAME).write_text("", encoding="utf-8")
    assert requests.check() is True
    assert (tmp_path / memory_sample.RESULT_NAME).read_text(encoding="utf-8") == "holds that\n"
    assert not (tmp_path / thread_sample.RESULT_NAME).exists()


def test_a_sample_names_the_module_level_cache_that_grew_and_no_content(monkeypatch):
    module = types.ModuleType("openprogram._memory_sample_probe")
    secret = "s3cret-" * 20_000
    module._cache = {"kept": [secret, {"n": list(range(50_000))}]}
    monkeypatch.setitem(sys.modules, module.__name__, module)
    sample = memory_sample.take_sample()
    assert "openprogram._memory_sample_probe._cache" in sample
    assert "openprogram._memory_sample_probe" in sample.split("by what holds them:")[1] or "dict['kept']" in sample
    assert "s3cret" not in sample


def test_a_shared_object_is_counted_for_one_holder_only():
    shared = [str(i) * 1000 for i in range(100)]
    seen: set[int] = set()
    first = memory_sample.deep_size({"a": shared}, seen)
    second = memory_sample.deep_size({"b": shared}, seen)
    assert first > 100_000 > second
