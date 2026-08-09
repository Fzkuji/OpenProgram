"""Read one logical memory that spans several workspaces.

A person works in many directories but is one person. What they prefer, how
they want things written, who they are — that belongs everywhere. What a
particular repository decided last Tuesday belongs to that repository.

A layer is one workspace plus a name. Reads span every layer; a write names
the layer it lands in. Layers never merge on disk: each stays a normal
workspace that `scriptorium validate` accepts and that the experiment path
opens directly.

With a single layer every method returns exactly what `inspect` returns for
that root — no qualification, no combined revision — so existing callers see
no change. With several layers, paths are qualified with the layer name
(`project:topics/api.md`) so a caller can always tell where something came
from, and can read it back.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import inspect

SEPARATOR = ":"
FORBIDDEN_IN_NAME = (SEPARATOR, "=", " ", "/")


@dataclass(frozen=True)
class Layer:
    """One named workspace."""

    name: str
    root: Path


class LayeredMemory:
    """Several workspaces read as one, ordered narrowest first.

    Order decides two things: where an unqualified write lands (the first
    layer), and which layer's answer comes first when both match.
    """

    def __init__(self, layers: list[Layer]):
        if not layers:
            raise ValueError("a layered memory needs at least one layer")
        names = [layer.name for layer in layers]
        if len(set(names)) != len(names):
            raise ValueError(f"layer names must be unique: {names}")
        for name in names:
            for char in FORBIDDEN_IN_NAME:
                if char in name:
                    raise ValueError(
                        f"layer name may not contain {char!r}: {name}"
                    )
        self.layers = layers
        self.single = len(layers) == 1

    @property
    def default(self) -> Layer:
        """Where a write goes when the caller does not say."""
        return self.layers[0]

    def resolve(self, name: str) -> Layer:
        for layer in self.layers:
            if layer.name == name:
                return layer
        known = ", ".join(layer.name for layer in self.layers)
        raise ValueError(f"no such layer: {name} (have {known})")

    def split(self, path: str) -> tuple[Layer, str]:
        """Split `layer:path` into its layer and the path within it.

        Only a prefix that names a real layer counts as qualification, so a
        file whose own name contains a colon never routes to the wrong place.
        An unqualified path belongs to the default layer.
        """
        if SEPARATOR in path:
            name, _, rest = path.partition(SEPARATOR)
            for layer in self.layers:
                if layer.name == name:
                    return layer, rest
        return self.default, path

    def qualify(self, layer: Layer, path: str) -> str:
        return path if self.single else f"{layer.name}{SEPARATOR}{path}"

    def layer_revision(self, revision: str, layer: Layer) -> str:
        """The one layer's revision out of a combined revision string.

        A bare revision (no `name=`) passes through untouched, so a caller
        holding a single layer's revision can still use it. An unparseable
        string also passes through: the workspace's own comparison then
        rejects it as CONCURRENT_UPDATE instead of this code guessing.
        """
        if self.single or "=" not in revision:
            return revision
        for token in revision.split():
            name, _, value = token.partition("=")
            if name == layer.name:
                return value
        return revision

    def status(self, *, embedding_available: bool = False) -> dict[str, Any]:
        if self.single:
            return inspect.status(
                self.default.root, embedding_available=embedding_available
            )
        per_layer: dict[str, Any] = {}
        for layer in self.layers:
            try:
                if not layer.root.is_dir():
                    # inspect.status happily counts zeros for a missing
                    # directory; a vanished volume must be visible instead.
                    raise FileNotFoundError(layer.root)
                per_layer[layer.name] = inspect.status(
                    layer.root, embedding_available=embedding_available
                )
            except Exception:  # noqa: BLE001 - a bad layer is reported, not fatal
                per_layer[layer.name] = {"error": "unreadable"}
        readable = {
            name: data for name, data in per_layer.items() if "revision" in data
        }
        if not readable:
            raise ValueError("no layer could be read")
        return {
            "layers": per_layer,
            "default_layer": self.default.name,
            # One revision string over all readable layers, so a stale write
            # in any of them is rejected rather than silently applied.
            "revision": " ".join(
                f"{name}={data['revision']}" for name, data in readable.items()
            ),
        }

    def revision(self) -> str:
        return self.status()["revision"]

    def list_files(self, *, prefix: str = "", include_derived: bool = True,
                   limit: int = 200) -> dict[str, Any]:
        if self.single:
            return inspect.list_files(
                self.default.root, prefix=prefix,
                include_derived=include_derived, limit=limit,
            )
        targets, within = self._targets(prefix)
        files: list[dict[str, Any]] = []
        total = 0
        error: Exception | None = None
        for layer in targets:
            try:
                data = inspect.list_files(
                    layer.root, prefix=within,
                    include_derived=include_derived, limit=limit,
                )
            except Exception as exc:  # noqa: BLE001
                error = exc
                continue
            total += data.get("total", len(data.get("files", [])))
            for entry in data.get("files", []):
                entry = dict(entry)
                entry["path"] = self.qualify(layer, entry["path"])
                entry["layer"] = layer.name
                files.append(entry)
        if not files and error is not None:
            # A bad argument must reach the caller. Only a layer that failed
            # while another answered is passed over.
            raise error
        return {
            "files": files[:limit],
            "total": total,
            "truncated": total > limit or len(files) > limit,
        }

    def read_file(self, path: str, **kwargs: Any) -> dict[str, Any]:
        if self.single:
            return inspect.read_file(self.default.root, path, **kwargs)
        layer, within = self.split(path)
        data = inspect.read_file(layer.root, within, **kwargs)
        if not self.single:
            data = dict(data)
            data["path"] = self.qualify(layer, data.get("path", within))
            data["layer"] = layer.name
        return data

    def grep(self, query: str, *, prefix: str = "",
             **kwargs: Any) -> dict[str, Any]:
        if self.single:
            return inspect.grep(self.default.root, query, prefix=prefix, **kwargs)
        targets, within = self._targets(prefix)
        limit = int(kwargs.get("limit", 50))
        matches: list[dict[str, Any]] = []
        total = 0
        error: Exception | None = None
        for layer in targets:
            try:
                data = inspect.grep(layer.root, query, prefix=within, **kwargs)
            except Exception as exc:  # noqa: BLE001
                error = exc
                continue
            total += data.get("total", 0)
            for match in data.get("matches", []):
                match = dict(match)
                match["path"] = self.qualify(layer, match["path"])
                match["layer"] = layer.name
                matches.append(match)
        if not matches and error is not None:
            raise error
        return {
            "matches": matches[:limit],
            "total": total,
            "truncated": total > limit or len(matches) > limit,
        }

    def search(self, query: str, *, top_k: int = 8,
               path_prefix: str | None = None, **kwargs: Any) -> dict[str, Any]:
        """Rank across layers.

        Each layer scores its own blocks with its own statistics, so scores
        are not comparable between layers. Interleaving by rank keeps a small
        layer from being buried by a large one, which straight score-sorting
        would do. The default layer's hit comes first at every rank.
        """
        if self.single:
            return inspect.search(
                self.default.root, query, top_k=top_k,
                path_prefix=path_prefix, **kwargs
            )
        targets, within = self._targets(path_prefix or "")
        per_layer: list[list[dict[str, Any]]] = []
        method = kwargs.get("method", "bm25")
        error: Exception | None = None
        for layer in targets:
            try:
                data = inspect.search(
                    layer.root, query, top_k=top_k,
                    path_prefix=within or None, **kwargs
                )
            except Exception as exc:  # noqa: BLE001
                # EMBEDDING_UNAVAILABLE and friends must surface if no layer
                # answers; a broken layer next to a working one is skipped.
                error = exc
                continue
            method = data.get("method", method)
            results = []
            for hit in data.get("results", []):
                hit = dict(hit)
                hit["path"] = self.qualify(layer, hit["path"])
                hit["layer"] = layer.name
                results.append(hit)
            per_layer.append(results)
        if not per_layer and error is not None:
            raise error
        interleaved: list[dict[str, Any]] = []
        for rank in range(max((len(r) for r in per_layer), default=0)):
            for results in per_layer:
                if rank < len(results):
                    interleaved.append(results[rank])
        return {"method": method, "results": interleaved[:top_k]}

    def _targets(self, prefix: str) -> tuple[list[Layer], str]:
        """A qualified prefix narrows to one layer; a bare one spans all."""
        if prefix and SEPARATOR in prefix:
            name, _, rest = prefix.partition(SEPARATOR)
            for layer in self.layers:
                if layer.name == name:
                    return [layer], rest
        return self.layers, prefix
