# Execution authority, permission, and sandbox

The permission model is part of the canonical [Execution Authority and Sandbox Design](sandbox-architecture.html).

That design keeps three contracts separate:

- **Authority** defines who initiated a request and the fixed capability ceiling of its tier.
- **Permission** decides whether one concrete tool operation is allowed, requires owner approval, or is denied.
- **Sandbox** enforces the host resources an approved local process can actually access.

This page is retained as a stable link target for source comments and older design references. It is not a second design specification.
