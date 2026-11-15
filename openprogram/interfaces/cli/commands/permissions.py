"""Read-only diagnostics for the same rules used by tool enforcement."""
from __future__ import annotations

import json
from pathlib import Path
import sys

from openprogram.runtime.permissions.modes import PermissionRules
from openprogram.runtime.permissions.rule import parse_rule

_DECISIONS = {'deny', 'ask', 'allow', 'unmatched'}


def explain_rules(rules, tool_name: str, args: dict) -> dict:
    """Diagnose existing enforcement without changing its implementation."""
    from openprogram.runtime.permissions.policy import match_rule
    matches = []
    for decision in ('deny', 'ask', 'allow'):
        for raw in getattr(rules, decision):
            single = PermissionRules(**{decision: [raw]})
            if match_rule(single, tool_name, args)[0] == decision:
                matches.append({'decision': decision, 'rule': raw})
    winning, _rule = match_rule(rules, tool_name, args)
    return {'scope': 'permission_rules', 'tool': tool_name,
            'decision': winning or 'unmatched', 'matches': matches}


def _rules(path):
    value = json.loads(Path(path).read_text(encoding='utf-8'))
    if not isinstance(value, dict) or set(value) - {'allow', 'deny', 'ask'}:
        raise ValueError('rules must be an object with allow, ask and deny lists')
    for items in value.values():
        if not isinstance(items, list) or any(not isinstance(item, str) or not item.strip() for item in items):
            raise ValueError('each rule list must contain nonempty strings')
        for item in items:
            parse_rule(item)
    return PermissionRules(**value)


def _case(rules, tool, args, expected=None):
    if not isinstance(tool, str) or not tool.strip() or not isinstance(args, dict):
        raise ValueError('each operation requires a tool name and an args object')
    if expected is not None and (not isinstance(expected, str) or expected not in _DECISIONS):
        raise ValueError('expected must be deny, ask, allow or unmatched')
    result = explain_rules(rules, tool, args)
    if expected is not None:
        result.update(expected=expected, passed=result['decision'] == expected)
    return result


def run(args) -> int:
    try:
        rules = _rules(args.rules)
        if args.permission_action == 'check':
            result = _case(rules, args.tool, json.loads(args.args), args.expect)
            ok = result.get('passed', True)
        else:
            cases = json.loads(Path(args.cases).read_text(encoding='utf-8'))
            if not isinstance(cases, list) or not cases:
                raise ValueError('cases must be a nonempty JSON array')
            results = []
            for case in cases:
                if not isinstance(case, dict) or set(case) != {'tool', 'args', 'expected'}:
                    raise ValueError('each case must contain exactly tool, args and expected')
                if case['expected'] is None:
                    raise ValueError('each case requires an expected decision')
                results.append(_case(rules, case['tool'], case['args'], case['expected']))
            passed = sum(item['passed'] for item in results)
            result = {'scope': 'permission_rules', 'passed': passed, 'total': len(results), 'cases': results}
            ok = passed == len(results)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if ok else 1
    except (ValueError, OSError) as exc:
        print(f'Permission diagnostic error: {exc}', file=sys.stderr)
        return 2
