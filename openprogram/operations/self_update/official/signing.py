"""The release signing key: Ed25519 over the exact bytes of a signed file.

A signature file holds the hex signature and a newline. The desktop shell
checks the same keys (``apps/desktop/main/self-update/update-service.js``).
"""
from __future__ import annotations

from collections.abc import Iterable
import os
from pathlib import Path

from .repository import ReleaseError

# Hex of the raw Ed25519 public keys an update may be signed with. More than
# one allows a rotation. Empty means this build accepts no official update.
RELEASE_PUBLIC_KEYS: tuple[str, ...] = ("e40d13b9572ab80002742e74a4243faa3312a8acec0d08a63ce71999e8531381",)


def sign(data: bytes, private_key_pem: bytes) -> str:
    """The hex signature of ``data``, newline-terminated, as a ``.sig`` file holds it."""
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from cryptography.hazmat.primitives.serialization import load_pem_private_key

    key = load_pem_private_key(private_key_pem, password=None)
    if not isinstance(key, Ed25519PrivateKey):
        raise ReleaseError("the signing key is not an Ed25519 key")
    return key.sign(data).hex() + "\n"


def public_key_hex(private_key_pem: bytes) -> str:
    """The value to add to :data:`RELEASE_PUBLIC_KEYS` for this signing key."""
    from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat, load_pem_private_key

    key = load_pem_private_key(private_key_pem, password=None)
    return key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw).hex()


def generate_signing_key(path: Path) -> str:
    """Write a new Ed25519 private key to ``path``, readable only by its owner.

    Returns the public value. An existing file is never replaced, because
    losing a key strands every installed copy.
    """
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from cryptography.hazmat.primitives.serialization import Encoding, NoEncryption, PrivateFormat

    pem = Ed25519PrivateKey.generate().private_bytes(Encoding.PEM, PrivateFormat.PKCS8, NoEncryption())
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError as exc:
        raise ReleaseError(f"{path} already exists, and a signing key is never replaced; choose another path") from exc
    except OSError as exc:
        raise ReleaseError(f"could not create {path}: {exc}") from exc
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(pem)
    return public_key_hex(pem)


def sign_file(path: Path, private_key_pem: bytes) -> Path:
    """Write ``<path>.sig`` for the file's exact bytes."""
    target = path.with_name(f"{path.name}.sig")
    target.write_text(sign(path.read_bytes(), private_key_pem), encoding="ascii")
    return target


def verify_signature(data: bytes, signature_text: str, keys: Iterable[str] | None = None) -> None:
    """Raise :class:`ReleaseError` unless a trusted key signed exactly ``data``."""
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

    trusted = list(RELEASE_PUBLIC_KEYS if keys is None else keys)
    if not trusted:
        raise ReleaseError("this build has no release signing key, so it accepts no official update")
    try:
        signature = bytes.fromhex(signature_text.strip())
    except ValueError as exc:
        raise ReleaseError("the signature is malformed") from exc
    for key in trusted:
        try:
            Ed25519PublicKey.from_public_bytes(bytes.fromhex(key)).verify(signature, data)
            return
        except (InvalidSignature, ValueError):
            continue
    raise ReleaseError("the signature does not match a trusted key")
