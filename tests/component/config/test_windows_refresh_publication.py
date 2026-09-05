"""Native Windows publication/rollback, without launching an App or worker."""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[3]
pytestmark = pytest.mark.skipif(os.name != "nt", reason="native Windows refresh publication")


def _deep_asset(directory):
    path = Path("\\\\?\\" + str(directory))
    for _ in range(3):
        path /= "deep-" + "d" * 70
    return path / "asset.txt"


@pytest.fixture
def publication(tmp_path):
    root = tmp_path / "中文 refresh"
    root.mkdir()
    app = root / "OpenProgram"
    candidate = root / "candidate-app"
    (root / "cli").mkdir()
    for directory, value in ((app, "old"), (candidate, "new"),
                             (root / "cli/runtime", "old-cli"), (root / "cli/candidate-runtime", "new-cli")):
        directory.mkdir()
        (directory / "app.txt").write_text(value)
        asset = _deep_asset(directory)
        asset.parent.mkdir(parents=True)
        asset.write_text(value)
    for name, value in (("openprogram.ps1", "old-ps1"), ("candidate.ps1", "new-ps1"),
                        ("openprogram.cmd", "old-cmd"), ("candidate.cmd", "new-cmd")):
        (root / name).write_text(value)
    script = tmp_path / "publication.ps1"
    script.write_text(r'''
param([string]$Root, [string]$Helper, [string]$Outcome)
$ErrorActionPreference = 'Stop'
. $Helper
$Events = Join-Path $Root 'events.txt'
$CmdLock = $null
$Ps1Lock = $null
$OuterLock = $null
$script:ActualMove = ${function:Move-RefreshPath}
$script:ActualJournal = ${function:Write-RefreshJournal}
$script:JournalFailureConsumed = $false
function Write-RefreshJournal {
    param([string]$Path, [object]$Journal)
    if ($Outcome -eq 'journal-failure' -and $Journal.Phase -eq 'publishing' -and
        $Journal.Entries[0].BackedUp -and -not $script:JournalFailureConsumed) {
        $script:JournalFailureConsumed = $true
        throw 'injected journal write failure after backup'
    }
    & $script:ActualJournal $Path $Journal
}
function Move-RefreshPath {
    param([string]$Source, [string]$Destination, [string]$Kind)
    & $script:ActualMove $Source $Destination $Kind
    if ($Outcome -eq 'rollback-failure' -and $Source.EndsWith('candidate.ps1')) {
        $script:Ps1Lock = [IO.File]::Open($Destination, [IO.FileMode]::Open,
            [IO.FileAccess]::Read, [IO.FileShare]::Read)
    }
}
$Changes = @(
    @{Source=(Join-Path $Root 'candidate-app'); Destination=(Join-Path $Root 'OpenProgram')},
    @{Source=(Join-Path $Root 'cli\candidate-runtime'); Destination=(Join-Path $Root 'cli\runtime')},
    @{Source=(Join-Path $Root 'candidate.ps1'); Destination=(Join-Path $Root 'openprogram.ps1')},
    @{Source=(Join-Path $Root 'candidate.cmd'); Destination=(Join-Path $Root 'openprogram.cmd')}
)
if ($Outcome -eq 'overlap') { $Changes[2].Destination = $Changes[0].Destination }
if ($Outcome -eq 'extended-overlap') { $Changes[2].Destination = '\\?\' + $Changes[0].Destination }
if ($Outcome -eq 'relative') { $Changes[0].Source = 'C:relative' }
if ($Outcome -eq 'drive-relative') { $Changes[0].Source = '\relative' }
if ($Outcome -eq 'control-file') { $Changes[2].Destination = Join-Path $Root '.openprogram-local-refresh.lock' }
if ($Outcome -eq 'missing-candidate') { $Changes[3].Source = Join-Path $Root 'absent.cmd' }
if ($Outcome -eq 'cross-directory') { $Changes[3].Source = Join-Path $Root 'other\candidate.cmd' }
if ($Outcome -eq 'pending') {
    [IO.File]::WriteAllText((Join-Path $Root '.openprogram-local-refresh.lock.pending.json'), 'existing recovery')
}
try {
    if ($Outcome -in @('activation-failure', 'rollback-failure')) {
        $CmdLock = [IO.File]::Open((Join-Path $Root 'openprogram.cmd'), [IO.FileMode]::Open,
            [IO.FileAccess]::Read, [IO.FileShare]::Read)
    }
    if ($Outcome -eq 'locked') {
        $OuterLock = [IO.File]::Open((Join-Path $Root '.openprogram-local-refresh.lock'),
            [IO.FileMode]::OpenOrCreate, [IO.FileAccess]::ReadWrite, [IO.FileShare]::None)
    }
    $Result = Invoke-WindowsRefreshPublication -Replacements $Changes -Quiesce {
        Add-Content -LiteralPath $Events -Value 'quiesce'
        if ($Outcome -eq 'quiesce-failure') { throw 'injected quiesce failure' }
        $true
    } -Verify {
        Add-Content -LiteralPath $Events -Value 'verify'
        if ($Outcome -eq 'crash') { [Environment]::Exit(77) }
        if ((Get-Content -LiteralPath (Join-Path $Root 'OpenProgram\app.txt') -Raw) -ne 'new' -or
            (Get-Content -LiteralPath (Join-Path $Root 'cli\runtime\app.txt') -Raw) -ne 'new-cli' -or
            (Get-Content -LiteralPath (Join-Path $Root 'openprogram.ps1') -Raw) -ne 'new-ps1' -or
            (Get-Content -LiteralPath (Join-Path $Root 'openprogram.cmd') -Raw) -ne 'new-cmd') {
            throw 'verification saw a mixed installation'
        }
        if ($Outcome -in @('verify-failure', 'new-target-failure', 'stop-replacement-failure', 'restore-failure')) {
            throw 'injected verification failure'
        }
        if ($Outcome -eq 'verify-false') { return $false }
        if ($Outcome -eq 'verify-no-result') { return }
        $true
    } -BeforeRollback {
        Add-Content -LiteralPath $Events -Value 'before-rollback'
        if ($Outcome -eq 'stop-replacement-failure') { throw 'injected replacement still running' }
        $true
    } -Restore {
        Add-Content -LiteralPath $Events -Value 'restore'
        if ($Outcome -eq 'restore-failure') { throw 'injected previous service failure' }
        $true
    }
    $Result | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath (Join-Path $Root 'result.json') -Encoding UTF8
} finally {
    foreach ($Handle in @($CmdLock, $Ps1Lock, $OuterLock)) { if ($Handle) { $Handle.Dispose() } }
    # Every exit path must release the cross-process installation lock.
    $Released = [IO.File]::Open((Join-Path $Root '.openprogram-local-refresh.lock'),
        [IO.FileMode]::OpenOrCreate, [IO.FileAccess]::ReadWrite, [IO.FileShare]::None)
    $Released.Dispose()
}
''', encoding="utf-8-sig")

    def run(outcome):
        result = subprocess.run([
            shutil.which("powershell.exe"), "-NoLogo", "-NoProfile", "-NonInteractive",
            "-ExecutionPolicy", "Bypass", "-File", str(script), "-Root", str(root),
            "-Helper", str(ROOT / "scripts/release/windows-refresh-transaction.ps1"), "-Outcome", outcome,
        ], capture_output=True, timeout=30, creationflags=subprocess.CREATE_NO_WINDOW)
        return result.returncode, result.stderr.decode("mbcs", errors="replace")

    return root, run


@pytest.mark.parametrize("new_target", [False, True])
def test_success_retains_prior_payloads_and_durable_receipt(publication, new_target):
    root, run = publication
    if new_target:
        (root / "openprogram.cmd").unlink()
    code, error = run("success")
    assert code == 0, error
    result = json.loads((root / "result.json").read_text(encoding="utf-8-sig"))
    receipt = json.loads(Path(result["Receipt"]).read_text(encoding="utf-8"))
    assert receipt["Phase"] == "committed"
    assert not (root / ".openprogram-local-refresh.lock.pending.json").exists()
    assert (root / "OpenProgram/app.txt").read_text() == "new"
    assert _deep_asset(root / "OpenProgram").read_text() == "new"
    assert _deep_asset(root / "cli/runtime").read_text() == "new-cli"
    assert (root / "openprogram.ps1").read_text() == "new-ps1"
    assert (root / "openprogram.cmd").read_text() == "new-cmd"
    for entry in receipt["Entries"]:
        assert entry["Activated"]
        assert not Path(entry["Source"]).exists()
        assert Path(entry["Backup"]).exists() == entry["HadDestination"]
    assert (Path(receipt["Entries"][0]["Backup"]) / "app.txt").read_text() == "old"
    assert _deep_asset(Path(receipt["Entries"][0]["Backup"])).read_text() == "old"


@pytest.mark.parametrize("outcome", ["verify-failure", "verify-false", "verify-no-result", "activation-failure",
                                     "quiesce-failure", "new-target-failure", "journal-failure"])
def test_failed_refresh_restores_every_prior_entry_and_preserves_candidate(publication, outcome):
    root, run = publication
    if outcome == "new-target-failure":
        (root / "openprogram.cmd").unlink()
    code, error = run(outcome)
    assert code != 0
    assert "recovery required" not in error
    assert (root / "OpenProgram/app.txt").read_text() == "old"
    assert (root / "candidate-app/app.txt").read_text() == "new"
    assert _deep_asset(root / "OpenProgram").read_text() == "old"
    assert _deep_asset(root / "candidate-app").read_text() == "new"
    assert _deep_asset(root / "cli/runtime").read_text() == "old-cli"
    assert _deep_asset(root / "cli/candidate-runtime").read_text() == "new-cli"
    assert (root / "openprogram.ps1").read_text() == "old-ps1"
    assert (root / "candidate.ps1").read_text() == "new-ps1"
    assert (root / "candidate.cmd").read_text() == "new-cmd"
    assert (root / "openprogram.cmd").exists() == (outcome != "new-target-failure")
    if outcome != "new-target-failure":
        assert (root / "openprogram.cmd").read_text() == "old-cmd"
    receipts = list(root.glob(".openprogram-local-refresh.lock.*.json"))
    assert len(receipts) == 1
    assert json.loads(receipts[0].read_text(encoding="utf-8"))["Phase"] == "rolled-back"
    assert not list(root.glob("*.openprogram-previous-*"))


@pytest.mark.parametrize("outcome", ["rollback-failure", "stop-replacement-failure", "restore-failure"])
def test_incomplete_recovery_preserves_journal_and_prevents_next_publication(publication, outcome):
    root, run = publication
    code, error = run(outcome)
    assert code != 0
    assert "recovery required" in error
    journal_path = root / ".openprogram-local-refresh.lock.pending.json"
    journal = json.loads(journal_path.read_text(encoding="utf-8"))
    assert journal["Phase"] == "recovery-required"
    assert journal["RecoveryErrors"]
    for entry in journal["Entries"]:
        if entry["HadDestination"]:
            original = Path(entry["Backup"] if entry["BackedUp"] else entry["Destination"])
            assert original.exists()
    before = journal_path.read_bytes()
    events = (root / "events.txt").read_bytes()
    retry_code, retry_error = run("success")
    assert retry_code != 0
    assert "unfinished refresh requires recovery" in retry_error
    assert journal_path.read_bytes() == before
    assert (root / "events.txt").read_bytes() == events


@pytest.mark.parametrize("outcome", ["locked", "pending", "overlap", "extended-overlap", "relative",
                                     "drive-relative", "control-file", "missing-candidate", "cross-directory"])
def test_preflight_failure_does_not_quiesce_or_change_payloads(publication, outcome):
    root, run = publication
    assert run(outcome)[0] != 0
    assert not (root / "events.txt").exists()
    assert (root / "OpenProgram/app.txt").read_text() == "old"
    assert (root / "candidate-app/app.txt").read_text() == "new"


def test_controller_exit_releases_lock_but_retains_interrupted_transaction(publication):
    root, run = publication
    assert run("crash")[0] == 77
    pending = root / ".openprogram-local-refresh.lock.pending.json"
    before = pending.read_bytes()
    journal = json.loads(before)
    assert journal["Phase"] == "publishing"
    assert (root / "OpenProgram/app.txt").read_text() == "new"
    for entry in journal["Entries"]:
        assert Path(entry["Backup"]).exists()
    code, error = run("success")
    assert code != 0
    assert "unfinished refresh requires recovery" in error
    assert pending.read_bytes() == before
