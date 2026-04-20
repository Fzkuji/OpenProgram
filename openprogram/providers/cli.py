"""
OAuth login CLI for pi-ai.

Provides interactive and scriptable OAuth login for all supported providers.

Mirrors cli.ts
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any

import typer

from openprogram.providers.utils.oauth import get_oauth_provider, get_oauth_providers
from openprogram.providers.utils.oauth.types import OAuthAuthInfo, OAuthLoginCallbacks, OAuthPrompt

_APP = typer.Typer(name="pi-ai", help="OAuth login CLI for pi-ai providers")
_AUTH_FILE = Path("auth.json")


def _load_auth() -> dict[str, Any]:
    if not _AUTH_FILE.exists():
        return {}
    try:
        return json.loads(_AUTH_FILE.read_text())
    except Exception:
        return {}


def _save_auth(auth: dict[str, Any]) -> None:
    _AUTH_FILE.write_text(json.dumps(auth, indent=2))


async def _do_login(provider_id: str) -> None:
    provider = get_oauth_provider(provider_id)
    if not provider:
        typer.echo(f"Unknown provider: {provider_id}", err=True)
        raise typer.Exit(1)

    def on_auth(info: OAuthAuthInfo) -> None:
        typer.echo(f"\nOpen this URL in your browser:\n{info.url}")
        if info.instructions:
            typer.echo(info.instructions)
        typer.echo()

    async def on_prompt(p: OAuthPrompt) -> str:
        placeholder = f" ({p.placeholder})" if p.placeholder else ""
        return typer.prompt(f"{p.message}{placeholder}")

    callbacks = OAuthLoginCallbacks(
        on_auth=on_auth,
        on_prompt=on_prompt,
        on_progress=lambda msg: typer.echo(msg),
    )

    credentials = await provider.login(callbacks)
    auth = _load_auth()
    auth[provider_id] = {"type": "oauth", **credentials.to_dict()}
    _save_auth(auth)
    typer.echo(f"\nCredentials saved to {_AUTH_FILE}")


@_APP.command("login")
def login_cmd(
    provider: str | None = typer.Argument(None, help="Provider ID to login to"),
) -> None:
    """Login to an OAuth provider."""
    providers = get_oauth_providers()

    if not provider:
        typer.echo("Select a provider:\n")
        for i, p in enumerate(providers, 1):
            typer.echo(f"  {i}. {p.name}")
        typer.echo()
        choice = typer.prompt(f"Enter number (1-{len(providers)})")
        try:
            idx = int(choice) - 1
            if idx < 0 or idx >= len(providers):
                raise ValueError()
        except ValueError:
            typer.echo("Invalid selection", err=True)
            raise typer.Exit(1)
        provider = providers[idx].id

    if not any(p.id == provider for p in providers):
        typer.echo(f"Unknown provider: {provider}", err=True)
        typer.echo("Use 'pi-ai list' to see available providers", err=True)
        raise typer.Exit(1)

    typer.echo(f"Logging in to {provider}...")
    asyncio.run(_do_login(provider))


@_APP.command("list")
def list_cmd() -> None:
    """List available OAuth providers."""
    providers = get_oauth_providers()
    typer.echo("Available OAuth providers:\n")
    for p in providers:
        typer.echo(f"  {p.id:<20} {p.name}")


def main() -> None:
    _APP()


if __name__ == "__main__":
    main()
