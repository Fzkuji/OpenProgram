"""Named survey presets: fixed research lines for a field surveyed again and again.

A preset replaces the research lines the planner would write. Each track has a
definition, a boundary and its queries in priority order: the first query is the one
the yearly counts use, and the retrieval runs them in order. The planner still writes
the title, prerequisites, boundary, questions and survey queries, and the baseline
stage may only map gaps onto the preset's tracks, never add a line.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class Track:
    name: str
    definition: str
    boundary: str
    queries: tuple[str, ...]          # most important first


@dataclass(frozen=True)
class Preset:
    name: str
    description: str
    since: int                        # the first year searched unless `since` is given
    tracks: tuple[Track, ...]


PRESETS = {p.name: p for p in (
    Preset(
        "llm_knowledge",
        "2025-2026 LLM papers on how models hold, use and change knowledge, in four tracks.",
        2025,
        (
            Track("Memory architecture",
                  "Persistent and long-horizon memory for LLMs and LLM agents: what is stored outside or "
                  "alongside the weights, in which structure, and how it is read at inference time.",
                  "How memory is built and read; managing its contents over time is Memory lifecycle.",
                  ('"large language model" memory agent long-term',
                   "LLM agent long-term memory architecture",
                   "memory-augmented large language model",
                   "hierarchical memory for LLM agents")),
            Track("Memory lifecycle",
                  "What happens to memory contents over time: retrieval, consolidation, management, "
                  "forgetting, evaluation and security of LLM memory.",
                  "Operations on and evaluation of memory; the memory's structure is Memory architecture.",
                  ("LLM memory retrieval lifecycle taxonomy",
                   "LLM agent memory consolidation and forgetting",
                   "benchmark for LLM agent memory",
                   "LLM memory poisoning and security")),
            Track("Modular experts",
                  "Expert routing, composition, specialization and pluggable components that decide which "
                  "parameters or modules of a language model are active.",
                  "Which components run; changing what a model knows is Knowledge adaptation.",
                  ('"mixture of experts" modular expert routing language model',
                   "pluggable expert modules for LLMs",
                   "LoRA expert composition and routing",
                   "expert specialization in mixture-of-experts LLMs")),
            Track("Knowledge adaptation",
                  "Changing the knowledge stored in a model over time: knowledge editing and continual "
                  "learning of large language models.",
                  "Updates to stored model knowledge; knowledge kept outside the weights is Memory architecture.",
                  ('"knowledge editing" continual learning large language model',
                   "knowledge editing in large language models",
                   "continual learning for large language models",
                   "lifelong model editing without forgetting")),
        ),
    ),
)}


def get(name: str) -> Preset | None:
    """The preset by name; an empty name is no preset, an unknown one raises ValueError."""
    if not name.strip():
        return None
    if name.strip() not in PRESETS:
        raise ValueError(f"preset must be one of {', '.join(PRESETS)}")
    return PRESETS[name.strip()]


def lines(preset: Preset) -> list[dict]:
    """The preset's tracks as research lines, with ids L1, L2, ..."""
    return [{"id": f"L{i}", "name": t.name, "definition": t.definition, "boundary": t.boundary,
             "queries": list(t.queries)} for i, t in enumerate(preset.tracks, 1)]


__all__ = ["PRESETS", "Preset", "Track", "get", "lines"]
