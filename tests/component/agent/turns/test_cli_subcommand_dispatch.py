"""main() 的 dispatch 不许引用 build_parser 的局部变量.

历史 bug: ``_need_subcommand(p_worker)`` / ``_dispatch_accounts_verb(args,
p_chacct)`` 里的 ``p_*`` 是 build_parser 的局部名, main() 里一律
NameError — 但只在"缺 verb"或对应 dispatch 分支被真正走到时才炸,
所以 15 个命令带病存活了很久 (channels accounts/access/bindings 与
agents 的所有 verb 全部命中). 现在子 parser 经 ``set_defaults
(_cmd_parser=...)`` 盖进 args, 这里对全部路径做 subprocess 冒烟.
"""
from __future__ import annotations

import os
import subprocess
import sys

import pytest

# 缺 verb → argparse 打印该子命令 help 后退出; 只要不是 Traceback 就算过.
MISSING_VERB_COMMANDS = [
    ["logs"],
    ["programs"],
    ["skills"],
    ["plugins"],
    ["sessions"],
    ["subagent"],
    ["memory"],
    ["worker"],
    ["channels"],
    ["channels", "accounts"],
    ["channels", "access"],
    ["channels", "bindings"],
    ["mcp"],
    ["browser"],
    ["agents"],
]


@pytest.mark.parametrize(
    "argv", MISSING_VERB_COMMANDS, ids=[" ".join(c) for c in MISSING_VERB_COMMANDS]
)
def test_missing_verb_prints_help_not_nameerror(argv, tmp_path):
    env = os.environ.copy()
    env["HOME"] = str(tmp_path)
    if sys.platform == "win32":
        env["USERPROFILE"] = str(tmp_path)

    proc = subprocess.run(
        [sys.executable, "-m", "openprogram", *argv],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=60,
        env=env,
    )
    combined = proc.stdout + proc.stderr
    assert "Traceback" not in combined, combined[-800:]
    assert "NameError" not in combined, combined[-800:]
