"""Persist role identities, never credentials; bind provider-specific runtimes."""
from contextlib import ExitStack, contextmanager
from contextvars import ContextVar
import math
from typing import get_args

from openprogram.providers.types import ThinkingLevel


_active_options = ContextVar("goal_role_options", default=None)
_resources = ContextVar("goal_role_resources", default=None)


def edit_role_requests(goal, updates):
    """Prepare explicit role selections without resolving credentials or models."""
    if not isinstance(updates, dict) or not updates or updates.keys() - {"work", "judge"}:
        raise ValueError("Role updates require work or judge")
    from .state import DEFAULT_PHASE_TIMEOUT_S
    requests = {
        "model": "", "effort": "off", "timeout_s": DEFAULT_PHASE_TIMEOUT_S,
        "judge_model": "", "judge_effort": "off", "judge_timeout_s": DEFAULT_PHASE_TIMEOUT_S,
        **(goal.get("role_requests") or {}),
    }
    for name, saved in (goal.get("roles") or {}).items():
        prefix = "judge_" if name == "judge" else ""
        requests.update({
            prefix + "model": f"{saved['provider']}:{saved['model']}",
            prefix + "effort": saved["effort"],
            prefix + "timeout_s": saved["timeout_s"],
        })
    for name, patch in updates.items():
        if not isinstance(patch, dict) or patch.keys() - {"provider", "model", "effort", "timeout_s"}:
            raise ValueError("Invalid role settings")
        prefix = "judge_" if name == "judge" else ""
        selector = requests[prefix + "model"]
        separators = [char for char in (":", "/") if char in selector]
        parts = selector.split(min(separators, key=selector.index), 1) if separators else ["", ""]
        config = {"provider": parts[0], "model": parts[1],
                  "effort": requests[prefix + "effort"], "timeout_s": requests[prefix + "timeout_s"], **patch}
        provider, model = config["provider"], config["model"]
        if (not isinstance(provider, str) or not provider.strip() or any(char in provider for char in ":/")
                or not isinstance(model, str) or not model.strip()):
            raise ValueError("Each role requires an explicit provider and model")
        effort = config["effort"]
        if not isinstance(effort, str) or effort not in {"off", *get_args(ThinkingLevel)}:
            raise ValueError("Invalid role reasoning effort")
        try:
            timeout = float(config["timeout_s"])
        except (TypeError, ValueError) as exc:
            raise ValueError("Role timeout must be a positive finite number") from exc
        if isinstance(config["timeout_s"], bool) or not math.isfinite(timeout) or timeout <= 0:
            raise ValueError("Role timeout must be a positive finite number")
        requests.update({prefix + "model": f"{provider.strip()}:{model.strip()}",
                         prefix + "effort": effort, prefix + "timeout_s": timeout})
    if not requests["model"]:
        raise ValueError("Configure the work role first")
    return requests


@contextmanager
def role_lifetime():
    with ExitStack() as resources:
        token = _resources.set(resources)
        try:
            yield
        finally:
            _resources.reset(token)


def _create_runtime(provider, model):
    from openprogram.providers.registry import create_runtime

    runtime = create_runtime(provider=provider, model=model)
    resources = _resources.get()
    if resources is not None:
        resources.callback(runtime.close)
    return runtime


def identity(runtime):
    model = getattr(runtime, "api_model", None)
    provider = getattr(runtime, "provider_id", None) or getattr(model, "provider", "")
    return {
        "provider": provider,
        "model": getattr(model, "id", "") or getattr(runtime, "model", ""),
        "model_provider": getattr(model, "provider", "") or provider,
    }


def _select(selector, current):
    if not selector:
        return current
    current_id = identity(current)
    separators = [char for char in (":", "/") if char in selector]
    separator = min(separators, key=selector.index) if separators else None
    if separator:
        provider, model = selector.split(separator, 1)
    else:
        provider, model = current_id["provider"], selector
    if provider == current_id["provider"] and model == current_id["model"]:
        return current
    if not provider or not model:
        raise ValueError("Goal role requires an explicit provider and model")
    selected = _create_runtime(provider, model)
    from openprogram.providers.registry import PROVIDERS
    allowed_providers = {provider, PROVIDERS.get(provider, {}).get("model_namespace")}
    selected_id = identity(selected)
    if selected_id["model"] != model or selected_id["provider"] not in allowed_providers:
        raise ValueError("Goal role unavailable; refusing a replacement model or provider")
    return selected


def prepare_roles(saved, current, *, model, effort, timeout_s,
                  judge_model, judge_effort, judge_timeout_s):
    runtimes = {}
    if saved is not None:
        if set(saved) != {"work", "judge"}:
            raise ValueError("Goal role configuration requires work and judge")
        for name, config in saved.items():
            if set(config) != {"provider", "model", "model_provider", "effort", "timeout_s"}:
                raise ValueError(f"Goal {name} role has an invalid configuration")
            expected = {key: config[key] for key in ("provider", "model", "model_provider")}
            if not all(isinstance(value, str) and value for value in expected.values()):
                raise ValueError(f"Goal {name} role has an invalid model identity")
            timeout = config.get("timeout_s")
            if (not isinstance(timeout, (int, float)) or not math.isfinite(timeout)
                    or timeout <= 0 or not isinstance(config.get("effort"), str)):
                raise ValueError(f"Goal {name} role has invalid execution settings")
            selected = current if identity(current) == expected else _create_runtime(
                config["provider"], config["model"],
            )
            if identity(selected) != expected:
                raise ValueError(f"Goal {name} model is unavailable; refusing a replacement")
            runtimes[name] = selected
        return saved, runtimes
    work = _select(model, current)
    judge = _select(judge_model, work)
    configs = {}
    for name, selected, thinking, timeout in (
        ("work", work, effort, timeout_s),
        ("judge", judge, judge_effort, judge_timeout_s),
    ):
        configs[name] = {
            **identity(selected),
            "effort": thinking or getattr(selected, "thinking_level", "off"),
            "timeout_s": timeout,
        }
        runtimes[name] = selected
    return configs, runtimes


@contextmanager
def use_role(runtime, config):
    from openprogram.agentic_programming.function import _current_runtime

    previous = _current_runtime.get(None)
    if previous is not None and previous is not runtime:
        for field in ("session_id", "on_stream", "system", "_skills_config"):
            if hasattr(previous, field):
                setattr(runtime, field, getattr(previous, field))
    runtime_token = _current_runtime.set(runtime)
    options_token = _active_options.set(config)
    try:
        yield
    finally:
        _active_options.reset(options_token)
        _current_runtime.reset(runtime_token)
        if previous is not None and previous is not runtime:
            # Consumers outside this scope inspect the just-finished phase.
            for field in ("last_blocks", "last_usage"):
                if hasattr(runtime, field):
                    setattr(previous, field, getattr(runtime, field))


def inspection_options(*, default_model="", default_timeout=300.0):
    config = _active_options.get()
    if config is None:
        return {"model": default_model, "timeout_s": default_timeout}
    return {"model": "", "effort": config["effort"], "timeout_s": config["timeout_s"]}
