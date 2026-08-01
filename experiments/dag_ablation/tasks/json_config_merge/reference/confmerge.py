import copy
import json


def deep_merge(base, override):
    out = copy.deepcopy(base)
    for k, v in override.items():
        if v is None:
            out.pop(k, None)
        elif isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = deep_merge(out[k], v)
        else:
            out[k] = copy.deepcopy(v)
    return out


def load_layers(*paths):
    out = {}
    for p in paths:
        with open(p) as f:
            out = deep_merge(out, json.load(f))
    return out
