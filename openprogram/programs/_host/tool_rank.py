"""Ranking deferred tools for a ``tool_search`` keyword query.

A port of Pi's ``Bm25Ranker`` (``coding-agent/src/extensions/tool-search``):
Okapi BM25 over each tool's description and schema text, with its name and
its namespace (MCP server or application) as separately weighted fields.

- A tool matching more of the query's terms, in any field, ranks above one
  matching fewer, whatever their scores.
- A query term in the name counts ``NAME_WEIGHT`` times its IDF: more than
  any number of mentions in the description.
- A term in the namespace counts ``NAMESPACE_WEIGHT`` times its IDF: it
  helps find the server, not the tool in it.
- A tool whose whole local name (the part after the last ``__``) is in the
  query gets a bonus worth one more match of every term.
- A query that is exactly a tool's name ranks that tool first.

Tokens: words split at identifier boundaries (``camelCase``, ``snake_case``,
``__``), lowercased, English stop words dropped, a naive singular form
(``issues`` → ``issue``). Pi segments non-ASCII text with
``Intl.Segmenter``; here each dense character (CJK, kana, hangul, the
ranges ``state.store.session.weight`` counts on their own) is its own token.

Design: docs/reference/design/function/calling-unification.html
"""
from __future__ import annotations

import math
import re
import unicodedata
from dataclasses import dataclass
from typing import Any, Iterable, Optional

K1 = 1.2
B = 0.75
NAME_WEIGHT = 3.0
NAMESPACE_WEIGHT = 0.5

STOP_WORDS = frozenset({
    "a", "an", "and", "are", "as", "at", "be", "by", "for", "from", "in", "is",
    "it", "of", "on", "or", "that", "the", "this", "to", "with",
})

_CAMEL_LOWER_UPPER = re.compile(r"([a-z0-9])([A-Z])")
_CAMEL_ACRONYM = re.compile(r"([A-Z]+)([A-Z][a-z])")
_ASCII_WORD = re.compile(r"^[a-z0-9]+$")


def _dense(char: str) -> bool:
    from openprogram.state.store.session.weight import _dense as dense

    return dense(ord(char))


def _word_char(char: str) -> bool:
    return unicodedata.category(char)[0] in ("L", "M", "N")


def stem(term: str) -> str:
    """Naive singular form, so ``issues`` matches ``issue``."""
    if len(term) > 4 and term.endswith("ies"):
        return term[:-3] + "y"
    if len(term) > 4 and re.search(r"(ches|shes|sses|xes|zes)$", term):
        return term[:-2]
    if len(term) > 3 and term.endswith("s") and not term.endswith("ss"):
        return term[:-1]
    return term


def tokenize(text: str) -> list[str]:
    """Words of ``text`` as the ranker compares them."""
    normalized = unicodedata.normalize("NFC", text or "")
    normalized = _CAMEL_LOWER_UPPER.sub(r"\1 \2", normalized)
    normalized = _CAMEL_ACRONYM.sub(r"\1 \2", normalized).lower()
    words: list[str] = []
    current: list[str] = []
    for char in normalized:
        if _word_char(char) and not _dense(char):
            current.append(char)
            continue
        if current:
            words.append("".join(current))
            current = []
        if _dense(char) and _word_char(char):
            words.append(char)
    if current:
        words.append("".join(current))
    return [stem(word) if _ASCII_WORD.match(word) else word
            for word in words if word and word not in STOP_WORDS]


def schema_text(schema: Any, root: Any = None, parts: Optional[list[str]] = None,
                seen: Optional[set[int]] = None) -> list[str]:
    """Descriptions and property names of a JSON schema, recursively,
    following local ``$ref``s once each."""
    parts = [] if parts is None else parts
    root = schema if root is None else root
    seen = {id(root)} if seen is None else seen
    if not isinstance(schema, dict):
        return parts
    ref = schema.get("$ref")
    if isinstance(ref, str) and ref.startswith("#/"):
        target: Any = root
        for key in ref[2:].split("/"):
            target = target.get(key) if isinstance(target, dict) else None
        if isinstance(target, dict) and id(target) not in seen:
            seen.add(id(target))
            schema_text(target, root, parts, seen)
    if isinstance(schema.get("description"), str):
        parts.append(schema["description"])
    properties = schema.get("properties")
    if isinstance(properties, dict):
        for name, prop in properties.items():
            parts.append(str(name))
            schema_text(prop, root, parts, seen)
    schema_text(schema.get("items"), root, parts, seen)
    for key in ("anyOf", "oneOf", "allOf"):
        for variant in schema.get(key) or ():
            schema_text(variant, root, parts, seen)
    return parts


@dataclass(frozen=True)
class Document:
    """A tool as the ranker sees it."""

    name: str
    text: str
    namespace: str = ""


def document(name: str, description: str = "", parameters: Any = None,
             hint: str = "", namespace: str = "") -> Document:
    """The search text of a tool: its description, search hint and schema."""
    parts = [description or "", hint or ""]
    schema_text(parameters or {}, parts=parts)
    return Document(name=name, text=" ".join(part for part in parts if part and part.strip()),
                    namespace=namespace or "")


def _normalize_name(name: str) -> str:
    return re.sub(r"[^a-z0-9_$]", "_", (name or "").strip().lower())


def _exact(query: str, name: str) -> bool:
    normalized = _normalize_name(name)
    return normalized == query or normalized.endswith(f"__{query}")


def rank(query: str, documents: Iterable[Document], limit: int) -> list[tuple[str, float]]:
    """``[(name, score)]`` of the best ``limit`` documents for ``query``.

    Exact name first, then the number of query terms covered, then score;
    ties keep the documents' order.
    """
    terms = list(dict.fromkeys(tokenize(query)))
    docs = list(documents)
    if not terms or not docs or limit <= 0:
        return []
    indexed = []
    for doc in docs:
        counts: dict[str, int] = {}
        for term in tokenize(doc.text):
            counts[term] = counts.get(term, 0) + 1
        segments = doc.name.split("__")
        indexed.append((
            doc,
            set(tokenize(doc.name)),
            list(dict.fromkeys(tokenize(segments[-1]))),
            counts,
            sum(counts.values()),
            set(tokenize(doc.namespace)),
        ))
    average = (sum(item[4] for item in indexed) / len(indexed)) or 1.0
    idf = {}
    for term in terms:
        frequency = sum(1 for _doc, names, _local, counts, _length, space in indexed
                        if term in names or term in counts or term in space)
        idf[term] = math.log(1 + (len(indexed) - frequency + 0.5) / (frequency + 0.5))
    full_name_bonus = sum(idf.values())
    exact_query = _normalize_name(query)
    matches = []
    for order, (doc, names, local, counts, length, space) in enumerate(indexed):
        norm = K1 * (1 - B + B * length / average)
        score = 0.0
        covered = 0
        for term in terms:
            count = counts.get(term, 0)
            weight = ((NAME_WEIGHT if term in names else 0.0)
                      + (NAMESPACE_WEIGHT if term in space else 0.0)
                      + (count * (K1 + 1)) / (count + norm))
            if weight > 0:
                covered += 1
            score += idf[term] * weight
        if score == 0:
            continue
        if local and all(term in terms for term in local):
            score += full_name_bonus
        matches.append((not _exact(exact_query, doc.name), -covered, -score, order, doc.name, score))
    matches.sort()
    return [(row[4], row[5]) for row in matches[:limit]]


__all__ = ["Document", "NAME_WEIGHT", "NAMESPACE_WEIGHT", "STOP_WORDS", "document", "rank",
           "schema_text", "stem", "tokenize"]
