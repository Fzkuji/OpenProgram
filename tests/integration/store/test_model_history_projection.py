"""Independent processes project one immutable model receipt without copying it."""
import json
import os
from pathlib import Path
import subprocess
import sys


SCRIPT = r'''
import json,sys
from pathlib import Path
from openprogram.store.project.project_store import resolve_project,get_project
from openprogram.store.session.session_store import SessionStore
from openprogram.store.snapshot.checkpoint import CheckpointStore
from openprogram.store.document_history import DocumentHistory
mode,argument=sys.argv[1:]
store=SessionStore()
try:
    if mode=='setup':
        project=resolve_project(argument)
        store.create_session('concurrent',agent_id='main',project_id=project.id)
        path=Path(project.path)/'a.docx'
        path.write_bytes(b'before\0')
        journal=CheckpointStore(store._session_dir('concurrent'))
        journal.backup_before_edit('turn',str(path),project_locator={
            'project_id':project.id,'path':'a.docx','recorded_root':project.path,
            'directory_identity':project.directory_identity,'location_revision':project.location_revision})
        path.write_bytes(b'after\xff')
        journal.commit_after_edit('turn',str(path))
        print(json.dumps({'project':project.id}))
    else:
        history=DocumentHistory()
        for _ in range(3):
            history.register_model_turn('concurrent','turn',session_store=store)
        page=history.list(argument,'a.docx')
        assert len(page['entries'])==1,page
        version=page['entries'][0]['version_id']
        assert history.content(argument,'a.docx',version)==b'after\xff'
        assert not (history._dir(argument,'a.docx')/'operations').exists()
        print(json.dumps({'version':version}))
finally:
    store.close()
'''


def test_two_processes_project_one_retained_model_version(tmp_path):
    home=tmp_path / "home"
    home.mkdir()
    project=tmp_path / "project"
    project.mkdir()
    env={**os.environ,"HOME":str(home),"USERPROFILE":str(home),"OPENPROGRAM_PROFILE":""}
    cwd=Path(__file__).resolve().parents[3]
    setup=subprocess.run([sys.executable,"-c",SCRIPT,"setup",str(project)],cwd=cwd,env=env,
                         text=True,capture_output=True,timeout=30,check=True)
    project_id=json.loads(setup.stdout.strip().splitlines()[-1])["project"]
    children=[]
    try:
        children=[subprocess.Popen([sys.executable,"-c",SCRIPT,"project",project_id],cwd=cwd,env=env,
                                   text=True,stdout=subprocess.PIPE,stderr=subprocess.PIPE) for _ in range(2)]
        versions=[]
        for child in children:
            stdout,stderr=child.communicate(timeout=30)
            assert child.returncode==0,stderr
            versions.append(json.loads(stdout.strip().splitlines()[-1])["version"])
        assert versions[0]==versions[1]
    finally:
        for child in children:
            if child.poll() is None:
                child.kill()
                child.communicate(timeout=10)
