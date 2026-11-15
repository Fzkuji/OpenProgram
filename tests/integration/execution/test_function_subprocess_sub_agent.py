"""A function running in a real subprocess starts a sub-agent through its parent."""
import json
import os
from pathlib import Path
import subprocess
import sys

_PROGRAM = r'''
import asyncio, json, sys
from pathlib import Path
from types import SimpleNamespace
from openprogram import Agent


class SubAgentProgram(Agent):
    method_options = {'starts_sub_agent': {'name': 'starts_sub_agent', 'tool': True}}

    @staticmethod
    def starts_sub_agent(folder: str):
        from openprogram.runtime.conversation.sub_agent_run import run_agent_turn
        marker = Path(folder) / 'accepted'
        result = run_agent_turn('session', 'look around', 'main', on_accepted=lambda job: marker.write_text(job.id))
        (Path(folder) / 'result').write_text(result.final_text)
        return result.final_text


starts_sub_agent = SubAgentProgram().starts_sub_agent


async def main():
    from openprogram.runtime.conversation.process_runner import run_agent_method_in_subprocess
    from openprogram.runtime.context.session_db import default_db
    from openprogram.runtime.jobs.runner import shared
    from openprogram.runtime.jobs.types import JobStatus
    from openprogram.runtime.permissions.authority import local_owner_authority
    folder = Path(sys.argv[1])

    class WorkerRunner:
        def spawn_job(self, **details):
            assert 'borrow_current_claim' not in details
            details['on_accepted'](SimpleNamespace(id=details['job_id']))
            (folder / 'spawned').write_text(json.dumps({'accepted_first': (folder / 'accepted').exists()}))
            return details['job_id']

        def await_job(self, job_id, timeout=None):
            return SimpleNamespace(status=JobStatus.COMPLETED, head_id='head', result_text='sub-agent reply', error=None)

    shared.get_runner = lambda: WorkerRunner()
    default_db().create_session('session', 'main', work_dir=str(folder))
    result = await asyncio.to_thread(
        run_agent_method_in_subprocess, tool_name='starts_sub_agent', kwargs={'folder': str(folder)},
        session_id='session', anchor_msg_id='anchor', work_dir=str(folder), on_event=lambda _event: None,
        execution_id='execution', authority=local_owner_authority(), timeout_seconds=40,
    )
    print(json.dumps({'result': str(result)}))


if __name__ == '__main__':
    asyncio.run(main())
'''


def test_a_function_subprocess_starts_a_sub_agent_in_the_worker(tmp_path):
    folder = tmp_path / "work"
    folder.mkdir()
    home = tmp_path / "home"
    home.mkdir()
    program = tmp_path / "program.py"
    program.write_text(_PROGRAM)
    finished = subprocess.run(
        [sys.executable, str(program), str(folder)], text=True, capture_output=True, timeout=60,
        env={**os.environ, "HOME": str(home), "OPENPROGRAM_PROFILE": "",
             "PYTHONPATH": str(Path(__file__).resolve().parents[3])},
    )
    assert finished.returncode == 0, finished.stderr
    assert (folder / "result").exists(), finished.stdout + finished.stderr
    assert (folder / "result").read_text() == "sub-agent reply"
    assert json.loads((folder / "spawned").read_text()) == {"accepted_first": True}
