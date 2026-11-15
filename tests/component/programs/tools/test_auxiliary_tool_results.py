"""Typed outcomes at registered image/MoA tool entries, without paid calls."""
from __future__ import annotations

import asyncio
import base64
import pytest

from openprogram.programs._host.runtime import get
from openprogram.programs.tools.agents import mixture_of_agents as moa
from openprogram.programs.tools.web.image_analyze import image_analyze as analyze
from openprogram.programs.tools.web.image_analyze.providers import openai as vision
from openprogram.programs.tools.web.image_generate import image_generate as generate
from openprogram.programs.tools.web.image_generate.providers import openai as image_provider
from openprogram.models.providers.types import AssistantMessage, Model, TextContent

# Owned 1x1 PNG. Provider stubs return these bytes; no image service is called.
PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aK1UAAAAASUVORK5CYII="
)


def invoke(name, **args):
    return asyncio.run(get(name).execute("controlled-call", args, None, None))


def text(result):
    return "\n".join(c.text for c in result.content if c.type == "text")


@pytest.fixture
def image_case(tmp_path, monkeypatch):
    import openprogram.host.sandbox as sandbox
    monkeypatch.setattr(sandbox, "_process_policy_override", sandbox.SandboxPolicy(
        writable_roots=(str(tmp_path),), deny_read=(), deny_write=(), network=False,
    ))
    monkeypatch.delenv("FAL_KEY", raising=False)
    monkeypatch.setattr("openprogram.models.providers.settings.env_api_keys.resolve_provider_key", lambda _: None)
    path = tmp_path / "owned.png"
    path.write_bytes(PNG)
    return path


@pytest.mark.parametrize("name", ["image_analyze", "image_generate", "mixture_of_agents"])
def test_missing_prompt_is_typed_failure(name, image_case):
    result = invoke(name)
    assert result.is_error
    assert "is required" in text(result)


@pytest.mark.parametrize("name", ["image_analyze", "image_generate"])
def test_unavailable_image_provider_is_typed_failure(name, image_case, tmp_path):
    result = invoke(name, prompt="owned input", image_paths=[str(image_case)], output_dir=str(tmp_path / "unused"))
    assert result.is_error
    assert "No " + name + " provider is available" in text(result)
    assert not (tmp_path / "unused").exists()


@pytest.mark.parametrize("name", ["image_analyze", "image_generate"])
def test_unknown_image_provider_is_typed_failure(name, image_case):
    result = invoke(name, prompt="owned input", image_paths=[str(image_case)], provider="unknown-controlled")
    assert result.is_error
    assert "not registered" in text(result)


def enable_openai(monkeypatch):
    monkeypatch.setattr("openprogram.models.providers.settings.env_api_keys.resolve_provider_key", lambda p: "controlled-test-key" if p == "openai" else None)


def test_vision_success_reads_owned_image_and_preserves_error_like_answer(image_case, monkeypatch):
    enable_openai(monkeypatch)
    calls = []

    def post(url, *, body, headers, **kwargs):
        assert url == vision.API_URL
        assert headers["Authorization"] == "Bearer controlled-test-key"
        parts = body["messages"][0]["content"]
        assert parts[0] == {"type": "text", "text": "Describe owned PNG"}
        assert base64.b64decode(parts[1]["image_url"]["url"].split(",", 1)[1]) == PNG
        calls.append(body["model"])
        return {"choices": [{"message": {"content": "Error: is the text displayed in this image."}}]}

    monkeypatch.setattr(vision, "post_json", post)
    args = dict(prompt="Describe owned PNG", image_paths=[str(image_case)], provider="openai", model="gpt-4o-mini")
    result = invoke("image_analyze", **args)
    assert not result.is_error
    assert "Error: is the text displayed" in text(result)
    direct = analyze.execute(**args)
    assert isinstance(direct, str) and direct == text(result)
    assert calls == ["gpt-4o-mini", "gpt-4o-mini"]


def test_generation_success_writes_provider_pngs_and_returns_real_paths(image_case, tmp_path, monkeypatch):
    enable_openai(monkeypatch)

    def post(url, *, body, headers, **kwargs):
        assert url == image_provider.API_URL
        assert headers["Authorization"] == "Bearer controlled-test-key"
        assert body == {"model": "dall-e-2", "prompt": "Owned red image", "n": 2,
                        "size": "512x512", "response_format": "b64_json"}
        return {"data": [{"b64_json": base64.b64encode(PNG).decode(), "revised_prompt": "Owned red image"} for _ in range(2)]}

    monkeypatch.setattr(image_provider, "post_json", post)
    out = tmp_path / "output"
    result = invoke("image_generate", prompt="Owned red image", provider="openai", model="dall-e-2", n=2, size="512x512", output_dir=str(out))
    files = sorted(out.glob("*.png"))
    assert not result.is_error
    assert len(files) == 2
    assert all(path.read_bytes() == PNG and str(path) in text(result) for path in files)
    assert "# image_generate (via openai, 2 images)" in text(result)


@pytest.mark.parametrize("data,expected", [([], "returned no images"),
    ([{"b64_json": base64.b64encode(b"not raster bytes").decode()}], "save failed")])
def test_generation_empty_or_invalid_result_is_typed_failure(image_case, tmp_path, monkeypatch, data, expected):
    enable_openai(monkeypatch)
    monkeypatch.setattr(image_provider, "post_json", lambda *a, **kw: {"data": data})
    output = tmp_path / "failed-output"
    args = dict(prompt="owned input", provider="openai", output_dir=str(output))
    result = invoke("image_generate", **args)
    assert result.is_error and expected in text(result)
    assert not list(output.glob("*.png"))
    direct = generate.execute(**args)
    assert isinstance(direct, str) and direct == text(result)


def test_vision_missing_image_is_typed_failure(image_case):
    result = invoke("image_analyze", prompt="no image")
    assert result.is_error and "at least one" in text(result)


@pytest.mark.parametrize("name,module", [("image_analyze", vision), ("image_generate", image_provider)])
def test_image_provider_exception_is_typed_failure(name, module, image_case, monkeypatch):
    enable_openai(monkeypatch)
    def fail(*args, **kwargs):
        raise RuntimeError("controlled service unavailable")
    monkeypatch.setattr(module, "post_json", fail)
    result = invoke(name, prompt="owned input", image_paths=[str(image_case)], provider="openai")
    assert result.is_error
    assert "RuntimeError: controlled service unavailable" in text(result)


@pytest.mark.parametrize("name,module", [("image_analyze", vision), ("image_generate", image_provider)])
def test_cancelled_image_provider_call_remains_cancellation(name, module, image_case, monkeypatch):
    enable_openai(monkeypatch)
    def cancel(*args, **kwargs):
        raise asyncio.CancelledError
    monkeypatch.setattr(module, "post_json", cancel)
    with pytest.raises(asyncio.CancelledError):
        invoke(name, prompt="cancelled owned input", image_paths=[str(image_case)], provider="openai")


@pytest.mark.parametrize("name", ["image_analyze", "image_generate"])
def test_sandbox_denial_is_typed_before_backend_call(name, image_case, tmp_path, monkeypatch):
    import openprogram.host.sandbox as sandbox
    enable_openai(monkeypatch)
    calls = []
    monkeypatch.setattr(vision, "post_json", lambda *a, **kw: calls.append(a))
    monkeypatch.setattr(image_provider, "post_json", lambda *a, **kw: calls.append(a))
    monkeypatch.setattr(sandbox, "_process_policy_override", sandbox.SandboxPolicy(
        writable_roots=(str(tmp_path),), deny_read=(str(image_case),), deny_write=(), network=False,
    ))
    outside = tmp_path.parent / "forbidden-image-output"
    args = {"image_paths": [str(image_case)]} if name == "image_analyze" else {"output_dir": str(outside)}
    result = invoke(name, prompt="denied", provider="openai", **args)
    assert result.is_error and "sandbox" in text(result)
    assert not calls and not outside.exists()


@pytest.fixture
def models(monkeypatch):
    import openprogram.models.providers as providers
    rows = [Model(id=mid, name=mid, provider=pid, api="openai-completions", base_url="https://api.openai.com/v1")
            for pid, mid in [("openai", "reference-a"), ("google", "reference-b"), ("openai", "aggregator")]]
    known = {(m.provider, m.id): m for m in rows}
    monkeypatch.setattr(providers, "get_models", lambda *_: rows)
    monkeypatch.setattr(providers, "get_model", lambda p, mid: known.get((p, mid)))
    monkeypatch.setattr("openprogram.models.providers.catalog.metadata.is_configured", lambda p: p in {"openai", "google"})
    monkeypatch.setattr(moa, "RETRY_BACKOFF_SECONDS", 0)
    return providers, rows


def reply(model, value):
    return AssistantMessage(content=[TextContent(text=value)], model=model.id, provider=model.provider, api=model.api, timestamp=1)


@pytest.mark.parametrize("args", [
    {"references": ["absent:unknown"]},
    {"references": ["openai:reference-a"], "aggregator": "absent:unknown"},
])
def test_unknown_moa_reference_or_aggregator_is_typed_without_calls(models, monkeypatch, args):
    providers, _ = models
    calls = []
    async def complete(*a, **kw):
        calls.append(a)
    monkeypatch.setattr(providers, "complete_simple", complete)
    result = invoke("mixture_of_agents", user_prompt="controlled question", **args)
    assert result.is_error and "unknown model spec" in text(result)
    assert not calls
    direct = asyncio.run(moa.execute(user_prompt="controlled question", **args))
    assert isinstance(direct, str) and direct == text(result)


def test_empty_moa_registry_is_typed_failure(models, monkeypatch):
    providers, _ = models
    monkeypatch.setattr(providers, "get_models", lambda *_: [])
    result = invoke("mixture_of_agents", user_prompt="controlled question")
    assert result.is_error and "registry is empty" in text(result)


def test_all_moa_references_failed_is_typed_failure(models, monkeypatch):
    providers, _ = models
    calls = []
    async def fail(model, context, options):
        calls.append(model.id)
        raise RuntimeError("controlled auth failure")
    monkeypatch.setattr(providers, "complete_simple", fail)
    result = invoke("mixture_of_agents", user_prompt="controlled question", references=["openai:reference-a", "google:reference-b"])
    assert result.is_error and "too few successful references" in text(result)
    assert len(calls) == 4 and "aggregator" not in calls


@pytest.mark.parametrize("aggregate_fails", [False, True])
def test_moa_aggregation_preserves_explicit_outcome_and_real_context(models, monkeypatch, aggregate_fails):
    providers, _ = models
    calls = []
    async def complete(model, context, options):
        assert context.messages[0].content[0].text == "controlled question"
        calls.append(model.id)
        if model.id == "aggregator":
            assert "owned reference-a" in context.system_prompt and "owned reference-b" in context.system_prompt
            if aggregate_fails:
                raise RuntimeError("controlled aggregator failure")
            return reply(model, "Error: is a literal token in the successful synthesis.")
        return reply(model, "owned " + model.id)
    monkeypatch.setattr(providers, "complete_simple", complete)
    result = invoke("mixture_of_agents", user_prompt="controlled question", references=["openai:reference-a", "google:reference-b"], aggregator="openai:aggregator")
    assert result.is_error is aggregate_fails
    assert calls == ["reference-a", "reference-b", "aggregator"]
    assert "**References**:" in text(result)
    assert ("aggregator call failed" if aggregate_fails else "literal token") in text(result)


def test_single_success_with_failed_reference_stays_success(models, monkeypatch):
    providers, _ = models
    async def complete(model, context, options):
        assert model.id != "aggregator"
        if model.id == "reference-b":
            raise RuntimeError("controlled reference failure")
        return reply(model, "Error: is literal answer content.")
    monkeypatch.setattr(providers, "complete_simple", complete)
    result = invoke("mixture_of_agents", user_prompt="controlled question", references=["openai:reference-a", "google:reference-b"], aggregator="openai:aggregator")
    assert not result.is_error
    assert "**Skipped**:" in text(result) and "skipped aggregator" in text(result)
    assert "literal answer content" in text(result)


def test_moa_cancelled_provider_call_remains_cancellation(models, monkeypatch):
    providers, _ = models
    async def cancel(*a, **kw):
        raise asyncio.CancelledError
    monkeypatch.setattr(providers, "complete_simple", cancel)
    with pytest.raises(asyncio.CancelledError):
        invoke("mixture_of_agents", user_prompt="controlled question", references=["openai:reference-a"])
