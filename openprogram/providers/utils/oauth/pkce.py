"""
PKCE utilities for OAuth flows.

Generates code verifier and challenge for OAuth PKCE flows using
Python's hashlib/secrets (mirrors Web Crypto API usage in TypeScript).

Mirrors utils/oauth/pkce.ts
"""

from __future__ import annotations

import base64
import hashlib
import secrets


def generate_pkce() -> tuple[str, str]:
    """Generate PKCE code verifier and challenge.

    Returns:
        Tuple of (verifier, challenge) where challenge is SHA-256 base64url
        of verifier.
    """
    # 32 random bytes → base64url encoded verifier
    verifier_bytes = secrets.token_bytes(32)
    verifier = base64.urlsafe_b64encode(verifier_bytes).rstrip(b"=").decode()

    # SHA-256 of verifier → base64url encoded challenge
    digest = hashlib.sha256(verifier.encode()).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()

    return verifier, challenge
