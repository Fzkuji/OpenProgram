"""Describe literal fields consumed by existing business command handlers."""
import ast
import inspect
import textwrap


def arguments_for(handler):
    properties = {}
    try:
        tree = ast.parse(textwrap.dedent(inspect.getsource(handler)))
    except (OSError, TypeError, SyntaxError):
        return {'type': 'object'}
    types = {str: 'string', bool: 'boolean', int: 'integer', float: 'number', list: 'array', dict: 'object'}
    for node in ast.walk(tree):
        key, default = None, None
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and isinstance(node.func.value, ast.Name) and node.func.value.id == 'cmd'
                and node.func.attr == 'get' and node.args and isinstance(node.args[0], ast.Constant)):
            key = node.args[0].value
            if len(node.args) > 1:
                try:
                    default = ast.literal_eval(node.args[1])
                except (ValueError, TypeError):
                    pass
        elif isinstance(node, ast.Subscript) and isinstance(node.value, ast.Name) and node.value.id == 'cmd' and isinstance(node.slice, ast.Constant):
            key = node.slice.value
        if isinstance(key, str) and key != 'action':
            properties[key] = {'type': types[type(default)], 'default': default} if type(default) in types else {}
    return {'type': 'object', 'properties': properties,
            'description': 'Fields read by this handler. The canonical backend validates required fields and action-specific constraints.'}
