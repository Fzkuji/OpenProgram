"""Unit tests for conditional control flow primitive."""

import pytest
from unittest.mock import Mock, patch
from openprogram.agentic_programming.control_flow import conditional


def test_conditional_executes_if_true():
    """验证条件为真时执行 if_true 分支"""
    if_true = Mock(return_value="详细分析结果")
    if_false = Mock(return_value="简要分析")

    with patch("openprogram.agentic_programming.control_flow.conditional.llm") as mock_llm:
        mock_llm.return_value = "YES, 条件满足"

        result = conditional(
            condition="是否需要详细分析",
            context="代码库很复杂",
            if_true=if_true,
            if_false=if_false
        )

    assert result == "详细分析结果"
    if_true.assert_called_once()
    if_false.assert_not_called()


def test_conditional_executes_if_false():
    """验证条件为假时执行 if_false 分支"""
    if_true = Mock(return_value="详细分析")
    if_false = Mock(return_value="简要分析结果")

    with patch("openprogram.agentic_programming.control_flow.conditional.llm") as mock_llm:
        mock_llm.return_value = "NO, 不需要"

        result = conditional(
            condition="是否需要详细分析",
            context="代码库很简单",
            if_true=if_true,
            if_false=if_false
        )

    assert result == "简要分析结果"
    if_false.assert_called_once()
    if_true.assert_not_called()


def test_conditional_without_context():
    """验证没有上下文的条件判定"""
    if_true = Mock(return_value="true branch")
    if_false = Mock(return_value="false branch")

    with patch("openprogram.agentic_programming.control_flow.conditional.llm") as mock_llm:
        mock_llm.return_value = "YES"

        result = conditional(
            condition="是否继续",
            if_true=if_true,
            if_false=if_false
        )

    assert result == "true branch"
    call_args = mock_llm.call_args[0][0]
    assert "上下文" not in call_args or "上下文：\n" in call_args


def test_conditional_only_if_true():
    """验证只有 if_true 分支的情况"""
    if_true = Mock(return_value="true result")

    with patch("openprogram.agentic_programming.control_flow.conditional.llm") as mock_llm:
        mock_llm.return_value = "YES"

        result = conditional(
            condition="条件",
            if_true=if_true
        )

    assert result == "true result"


def test_conditional_only_if_false():
    """验证只有 if_false 分支的情况"""
    if_false = Mock(return_value="false result")

    with patch("openprogram.agentic_programming.control_flow.conditional.llm") as mock_llm:
        mock_llm.return_value = "NO"

        result = conditional(
            condition="条件",
            if_false=if_false
        )

    assert result == "false result"


def test_conditional_no_branches_returns_empty():
    """验证没有分支时返回空字符串"""
    with patch("openprogram.agentic_programming.control_flow.conditional.llm") as mock_llm:
        mock_llm.return_value = "YES"

        result = conditional(condition="条件")

    assert result == ""


def test_conditional_case_insensitive():
    """验证 YES/NO 判定大小写不敏感"""
    if_true = Mock(return_value="true")
    if_false = Mock(return_value="false")

    with patch("openprogram.agentic_programming.control_flow.conditional.llm") as mock_llm:
        mock_llm.return_value = "yes, definitely"

        result = conditional(
            condition="条件",
            if_true=if_true,
            if_false=if_false
        )

    assert result == "true"
    if_true.assert_called_once()
