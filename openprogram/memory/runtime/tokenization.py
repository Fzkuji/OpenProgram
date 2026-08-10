"""Local token counting shared by memory construction and retrieval."""

from __future__ import annotations

import importlib.metadata
import json
from typing import Any, Iterable, Mapping


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


class TokenCounter:
    """A reversible local tokenizer with a recorded identity."""

    def __init__(self, *, identity: Mapping[str, Any], encoder: Any, decoder: Any):
        self.identity = json.loads(_canonical_json(dict(identity)))
        if self.identity.get("provider_exact") is not False:
            raise ValueError("local token counter must declare provider_exact=false")
        self._encode = encoder
        self._decode = decoder

    @classmethod
    def utf8_bytes(
        cls,
        *,
        requested_model: str,
        fallback_reason: str = "explicit_utf8_byte_fallback",
    ) -> TokenCounter:
        identity = {
            "implementation": "builtin_utf8_bytes",
            "implementation_version": "1",
            "encoding_name": "utf8_bytes_v1",
            "requested_model": requested_model,
            "resolution": "explicit_byte_fallback",
            "fallback_reason": fallback_reason,
            "provider_exact": False,
            "counting_note": (
                "One token equals one UTF-8 byte. This is deterministic budget "
                "accounting, not a provider tokenizer estimate."
            ),
        }
        return cls(
            identity=identity,
            encoder=lambda text: list(text.encode("utf-8")),
            decoder=lambda token_ids: bytes(token_ids).decode(
                "utf-8", errors="ignore"
            ),
        )

    @classmethod
    def resolve(
        cls,
        *,
        requested_model: str,
        fallback_encoding: str = "o200k_base",
        allow_byte_fallback: bool = False,
    ) -> TokenCounter:
        try:
            import tiktoken  # type: ignore[import-not-found]
        except ImportError as exc:
            if not allow_byte_fallback:
                raise RuntimeError(
                    "tiktoken is unavailable; install it or explicitly enable "
                    "the UTF-8 byte fallback"
                ) from exc
            return cls.utf8_bytes(
                requested_model=requested_model,
                fallback_reason="tiktoken_not_installed",
            )

        version = importlib.metadata.version("tiktoken")
        try:
            encoding = tiktoken.encoding_for_model(requested_model)
            resolution = "tiktoken_model_mapping"
            fallback_reason = None
        except KeyError:
            encoding = tiktoken.get_encoding(fallback_encoding)
            resolution = "tiktoken_named_fallback"
            fallback_reason = "requested_model_not_in_tiktoken_mapping"

        identity = {
            "implementation": "tiktoken",
            "implementation_version": version,
            "encoding_name": encoding.name,
            "requested_model": requested_model,
            "resolution": resolution,
            "fallback_encoding": fallback_encoding,
            "fallback_reason": fallback_reason,
            "provider_exact": False,
            "counting_note": (
                "Local tiktoken count used for an enforceable experiment budget; "
                "it is not a provider-reported exact count."
            ),
        }
        return cls(identity=identity, encoder=encoding.encode, decoder=encoding.decode)

    @classmethod
    def from_identity(cls, identity: Mapping[str, Any]) -> TokenCounter:
        implementation = identity.get("implementation")
        if implementation == "builtin_utf8_bytes":
            expected = cls.utf8_bytes(
                requested_model=str(identity.get("requested_model", "")),
                fallback_reason=str(identity.get("fallback_reason", "")),
            )
            if expected.identity != dict(identity):
                raise ValueError("recorded UTF-8 byte tokenizer identity is invalid")
            return expected
        if implementation != "tiktoken":
            raise ValueError(
                f"unsupported tokenizer implementation: {implementation!r}"
            )

        try:
            import tiktoken  # type: ignore[import-not-found]
        except ImportError as exc:
            raise RuntimeError("tiktoken is required to audit this trace") from exc
        installed = importlib.metadata.version("tiktoken")
        recorded = str(identity.get("implementation_version"))
        if installed != recorded:
            raise RuntimeError(
                f"tiktoken version mismatch: trace={recorded}, installed={installed}"
            )
        encoding = tiktoken.get_encoding(str(identity.get("encoding_name")))
        if identity.get("disallowed_special") == []:
            encoder = lambda text: list(  # noqa: E731
                encoding.encode(text, disallowed_special=())
            )
        else:
            encoder = encoding.encode
        return cls(identity=identity, encoder=encoder, decoder=encoding.decode)

    def encode(self, text: str) -> list[int]:
        if not isinstance(text, str):
            raise TypeError("visible content must be a rendered string")
        return list(self._encode(text))

    def decode(self, token_ids: Iterable[int]) -> str:
        return str(self._decode(list(token_ids)))

    def count(self, text: str) -> int:
        return len(self.encode(text))

    def truncate(self, text: str, limit: int) -> str:
        if limit < 0:
            raise ValueError("token limit must be non-negative")
        token_ids = self.encode(text)
        if len(token_ids) <= limit:
            return text
        candidate_ids = token_ids[:limit]
        while candidate_ids:
            try:
                candidate = self.decode(candidate_ids)
                if text.startswith(candidate) and self.count(candidate) <= limit:
                    return candidate
            except Exception:  # noqa: BLE001
                pass
            candidate_ids.pop()
        return ""
