from __future__ import annotations


def test_execution_branch_commands_have_strict_cli_arguments() -> None:
    from openprogram.cli import build_parser

    steer = build_parser().parse_args([
        "execution", "steer", "exec-1", "--expected-version", "3",
        "--message", "Use the new priority.",
    ])
    assert steer.execution_verb == "steer"
    assert steer.message == "Use the new priority."

    fork = build_parser().parse_args([
        "execution", "fork", "exec-1", "--expected-version", "3",
        "--checkpoint-id", "checkpoint-1", "--manifest-id", "manifest-1",
        "--proof-hash", "proof-hash-1",
    ])
    assert fork.execution_verb == "fork"
    assert fork.checkpoint_id == "checkpoint-1"
    assert fork.manifest_id == "manifest-1"
    assert fork.proof_hash == "proof-hash-1"

    retry = build_parser().parse_args([
        "execution", "retry", "exec-1", "--expected-version", "3",
        "--checkpoint-id", "checkpoint-1",
    ])
    assert retry.execution_verb == "retry"
    assert retry.checkpoint_id == "checkpoint-1"
