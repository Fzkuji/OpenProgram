"""What extends OpenProgram from outside: plugins, skills and MCP servers.

Importing any of them fills the runtime's ports for them
(``lower_layers``): the runtime sits below and never imports them.
"""
from .lower_layers import register as _register_lower_layer_ports

_register_lower_layer_ports()
