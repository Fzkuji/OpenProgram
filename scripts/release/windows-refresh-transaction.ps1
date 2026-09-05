# Publication primitive for the Windows local refresh orchestrator. Loading
# this file does not stop processes, create state or modify an installation.
Set-StrictMode -Version Latest

function Get-RefreshFullPath {
    param([Parameter(Mandatory = $true)][string]$Path)
    # Canonicalize extended aliases before comparing paths. IsPathRooted alone
    # also accepts C:relative and \current-drive-relative on .NET Framework.
    $Path = $Path.Replace('/', '\')
    if ($Path.StartsWith('\\?\UNC\', [StringComparison]::OrdinalIgnoreCase)) {
        $Path = '\\' + $Path.Substring(8)
    } elseif ($Path.StartsWith('\\?\', [StringComparison]::Ordinal)) {
        $Path = $Path.Substring(4)
    }
    if ($Path.StartsWith('\\.\', [StringComparison]::Ordinal) -or
        $Path -notmatch '^(?:[A-Za-z]:\\|\\\\[^\\]+\\[^\\]+(?:\\|$))') {
        throw "refresh path must be absolute: $Path"
    }
    $Full = [IO.Path]::GetFullPath($Path).TrimEnd('\', '/')
    if (-not $Full -or $Full -eq [IO.Path]::GetPathRoot($Full).TrimEnd('\', '/')) {
        throw "refresh path must not be a volume root: $Path"
    }
    return $Full
}

function Get-RefreshExtendedPath {
    param([Parameter(Mandatory = $true)][string]$Path)
    if ($Path.StartsWith('\\?\', [StringComparison]::Ordinal)) { return $Path }
    if ($Path.StartsWith('\\', [StringComparison]::Ordinal)) { return '\\?\UNC\' + $Path.Substring(2) }
    return '\\?\' + $Path
}

function Get-RefreshPathKind {
    param([Parameter(Mandatory = $true)][string]$Path)
    try { $Attributes = [IO.File]::GetAttributes((Get-RefreshExtendedPath $Path)) }
    catch [IO.FileNotFoundException] { return 'missing' }
    catch [IO.DirectoryNotFoundException] { return 'missing' }
    if ($Attributes -band [IO.FileAttributes]::ReparsePoint) { throw "refresh target is redirected: $Path" }
    if ($Attributes -band [IO.FileAttributes]::Directory) { return 'directory' }
    return 'file'
}

function Move-RefreshPath {
    param([string]$Source, [string]$Destination, [string]$Kind)
    $From = Get-RefreshExtendedPath $Source
    $To = Get-RefreshExtendedPath $Destination
    if ($Kind -eq 'directory') { [IO.Directory]::Move($From, $To) }
    else { [IO.File]::Move($From, $To) }
}

function Write-RefreshJournal {
    param([string]$Path, [object]$Journal)
    $Temporary = $Path + '.' + [guid]::NewGuid().ToString('N') + '.tmp'
    try {
        $Stream = [IO.File]::Open((Get-RefreshExtendedPath $Temporary),
            [IO.FileMode]::CreateNew, [IO.FileAccess]::Write, [IO.FileShare]::None)
        try {
            $Bytes = [Text.UTF8Encoding]::new($false).GetBytes(($Journal | ConvertTo-Json -Depth 8))
            $Stream.Write($Bytes, 0, $Bytes.Length)
            $Stream.Flush($true)
        } finally { $Stream.Dispose() }
        if ([IO.File]::Exists((Get-RefreshExtendedPath $Path))) {
            [IO.File]::Replace((Get-RefreshExtendedPath $Temporary),
                (Get-RefreshExtendedPath $Path), [NullString]::Value)
        } else {
            [IO.File]::Move((Get-RefreshExtendedPath $Temporary), (Get-RefreshExtendedPath $Path))
        }
    } finally {
        if ([IO.File]::Exists((Get-RefreshExtendedPath $Temporary))) {
            [IO.File]::Delete((Get-RefreshExtendedPath $Temporary))
        }
    }
}

function Invoke-RefreshLifecycleCheck {
    param([string]$Name, [scriptblock]$Action)
    # Do not interpret false, an exit-code number or diagnostic output as a
    # successful lifecycle boundary. Log through Write-Host, return $true.
    $Result = & $Action
    if ($Result -isnot [bool] -or -not $Result) { throw "refresh $Name did not confirm success" }
}

function Invoke-WindowsRefreshPublication {
    [CmdletBinding()]
    param(
        # Each source is already verified and staged beside its destination.
        [Parameter(Mandatory = $true)][hashtable[]]$Replacements,
        # Each lifecycle callback must throw on failure or return exactly $true.
        [Parameter(Mandatory = $true)][scriptblock]$Quiesce,
        [Parameter(Mandatory = $true)][scriptblock]$Verify,
        [Parameter(Mandatory = $true)][scriptblock]$BeforeRollback,
        [Parameter(Mandatory = $true)][scriptblock]$Restore
    )
    $ErrorActionPreference = 'Stop'
    if (-not $Replacements.Count) { throw 'refresh requires at least one replacement' }
    $Id = [guid]::NewGuid().ToString('N')
    $Entries = @()
    $Paths = [Collections.Generic.List[string]]::new()
    foreach ($Replacement in $Replacements) {
        $Source = Get-RefreshFullPath $Replacement.Source
        $Destination = Get-RefreshFullPath $Replacement.Destination
        if (-not [StringComparer]::OrdinalIgnoreCase.Equals(
            [IO.Path]::GetDirectoryName($Source), [IO.Path]::GetDirectoryName($Destination))) {
            throw 'refresh candidates must be staged beside their destination for same-volume rename'
        }
        foreach ($Path in @($Source, $Destination)) {
            foreach ($Other in $Paths) {
                if ([StringComparer]::OrdinalIgnoreCase.Equals($Path, $Other) -or
                    $Path.StartsWith($Other + '\', [StringComparison]::OrdinalIgnoreCase) -or
                    $Other.StartsWith($Path + '\', [StringComparison]::OrdinalIgnoreCase)) {
                    throw "refresh paths overlap: $Path and $Other"
                }
            }
            $Paths.Add($Path)
        }
        $Parent = [IO.Path]::GetDirectoryName($Source)
        while ($Parent) {
            if ((Get-RefreshPathKind $Parent) -ne 'directory') { throw "refresh parent is not a directory: $Parent" }
            $Parent = [IO.Path]::GetDirectoryName($Parent)
        }
        $Backup = $Destination + '.openprogram-previous-' + $Id
        $Entries += @{
            Source = $Source; Destination = $Destination; Backup = $Backup; Kind = ''
            HadDestination = $false; BackedUp = $false; Activated = $false
        }
    }
    $LockPath = Join-Path ([IO.Path]::GetDirectoryName($Entries[0].Destination)) '.openprogram-local-refresh.lock'
    $PendingPath = $LockPath + '.pending.json'
    $ReceiptPath = $LockPath + '.' + $Id + '.json'
    foreach ($Path in $Paths) {
        if ([StringComparer]::OrdinalIgnoreCase.Equals($Path, $LockPath) -or
            $Path.StartsWith($LockPath + '.', [StringComparison]::OrdinalIgnoreCase)) {
            throw "refresh payload overlaps control files: $Path"
        }
    }
    if ((Get-RefreshPathKind $LockPath) -eq 'directory') { throw "refresh lock is not a regular file: $LockPath" }
    # The OS releases the lock if the controller dies. The pending journal does
    # not disappear with it: a later refresh must not overwrite recovery data.
    $Lock = [IO.File]::Open((Get-RefreshExtendedPath $LockPath),
        [IO.FileMode]::OpenOrCreate, [IO.FileAccess]::ReadWrite, [IO.FileShare]::None)
    try {
        if ((Get-RefreshPathKind $PendingPath) -ne 'missing') {
            throw "unfinished refresh requires recovery before retry: $PendingPath"
        }
        # Inspect mutable payloads under the same lock used for publication,
        # and only after reporting any earlier interrupted transaction.
        foreach ($Entry in $Entries) {
            $Entry.Kind = Get-RefreshPathKind $Entry.Source
            if ($Entry.Kind -eq 'missing') { throw "refresh candidate is missing: $($Entry.Source)" }
            $PriorKind = Get-RefreshPathKind $Entry.Destination
            if ($PriorKind -ne 'missing' -and $PriorKind -ne $Entry.Kind) {
                throw "refresh candidate and destination types differ: $($Entry.Destination)"
            }
            $Entry.HadDestination = ($PriorKind -ne 'missing')
            if ((Get-RefreshPathKind $Entry.Backup) -ne 'missing') {
                throw "refresh backup already exists: $($Entry.Backup)"
            }
        }
        $Journal = @{ Schema = 1; Id = $Id; Phase = 'prepared'; Entries = $Entries }
        Write-RefreshJournal $PendingPath $Journal
        try {
            Invoke-RefreshLifecycleCheck 'quiesce' $Quiesce
            $Journal.Phase = 'publishing'
            Write-RefreshJournal $PendingPath $Journal
            foreach ($Entry in $Entries) {
                if ($Entry.HadDestination) {
                    Move-RefreshPath $Entry.Destination $Entry.Backup $Entry.Kind
                    $Entry.BackedUp = $true
                    Write-RefreshJournal $PendingPath $Journal
                }
                Move-RefreshPath $Entry.Source $Entry.Destination $Entry.Kind
                $Entry.Activated = $true
                Write-RefreshJournal $PendingPath $Journal
            }
            Invoke-RefreshLifecycleCheck 'verify' $Verify
            $Journal.Phase = 'committed'
            Write-RefreshJournal $PendingPath $Journal
        } catch {
            $Failure = $_
            $RecoveryErrors = [Collections.Generic.List[string]]::new()
            try { Invoke-RefreshLifecycleCheck 'before-rollback' $BeforeRollback }
            catch { $RecoveryErrors.Add("could not quiesce replacement: $($_.Exception.Message)") }
            # Never move a runtime still executing after failed quiescence.
            if ($RecoveryErrors.Count -eq 0) {
                for ($Index = $Entries.Count - 1; $Index -ge 0; $Index--) {
                    $Entry = $Entries[$Index]
                    try {
                        if ($Entry.Activated) {
                            Move-RefreshPath $Entry.Destination $Entry.Source $Entry.Kind
                            $Entry.Activated = $false
                        }
                        if ($Entry.BackedUp) {
                            Move-RefreshPath $Entry.Backup $Entry.Destination $Entry.Kind
                            $Entry.BackedUp = $false
                        }
                        Write-RefreshJournal $PendingPath $Journal
                    } catch {
                        $RecoveryErrors.Add("$($Entry.Destination): $($_.Exception.Message)")
                    }
                }
                if ($RecoveryErrors.Count -eq 0) {
                    try { Invoke-RefreshLifecycleCheck 'restore' $Restore }
                    catch { $RecoveryErrors.Add("could not restore previous service: $($_.Exception.Message)") }
                }
            }
            $Journal.Phase = if ($RecoveryErrors.Count) { 'recovery-required' } else { 'rolled-back' }
            $Journal.Error = $Failure.Exception.Message
            $Journal.RecoveryErrors = @($RecoveryErrors.ToArray())
            try { Write-RefreshJournal $PendingPath $Journal }
            catch { $RecoveryErrors.Add("could not record recovery: $($_.Exception.Message)") }
            if ($RecoveryErrors.Count) {
                throw "refresh failed; recovery required; originals and candidates retained; journal ${PendingPath}: $($RecoveryErrors -join '; ')"
            }
            [IO.File]::Move((Get-RefreshExtendedPath $PendingPath), (Get-RefreshExtendedPath $ReceiptPath))
            throw $Failure
        }
        # Retain the original payloads and a receipt. Do not recursively delete
        # an installed runtime as a side effect of completing a local refresh.
        [IO.File]::Move((Get-RefreshExtendedPath $PendingPath), (Get-RefreshExtendedPath $ReceiptPath))
        return [pscustomobject]@{ Id = $Id; Receipt = $ReceiptPath; Entries = $Entries }
    } finally { $Lock.Dispose() }
}
