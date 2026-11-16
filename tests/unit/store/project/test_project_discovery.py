from pathlib import Path
import shutil

from openprogram.state.store.project import project_store as projects
from openprogram.state.store.project.discovery import discover_moved_projects


def test_discovers_renamed_folder_preserving_project(tmp_path, monkeypatch):
    monkeypatch.setattr('openprogram.state.paths.get_state_dir', lambda: str(tmp_path / 'state'))
    monkeypatch.setattr('openprogram.state.store.session.session_store.default_store', lambda: type('Store', (), {'relocate_project_sessions': lambda *args, **kwargs: 0})())
    old = tmp_path / 'old'; old.mkdir()
    project = projects.resolve_project(old)
    new = tmp_path / 'renamed'; old.rename(new)
    assert discover_moved_projects() == [project.id]
    assert projects.get_project(project.id).path == str(new)
    assert projects.resolve_project(new).id == project.id
    old.mkdir()
    replacement = projects.resolve_project(old)
    assert replacement.id != project.id
    assert replacement.path == str(old)
    assert projects.resolve_project(old).id == replacement.id


def test_ambiguous_session_copies_are_not_claimed(tmp_path, monkeypatch):
    monkeypatch.setattr('openprogram.state.paths.get_state_dir', lambda: str(tmp_path / 'state'))
    old = tmp_path / 'old'; old.mkdir()
    project = projects.resolve_project(old)
    projects.bind_session('s1', project.id)
    (old / '.openprogram/sessions/s1').mkdir(parents=True)
    shutil.copytree(old, tmp_path / 'copy')
    old.rename(tmp_path / 'moved')
    relocated = discover_moved_projects()
    assert relocated == []
    assert Path(projects.get_project(project.id).path) == old
    monkeypatch.setattr('openprogram.state.store.session.session_store.default_store', lambda: type('Store', (), {'relocate_project_sessions': lambda *args, **kwargs: 0})())
    projects.relocate_project(project.id, tmp_path / 'moved', require_identity=True)
    assert Path(projects.get_project(project.id).path) == (tmp_path / 'moved')
    copy = projects.resolve_project(tmp_path / 'copy')
    assert copy.id != project.id


def test_incomplete_search_and_existing_original_do_not_relocate(tmp_path, monkeypatch):
    monkeypatch.setattr('openprogram.state.paths.get_state_dir', lambda: str(tmp_path / 'state'))
    old = tmp_path / 'old'; old.mkdir()
    project = projects.resolve_project(old)
    assert discover_moved_projects() == []
    old.rename(tmp_path / 'new')
    assert discover_moved_projects(max_directories=1) == []
    assert projects.get_project(project.id).path == str(old)


def test_missing_folder_keeps_session_evidence_during_list_refresh(tmp_path, monkeypatch):
    monkeypatch.setattr('openprogram.state.paths.get_state_dir', lambda: str(tmp_path / 'state'))
    old = tmp_path / 'old'; old.mkdir()
    project = projects.resolve_project(old)
    projects.bind_session('s1', project.id)
    old.rename(tmp_path / 'new')
    assert projects.prune_sessions(set()) == 0
    assert projects.get_project(project.id).session_ids == ['s1']


def test_a_removed_project_made_again_takes_the_new_name(tmp_path, monkeypatch):
    """Making a removed project again under a new name showed the old one."""
    monkeypatch.setattr('openprogram.state.paths.get_state_dir', lambda: str(tmp_path / 'state'))
    folder = tmp_path / 'work'; folder.mkdir()
    first = projects.resolve_project(folder, name='First')
    projects.set_project_hidden(first.id, True)
    again = projects.resolve_project(folder, name='Second')
    assert (again.id, again.name, again.hidden) == (first.id, 'Second', False)
    assert projects.get_project(first.id).name == 'Second'
    # Opening a project that is shown keeps the name it has.
    assert projects.resolve_project(folder, name='Third').name == 'Second'


def test_many_missing_projects_share_one_walk(tmp_path, monkeypatch):
    """A registry full of deleted folders walked their parents once per
    missing project on every pass; the walk is the same for all of them."""
    from openprogram.state.store.project import discovery
    from openprogram.state.store.project.location import reconcile_registered_projects
    monkeypatch.setattr('openprogram.state.paths.get_state_dir', lambda: str(tmp_path / 'state'))
    monkeypatch.setattr('openprogram.state.store.session.session_store.default_store', lambda: type('Store', (), {'relocate_project_sessions': lambda *args, **kwargs: 0})())
    work = tmp_path / 'work'; work.mkdir()
    made = []
    for index in range(6):
        folder = work / f'p{index}'; folder.mkdir()
        made.append(projects.resolve_project(folder))
    for project in made[:4]:
        shutil.rmtree(project.path)
    Path(made[4].path).rename(work / 'renamed')

    walks = []
    walk = discovery.MarkerScan._walk
    monkeypatch.setattr(discovery.MarkerScan, '_walk', lambda self: walks.append(1) or walk(self))
    assert reconcile_registered_projects() == [made[4].id]
    assert len(walks) == 1
    assert [projects.get_project(p.id).location_state for p in made] == ['missing'] * 4 + ['available'] * 2

    # Nothing is missing any more than it was recorded, and nothing moved:
    # the pass still shares one walk, and a pass with no missing project
    # walks nothing.
    for project in made[:4]:
        projects.set_project_hidden(project.id, True)
        Path(project.path).mkdir()
    walks.clear()
    reconcile_registered_projects()
    assert len(walks) <= 1


def test_no_walk_when_every_folder_is_where_it_was(tmp_path, monkeypatch):
    from openprogram.state.store.project import discovery
    from openprogram.state.store.project.location import reconcile_registered_projects
    monkeypatch.setattr('openprogram.state.paths.get_state_dir', lambda: str(tmp_path / 'state'))
    for index in range(3):
        folder = tmp_path / f'p{index}'; folder.mkdir()
        projects.resolve_project(folder)
    monkeypatch.setattr(discovery.MarkerScan, '_walk', lambda self: (_ for _ in ()).throw(AssertionError('walked')))
    assert reconcile_registered_projects() == []


def test_pruning_with_nothing_to_remove_never_takes_the_registry(tmp_path, monkeypatch):
    """The pass behind every listing held the registry lock, which a turn
    binding its conversation waits on, while it checked every folder."""
    monkeypatch.setattr('openprogram.state.paths.get_state_dir', lambda: str(tmp_path / 'state'))
    here = tmp_path / 'here'; here.mkdir()
    gone = tmp_path / 'gone'; gone.mkdir()
    kept = projects.resolve_project(here)
    lost = projects.resolve_project(gone)
    projects.bind_session('s1', kept.id)
    projects.bind_session('s2', lost.id)
    shutil.rmtree(gone)

    def refuse():
        raise AssertionError('registry lock taken')

    with monkeypatch.context() as patch:
        patch.setattr(projects, '_registry_transaction', refuse)
        # s2 is unknown, but its project's folder is gone: no evidence.
        assert projects.prune_sessions({'s1'}) == 0
    assert projects.prune_sessions(set()) == 1
    assert projects.get_project(kept.id).session_ids == []
    assert projects.get_project(lost.id).session_ids == ['s2']
