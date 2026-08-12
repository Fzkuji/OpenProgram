from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

CredentialStatus = Literal[
    "permission", "symlink", "not_regular", "foreign_owner", "stale_temporary"
]


@dataclass(frozen=True)
class CredentialFinding:
    kind: str
    relative_path: str
    status: CredentialStatus
    repairable: bool
    repaired: bool = False

    def as_dict(self) -> dict[str, object]:
        return {
            "kind": self.kind,
            "relative_path": self.relative_path,
            "status": self.status,
            "repairable": self.repairable,
            "repaired": self.repaired,
        }


def audit_credentials(*, root: Path) -> list[CredentialFinding]:
    return []


def repair_credentials(*, root: Path) -> list[CredentialFinding]:
    return []
