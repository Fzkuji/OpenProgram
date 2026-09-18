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

    def register(self, provider: ResourceProvider) -> None:
        if not re.fullmatch(r"[a-z][a-z0-9._-]{0,63}", provider.name):
            raise ValueError("invalid_resource_provider")
        if provider.name.startswith("application."):
            raise ValueError("application_provider_namespace_reserved")
        if provider.name in self._providers:
            raise ValueError("resource_provider_exists")
        for schema in provider.actions.values():
            Draft202012Validator.check_schema(schema)
        self._providers[provider.name] = ResourceProvider(
            provider.name, provider.title, copy.deepcopy(provider.actions), provider.invoke,
        )

    def _provider(self, name: str) -> ResourceProvider:
        if self.include_applications and name.startswith("application."):
            from .providers.applications import application_provider
            return application_provider(name.removeprefix("application."))
        try:
            return self._providers[name]
        except KeyError as exc:
            raise ValueError("unknown_resource_provider") from exc

    def describe(self, name: str = "") -> dict:
        if not name:
            from .providers.applications import installed_providers
            names = [*self._providers, *(installed_providers() if self.include_applications else [])]
            return {"ok": True, "providers": [self.describe(key) for key in names]}
        provider = self._provider(name)
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


