"""Capability validation and dispatch; providers retain execution authority."""
from __future__ import annotations

import copy
from dataclasses import dataclass
import re
from typing import Any, Callable

from jsonschema import Draft202012Validator, ValidationError


@dataclass(frozen=True)
class ResourceProvider:
    name: str
    title: str
    actions: dict[str, dict]
    invoke: Callable[[str, dict], Any]


class ResourceRegistry:
    def __init__(self, *, include_applications: bool = False):
        self.include_applications = include_applications
        self._providers: dict[str, ResourceProvider] = {}

    def register(self, provider: ResourceProvider, *, owner: str = "") -> None:
        """Add a provider. ``application.<id>`` is reserved to the
        application ``<id>``, which registers through its context with
        ``owner="application:<id>"``."""
        if not re.fullmatch(r"[a-z][a-z0-9._-]{0,63}", provider.name):
            raise ValueError("invalid_resource_provider")
        if provider.name.startswith("application.") and owner != "application:" + provider.name.removeprefix("application."):
            raise ValueError("application_provider_namespace_reserved")
        if provider.name in self._providers:
            raise ValueError("resource_provider_exists")
        for schema in provider.actions.values():
            Draft202012Validator.check_schema(schema)
        self._providers[provider.name] = ResourceProvider(
            provider.name, provider.title, copy.deepcopy(provider.actions), provider.invoke,
        )

    def unregister(self, name: str) -> None:
        self._providers.pop(name, None)

    def _load_programs(self) -> None:
        """Load the built-in Programs: an application adds its providers when it loads.

        Installed applications register theirs when the worker loads them.
        """
        if self.include_applications:
            from openprogram.host import runtime_facts
            runtime_facts.load_programs()

    def _provider(self, name: str) -> ResourceProvider:
        if name not in self._providers:
            self._load_programs()
        try:
            return self._providers[name]
        except KeyError as exc:
            raise ValueError("unknown_resource_provider") from exc

    def describe(self, name: str = "") -> dict:
        if not name:
            self._load_programs()
            # A snapshot: applications register and leave from other threads.
            return {"ok": True, "providers": [self._descriptor(p) for p in list(self._providers.values())]}
        return self._descriptor(self._provider(name))

    @staticmethod
    def _descriptor(provider: ResourceProvider) -> dict:
        return {"provider": provider.name, "title": provider.title,
                "actions": copy.deepcopy(provider.actions)}

    def invoke(self, name: str, action: str, arguments: dict) -> Any:
        provider = self._provider(name)
        schema = provider.actions.get(action)
        if schema is None:
            raise ValueError("unsupported_resource_action")
        try:
            Draft202012Validator(schema).validate(arguments)
        except ValidationError as exc:
            raise ValueError("invalid_resource_arguments: " + exc.message) from exc
        return provider.invoke(action, copy.deepcopy(arguments))


