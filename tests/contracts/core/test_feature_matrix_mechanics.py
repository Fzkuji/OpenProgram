from __future__ import annotations

import ast
import html
from pathlib import Path
import re
import subprocess
import sys

import pytest

from scripts.check_feature_matrix import MatrixError, check_matrix


ROOT = Path(__file__).resolve().parents[3]
MATRIX = ROOT / "docs/reference/design/feature-matrix.html"
FRAMEWORK_COMPARISON = ROOT / "docs/reference/design/framework-comparison.html"


@pytest.fixture(params=["", ".zh"])
def matrix_path(request) -> Path:
    return MATRIX.with_name(f"feature-matrix{request.param}.html")


def _label(text: str, chinese: str, english: str) -> str:
    return english if "<title>Feature matrix</title>" in text else chinese


def _replace_number(text: str, chinese: str, english: str, number: str) -> str:
    label = _label(text, chinese, english)
    return text.replace(label, label.replace(number, "999"), 1)


def _demote_json_schema_row(text: str) -> str:
    name = _label(text, "按 JSON schema 约束输出", "JSON-schema-constrained output")
    start = text.index(f'<tr><th scope="row">{name}')
    old = "<td>●</td>"
    cell = text.index(old, start)
    return text[:cell] + "<td>◐</td>" + text[cell + len(old) :]


def _rename_section_item(text: str, section: str, old: str) -> str:
    start = text.index(f'<h2 id="{section}"')
    end = text.index("<h2", start + 4)
    section_text = text[start:end]
    changed = section_text.replace(old, "伪造的明细项", 1)
    assert changed != section_text
    return text[:start] + changed + text[end:]


def test_feature_matrix_published_values_match_canonical_table(matrix_path: Path) -> None:
    result = check_matrix(matrix_path)

    assert result.feature_count == 160
    assert result.openprogram_score == 92.0
    assert result.openprogram_gaps == 59
    assert result.openprogram_only == 6
    assert result.json_schema_status == "●"
    assert result.snapshot == "2fb471b3"


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (lambda text: text.replace("2fb471b3", "deadbeef"), "snapshot"),
        (
            lambda text: re.sub(
                r"(?:main|integrated candidate)@[0-9a-f]{8}",
                "integrated candidate@cafebabe",
                text,
            ),
            "integration snapshot",
        ),
        (
            lambda text: _replace_number(
                text,
                '<th scope="row">我们</th><td>92.0</td>',
                '<th scope="row">Ours</th><td>92.0</td>',
                "92.0",
            ),
            "score",
        ),
        (
            lambda text: _replace_number(text, "功能矩阵、92.0 分", "Feature matrix, 92.0 score", "92.0"),
            "score summary",
        ),
        (
            lambda text: _replace_number(text, "参考列已确认的 59 项", "59 features unconfirmed in OpenProgram", "59"),
            "gaps",
        ),
        (
            lambda text: _replace_number(text, "仅 OpenProgram 确认的 6 项", "6 features confirmed only in OpenProgram", "6"),
            "OpenProgram-only",
        ),
        (
            lambda text: _replace_number(
                text,
                '<th scope="row">编辑器集成</th><td>50%</td>',
                '<th scope="row">Editor integration</th><td>50%</td>',
                "50",
            ),
            "category point",
        ),
        (
            lambda text: _replace_number(
                text,
                "<td>6%–78%, 中位数 44%</td>",
                "<td>6%–78%, median 44%</td>",
                "44",
            ),
            "category point",
        ),
        (
            lambda text: _replace_number(text, "01 编辑器集成 9 项", "01 Editor integration 9 features", "9"),
            "category heading count",
        ),
        (
            lambda text: text.replace("</td><td>● buddy</td></tr>", "</td><td>● nobody</td></tr>", 1),
            "invalid status",
        ),
        (
            lambda text: text.replace("</td><td>● buddy</td></tr>", "</td><td>● buddy, buddy</td></tr>", 1),
            "twice",
        ),
        (
            lambda text: text.replace("</td><td>● buddy</td></tr>", "</td><td>● claude</td></tr>", 1),
            "score",
        ),
        (
            lambda text: text.replace("<details>\n<summary>01 ", "<details hidden>\n<summary>01 ", 1),
            "visible",
        ),
        (
            lambda text: _rename_section_item(text, "gaps", _label(text, "终端快捷键自动配置", "Automatic terminal-shortcut configuration")),
            "gap detail",
        ),
        (
            lambda text: _replace_number(
                text,
                '进入后续评估</b> <span>32 项',
                'For further assessment</b> <span>32 features',
                "32",
            ),
            "gap detail group count",
        ),
        (
            lambda text: _rename_section_item(
                text, "ours", _label(text, "函数调用树写进<br>同一张会话图", "Function-call trees in<br>the same session graph")
            ),
            "OpenProgram-only detail",
        ),
        (_demote_json_schema_row, "JSON Schema"),
    ],
)
def test_feature_matrix_checker_rejects_published_drift(
    tmp_path: Path,
    matrix_path: Path,
    mutation,
    message: str,
) -> None:
    original = matrix_path.read_text(encoding="utf-8")
    changed = mutation(original)
    assert changed != original
    candidate = tmp_path / "feature-matrix.html"
    candidate.write_text(changed, encoding="utf-8")

    with pytest.raises(MatrixError, match=message):
        check_matrix(candidate)

    completed = subprocess.run(
        [sys.executable, str(ROOT / "scripts/check_feature_matrix.py"), str(candidate)],
        cwd=ROOT,
        capture_output=True,
        check=False,
        text=True,
    )
    assert completed.returncode == 1
    assert message in completed.stderr


def test_framework_documents_pin_the_same_current_release_versions() -> None:
    expected = {
        "anomalyco/opencode": "v1.18.25",
        "anthropics/claude-code": "v2.1.252",
        "openai/codex": "rust-v0.152.0",
        "openclaw/openclaw": "v2026.8.1",
    }

    def versions(path: Path) -> dict[str, str]:
        found: dict[str, set[str]] = {}
        for repo, tag in re.findall(
            r"https://github\.com/([^/]+/[^/]+)/releases/tag/([^\"<]+)",
            path.read_text(encoding="utf-8"),
        ):
            found.setdefault(repo, set()).add(tag)
        assert all(len(tags) == 1 for tags in found.values())
        return {repo: next(iter(tags)) for repo, tags in found.items()}

    for path in (MATRIX, FRAMEWORK_COMPARISON):
        assert versions(path) == expected
        assert versions(path.with_name(path.stem + ".zh.html")) == expected


def test_framework_documents_publish_the_current_event_count() -> None:
    module = ast.parse(
        (ROOT / "openprogram/runtime/events/registry.py").read_text(encoding="utf-8")
    )
    event_dict = next(
        node.value
        for node in module.body
        if isinstance(node, ast.AnnAssign)
        and isinstance(node.target, ast.Name)
        and node.target.id == "EVENTS"
    )
    assert isinstance(event_dict, ast.Dict)
    count = len(event_dict.keys)

    matrix = MATRIX.read_text(encoding="utf-8")
    comparison = FRAMEWORK_COMPARISON.read_text(encoding="utf-8")
    assert f"ours {count}" in matrix.casefold()
    assert comparison.count(f"{count} events") == 2
    chinese_matrix = MATRIX.with_name("feature-matrix.zh.html").read_text(encoding="utf-8")
    chinese_comparison = FRAMEWORK_COMPARISON.with_name("framework-comparison.zh.html").read_text(encoding="utf-8")
    assert f"我们 {count} 个" in chinese_matrix
    assert chinese_comparison.count(f"{count}事件") + chinese_comparison.count(f"{count} 个事件") == 2


def test_runtime_docs_publish_structured_return_and_error_contracts() -> None:
    english = html.unescape((ROOT / "docs/reference/api/runtime.html").read_text(encoding="utf-8"))
    chinese = html.unescape((ROOT / "docs/reference/api/runtime.zh.html").read_text(encoding="utf-8"))

    for text in (english, chinese):
        assert "StructuredOutputSchemaError" in text
        assert "StructuredOutputValidationError" in text
        assert "StructuredOutputGenerationError" in text
        assert "StructuredOutputUnsupportedError" in text
        assert "invalid_schema" in text
        assert "invalid_json" in text
        assert "validation_failed" in text
        assert "missing_submission" in text
        assert "mixed_submission" in text
        assert "incomplete" in text
        assert "refusal" in text
        assert "unsupported" in text
        assert "response_format=None" in text
        assert "Python JSON" in text

    for text in (english, chinese):
        assert 'execution_kind="agent") -> Any' in text
        assert "timeout_s=None, on_retry=None," in text
        assert "stream_fn=None) -> str" not in text
        assert "timeout_s=None, on_retry=None) -> str" not in text
        assert '_call(content, model="default", response_format=None)' in text
        assert "_async_call(...)" in text
        assert 'response_format=None) -> str' not in text
    assert '<code translate="no">response_format=None</code></th><td>Reply' in english
    assert '<code translate="no">response_format=None</code></th><td>回复' in chinese
