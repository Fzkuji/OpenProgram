"""Native acceptance cannot leave subprocesses behind a successful receipt."""
import os
import signal

import pytest

from tests.component.self_update.test_candidate_checks import (
    _test_plan, live,  # noqa: F401
)
from tests.component.self_update.test_native_checks import installed_cli, native_verifier  # noqa: F401
from tests.component.self_update.test_system_probe import live as http_live  # noqa: F401
from tests.component.self_update.test_verification_channel import (
    consume, store_fixture, verifier,  # noqa: F401
)


_SCRIPTS = {
    kind: (
        "import subprocess,sys\n"
        "p=subprocess.Popen([sys.executable,'-I','-B','-c','import time;time.sleep(30)'], "
        f"start_new_session={detached}, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)\n"
        "print('CHILD_PID='+str(p.pid),flush=True)\n"
    ) for kind, detached in (("spawn", False), ("detached", True))
}
_SCRIPTS["fork"] = (
    "import os,time\n"
    "pid=os.fork()\n"
    "if pid == 0:\n"
    " os.setsid();os.close(1);os.close(2);time.sleep(30);os._exit(0)\n"
    "print('CHILD_PID='+str(pid),flush=True)\n"
)
_SCRIPTS["posix_spawn"] = (
    "import os,sys\n"
    "pid=os.posix_spawn(sys.executable,[sys.executable,'-I','-B','-c','import time;time.sleep(30)'], "
    "os.environ,setsid=True,file_actions=[(os.POSIX_SPAWN_OPEN,fd,'/dev/null',os.O_WRONLY,0) for fd in (1,2)])\n"
    "print('CHILD_PID='+str(pid),flush=True)\n"
)


@pytest.mark.parametrize("verifier", [_test_plan()], indirect=True)
@pytest.mark.parametrize("live", _SCRIPTS.values(), ids=_SCRIPTS.keys(), indirect=True)
def test_registered_native_check_rejects_process_creation(native_verifier):
    v = native_verifier
    child_pids = []
    try:
        v.run()
        assert not v.control["tool_result"].is_error, v.control["tool_result"]
        observed = v.control["observed"]
        # Baseline execution may create a real child. Record only this script's
        # explicitly returned PID and always terminate it before test teardown.
        child_pids = [int(line.removeprefix("CHILD_PID="))
                      for line in observed["body"].splitlines() if line.startswith("CHILD_PID=")]
        verdict = consume(v)["verdict"]
        assert observed["status"] != 0, "native verification allowed a subprocess"
        assert "Operation not permitted" in observed["body"]
        assert not child_pids
        assert verdict == "inconclusive"
        assert "single-process" in v.control["prompt"]
        assert not list((v.store.root / v.request.update_id).glob("native-check-*"))
    finally:
        for pid in child_pids:
            try:
                os.kill(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
