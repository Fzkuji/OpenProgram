"""Tool schemas and view definitions exposed to retrieval models."""

TOOL_DEFINITIONS = [
    {"type": "function", "function": {
        "name": "bash",
        "description": (
            "Run a read-only command in the visible memory workspace. Supported "
            "commands: find, rg, grep, cat, sed -n, ls, head, tail, wc, sort, "
            "uniq, cut, and pwd. Claude Code manages tool-result context."
        ),
        "parameters": {"type": "object", "properties": {
            "command": {"type": "string"},
        }, "required": ["command"]},
    }},
    {"type": "function", "function": {
        "name": "bm25_search",
        "description": "Rank topic and source memory by sparse lexical relevance.",
        "parameters": {"type": "object", "properties": {
            "query": {"type": "string"},
            "top_k": {"type": "integer", "minimum": 1, "maximum": 10},
            "path_prefix": {"type": "string"},
            "date_from": {"type": "string", "description": "Inclusive lower bound: YYYY, YYYY-MM, or YYYY-MM-DD."},
            "date_to": {"type": "string", "description": "Inclusive upper bound: YYYY, YYYY-MM, or YYYY-MM-DD."},
        }, "required": ["query"]},
    }},
    {"type": "function", "function": {
        "name": "embedding_search",
        "description": "Rank topic and source memory by dense semantic similarity.",
        "parameters": {"type": "object", "properties": {
            "query": {"type": "string"},
            "top_k": {"type": "integer", "minimum": 1, "maximum": 10},
            "date_from": {"type": "string", "description": "Inclusive lower bound: YYYY, YYYY-MM, or YYYY-MM-DD."},
            "date_to": {"type": "string", "description": "Inclusive upper bound: YYYY, YYYY-MM, or YYYY-MM-DD."},
        }, "required": ["query"]},
    }},
    {"type": "function", "function": {
        "name": "memory_search",
        "description": (
            "Rank topic and source memory by fused lexical + semantic "
            "relevance (BM25 and embedding merged with reciprocal rank "
            "fusion). Preferred single search entry point."
        ),
        "parameters": {"type": "object", "properties": {
            "query": {"type": "string"},
            "top_k": {"type": "integer", "minimum": 1, "maximum": 10},
            "path_prefix": {"type": "string"},
            "date_from": {"type": "string", "description": "Inclusive lower bound: YYYY, YYYY-MM, or YYYY-MM-DD."},
            "date_to": {"type": "string", "description": "Inclusive upper bound: YYYY, YYYY-MM, or YYYY-MM-DD."},
        }, "required": ["query"]},
    }},
]

CONDITION_VIEWS = {
    "dual_source": ("topics", "timeline", "sources", "recent"),
    "topic_source": ("topics", "sources", "recent"),
    "timeline_source": ("timeline", "sources", "recent"),
    "dual_no_source": ("topics", "timeline", "recent"),
}
