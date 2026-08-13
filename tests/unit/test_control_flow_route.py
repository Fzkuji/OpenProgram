"""Unit tests for route control flow primitive."""

import pytest
from unittest.mock import patch
from openprogram.agentic_programming.control_flow import route


def test_route_selects_first_option():
    """验证选择第一个选项"""
    with patch("openprogram.agentic_programming.control_flow.route.llm") as mock_llm:
        mock_llm.return_value = "直接迁移是最简单的方式"

        result = route(
            question="选择迁移策略",
            options=["直接迁移", "重构后迁移", "分阶段迁移"]
        )

    assert result == "直接迁移"
    mock_llm.assert_called_once()


def test_route_selects_middle_option():
    """验证选择中间选项"""
    with patch("openprogram.agentic_programming.control_flow.route.llm") as mock_llm:
        mock_llm.return_value = "建议重构后迁移"

        result = route(
            question="选择迁移策略",
            options=["直接迁移", "重构后迁移", "分阶段迁移"]
        )

    assert result == "重构后迁移"


def test_route_with_context():
    """验证带上下文的路由"""
    with patch("openprogram.agentic_programming.control_flow.route.llm") as mock_llm:
        mock_llm.return_value = "分阶段迁移"

        result = route(
            question="选择迁移策略",
            options=["直接迁移", "重构后迁移", "分阶段迁移"],
            context="代码库很大，依赖复杂"
        )

    assert result == "分阶段迁移"
    call_args = mock_llm.call_args[0][0]
    assert "代码库很大，依赖复杂" in call_args


def test_route_fallback_to_first():
    """验证无法匹配时回退到第一个选项"""
    with patch("openprogram.agentic_programming.control_flow.route.llm") as mock_llm:
        mock_llm.return_value = "这个问题很复杂，需要更多信息"

        result = route(
            question="选择策略",
            options=["选项A", "选项B", "选项C"]
        )

    assert result == "选项A"


def test_route_partial_match():
    """验证部分匹配也能识别"""
    with patch("openprogram.agentic_programming.control_flow.route.llm") as mock_llm:
        mock_llm.return_value = "我认为选项B是最合适的"

        result = route(
            question="选择",
            options=["选项A", "选项B", "选项C"]
        )

    assert result == "选项B"
