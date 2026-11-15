"""Shared resource contracts and provider dispatch."""
from .registry import ResourceProvider, ResourceRegistry
from .providers.builtin import register_builtins

registry = ResourceRegistry(include_applications=True)
register_builtins(registry)

__all__ = ["ResourceProvider", "ResourceRegistry", "registry"]
