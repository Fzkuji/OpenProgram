from __future__ import annotations

from pathlib import Path
import subprocess
import sys

import pytest

from scripts.check_feature_matrix import MatrixError, check_matrix


ROOT = Path(__file__).resolve().parents[2]
MATRIX = ROOT / "docs/reference/design/feature-matrix.html"


def _hide_the_canonical_score_behind_visible_drift(text: str) -> str:
    return text.replace(
        '<span>OpenProgram 完整确认 <b data-matrix-metric="openprogram-score" '
        'data-value="71">71</b></span>',
        "<span>OpenProgram 完整确认 <b>999</b></span>"
        '<span hidden data-matrix-metric="openprogram-score" '
        'data-value="71">71</span>',
        1,
    )


def _replace_visible_matrix_with_hidden_canonical_copy(text: str) -> str:
    marker = '<table class="fx">'
    start = text.index(marker)
    end = text.index("</table>", start) + len("</table>")
    canonical = text[start:end]
    visible = canonical.replace(marker, '<table class="wide">', 1)
    ssrf = visible.index("Runtime URL 获取 SSRF 防护")
    cell = visible.index('<td class="g0 us">·</td>', ssrf)
    visible = (
        visible[:cell]
        + '<td class="g1 us">●</td>'
        + visible[cell + len('<td class="g0 us">·</td>') :]
    )
    hidden = canonical.replace(marker, '<table class="fx" hidden>', 1)
    return (
        text[:start]
        + visible
        + text[end:].replace("</body>", f"<div hidden>{hidden}</div></body>", 1)
    )


def test_feature_matrix_mechanics_match_displayed_summary() -> None:
    result = check_matrix(MATRIX)

    assert result.feature_count == 160
    assert all(len(row.cells) == 13 for row in result.rows)
    assert all(cell in {"●", "·"} for row in result.rows for cell in row.cells)
    assert result.displayed_metrics == result.recomputed_metrics


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (lambda text: text.replace("●</td>", "◐</td>", 1), "invalid symbol"),
        (
            lambda text: text.replace('<td class="g1">●</td></tr>', "</tr>", 1),
            "13 framework cells",
        ),
        (
            lambda text: text.replace(
                'data-matrix-metric="openprogram-gaps" data-value="',
                'data-matrix-metric="openprogram-gaps" data-value="999',
                1,
            ),
            "openprogram-gaps",
        ),
        (
            lambda text: text.replace(
                'data-matrix-metric="openprogram-score" data-value="',
                'data-matrix-metric="openprogram-score" data-value="999',
                1,
            ),
            "openprogram-score",
        ),
        (
            lambda text: text.replace(
                'data-matrix-metric="openprogram-score" data-value="71">71</b>',
                'data-matrix-metric="openprogram-score" data-value="71">999</b>',
                1,
            ),
            "openprogram-score",
        ),
        (
            lambda text: text.replace(
                '#gaps">2 OpenProgram 未确认的 <span '
                'data-matrix-metric="openprogram-gaps" data-value="86">86</span>',
                '#gaps">2 OpenProgram 未确认的 <span '
                'data-matrix-metric="openprogram-gaps" data-value="86">999</span>',
                1,
            ),
            "openprogram-gaps",
        ),
        (
            lambda text: text.replace(
                'data-matrix-metric="score-openprogram" data-value="71" '
                'x="444" y="12" fill="#4f8ef7" font-size="10" '
                'font-weight="700">71</text>',
                'data-matrix-metric="score-openprogram" data-value="71" '
                'x="444" y="12" fill="#4f8ef7" font-size="10" '
                'font-weight="700">999</text>',
                1,
            ),
            "score-openprogram",
        ),
        (
            lambda text: text.replace(
                'data-matrix-metric="category-integration-score-openprogram" '
                'data-value="1">1</span>',
                'data-matrix-metric="category-integration-score-openprogram" '
                'data-value="1">999</span>',
                1,
            ),
            "category-integration-score-openprogram",
        ),
        (
            lambda text: text.replace(
                'data-matrix-category-gap-rank="1">集成与被集成</span>、'
                '<span data-matrix-category-gap-rank="2">编辑器集成</span>',
                'data-matrix-category-gap-rank="1">编辑器集成</span>、'
                '<span data-matrix-category-gap-rank="2">集成与被集成</span>',
                1,
            ),
            "category gap order",
        ),
        (
            lambda text: text.replace(
                '<th class="us">我们</th><th>claude</th>',
                '<th class="us">claude</th><th>我们</th>',
                1,
            ),
            "framework header",
        ),
        (
            _hide_the_canonical_score_behind_visible_drift,
            "visible matrix metric",
        ),
        (
            lambda text: text.replace(
                'data-matrix-metric="openprogram-score" data-value="71">71</b>',
                'data-matrix-metric="openprogram-score" data-value="999" '
                'data-value="71">71</b>',
                1,
            ),
            "duplicate attribute",
        ),
        (
            _replace_visible_matrix_with_hidden_canonical_copy,
            "canonical feature table",
        ),
    ],
)
def test_feature_matrix_checker_rejects_mechanical_drift(
    tmp_path: Path,
    mutation,
    message: str,
) -> None:
    original = MATRIX.read_text(encoding="utf-8")
    changed = mutation(original)
    assert changed != original
    candidate = tmp_path / "feature-matrix.html"
    candidate.write_text(changed, encoding="utf-8")

    with pytest.raises(MatrixError, match=message):
        check_matrix(candidate)

    completed = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts/check_feature_matrix.py"),
            str(candidate),
        ],
        cwd=ROOT,
        capture_output=True,
        check=False,
        text=True,
    )
    assert completed.returncode == 1
    assert message in completed.stderr
