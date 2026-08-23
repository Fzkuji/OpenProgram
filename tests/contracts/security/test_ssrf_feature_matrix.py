from __future__ import annotations

from pathlib import Path

import pytest

from scripts.check_feature_matrix import MatrixError, check_matrix


ROOT = Path(__file__).resolve().parents[3]
MATRIX = ROOT / "docs/reference/design/feature-matrix.html"


def test_ssrf_matrix_evidence_matches_integrated_snapshot() -> None:
    result = check_matrix(MATRIX)

    assert result.feature_count == 160
    assert result.openprogram_score == 83.5
    assert result.openprogram_gaps == 68
    assert result.openprogram_only == 6


def _replace_in_ssrf_row(text: str, old: str, new: str) -> str:
    start = text.index("私网访问与 SSRF 防护")
    end = text.index("</tr>", start)
    return text[:start] + text[start:end].replace(old, new, 1) + text[end:]


@pytest.mark.parametrize(
    ("old", "new", "message"),
    [
        ("be5eaa3c", "cafebabe", "SSRF snapshot"),
        ('class="g2 us">◐', 'class="g1 us">●', "SSRF status"),
    ],
)
def test_ssrf_matrix_checker_rejects_stale_evidence(
    tmp_path: Path,
    old: str,
    new: str,
    message: str,
) -> None:
    original = MATRIX.read_text(encoding="utf-8")
    changed = _replace_in_ssrf_row(original, old, new)
    assert changed != original
    candidate = tmp_path / "feature-matrix.html"
    candidate.write_text(changed, encoding="utf-8")

    with pytest.raises(MatrixError, match=message):
        check_matrix(candidate)
