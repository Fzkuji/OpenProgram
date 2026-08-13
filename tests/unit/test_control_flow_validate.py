"""Unit tests for validate_and_retry control flow primitive."""

import pytest
from unittest.mock import Mock, patch
from openprogram.agentic_programming.control_flow import validate_and_retry


def test_validate_success_first_try():
    """验证第一次就成功的情况"""
    action = Mock(return_value="file1.py\nfile2.py\nfile3.py")
    retry = Mock(return_value="more files")

    with patch("openprogram.agentic_programming.control_flow.validate.llm") as mock_llm:
        mock_llm.return_value = "YES, there are 3 files"

        result = validate_and_retry(
            action=action,
            check="文件数量 >= 3",
            retry=retry,
            max_retries=2
        )

    assert result == "file1.py\nfile2.py\nfile3.py"
    action.assert_called_once()
    retry.assert_not_called()
    mock_llm.assert_called_once()


def test_validate_retry_once():
    """验证需要重试一次的情况"""
    action = Mock(return_value="file1.py")
    retry = Mock(return_value="file1.py\nfile2.py\nfile3.py")

    with patch("openprogram.agentic_programming.control_flow.validate.llm") as mock_llm:
        mock_llm.side_effect = [
            "NO, only 1 file",
            "YES, now 3 files"
        ]

        result = validate_and_retry(
            action=action,
            check="文件数量 >= 3",
            retry=retry,
            max_retries=2
        )

    assert result == "file1.py\nfile2.py\nfile3.py"
    action.assert_called_once()
    retry.assert_called_once()
    assert mock_llm.call_count == 2


def test_validate_max_retries_exhausted():
    """验证重试次数用尽的情况"""
    action = Mock(return_value="file1.py")
    retry = Mock(return_value="file1.py\nfile2.py")

    with patch("openprogram.agentic_programming.control_flow.validate.llm") as mock_llm:
        mock_llm.return_value = "NO, still not enough"

        result = validate_and_retry(
            action=action,
            check="文件数量 >= 3",
            retry=retry,
            max_retries=2
        )

    assert result == "file1.py\nfile2.py"
    action.assert_called_once()
    assert retry.call_count == 2
    assert mock_llm.call_count == 3


def test_validate_case_insensitive():
    """验证 YES/NO 判定大小写不敏感"""
    action = Mock(return_value="result")
    retry = Mock()

    with patch("openprogram.agentic_programming.control_flow.validate.llm") as mock_llm:
        mock_llm.return_value = "yes, it's good"

        result = validate_and_retry(
            action=action,
            check="is good",
            retry=retry
        )

    assert result == "result"
    retry.assert_not_called()
