# Run by the uninstaller (installer.nsh, customUnInstall) when the App is really
# being removed, not replaced by an upgrade or a reinstall.
#
# The background worker and its logon task run from the version store in the
# user's state directory, so removing the App's own folder leaves both running.
# This stops the worker and removes the task. It deletes nothing else: the
# conversations, the settings and the version store all stay where they are.
param(
    [string]$InstallRoot = "",
    [string]$StateDir = "",
    [int]$StopSeconds = 90
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$TaskName = "OpenProgram Worker"
$TaskScriptName = "worker-service.cmd"

if (-not $StateDir) {
    $StateDir = Join-Path $env:USERPROFILE ".openprogram"
}
$State = [IO.Path]::GetFullPath($StateDir).TrimEnd([IO.Path]::DirectorySeparatorChar)
$Store = Join-Path $State "runtime"
$Notes = [Collections.Generic.List[string]]::new()

# Folders whose programs are the product's background processes. The App's own
# window is OpenProgram.exe, which the uninstaller has already asked to close.
$Roots = @($Store)
if ($InstallRoot) {
    $Install = [IO.Path]::GetFullPath($InstallRoot).TrimEnd([IO.Path]::DirectorySeparatorChar)
    $Volume = [IO.Path]::GetPathRoot($Install).TrimEnd([IO.Path]::DirectorySeparatorChar)
    if (-not [StringComparer]::OrdinalIgnoreCase.Equals($Install, $Volume) -and
        (Test-Path -LiteralPath (Join-Path $Install "OpenProgram.exe") -PathType Leaf)) {
        $Roots += $Install
    }
}

# This helper and the programs that started it are not background processes.
# An uninstaller run in place (/S _?=<folder>, which is how a script waits for
# it) is itself a program under the install folder; ending it would stop the
# uninstall half way. A parent that began after its child is a reused id.
function Get-OwnProcessChain {
    $Rows = @{}
    Get-CimInstance Win32_Process -Property ProcessId, ParentProcessId, CreationDate `
        -OperationTimeoutSec 10 -ErrorAction Stop | ForEach-Object { $Rows[[int]$_.ProcessId] = $_ }
    $Chain = [Collections.Generic.HashSet[int]]::new()
    $Current = [int]$PID
    while ($Rows.ContainsKey($Current) -and $Chain.Add($Current)) {
        $Row = $Rows[$Current]
        $Parent = [int]$Row.ParentProcessId
        if (-not $Rows.ContainsKey($Parent)) { break }
        if ($null -ne $Row.CreationDate -and $null -ne $Rows[$Parent].CreationDate -and
            $Rows[$Parent].CreationDate -gt $Row.CreationDate) { break }
        $Current = $Parent
    }
    [void]$Chain.Add([int]$PID)
    return ,$Chain
}

try {
    $OwnChain = Get-OwnProcessChain
} catch {
    # Without the chain the install folder cannot be searched safely.
    $OwnChain = [Collections.Generic.HashSet[int]]::new()
    [void]$OwnChain.Add([int]$PID)
    $Roots = @($Store)
    $Notes.Add("the uninstaller's own processes could not be listed: $($_.Exception.Message)")
}

function Get-BackgroundProcess {
    Get-CimInstance Win32_Process -Property Name, ExecutablePath, ProcessId -OperationTimeoutSec 10 -ErrorAction Stop |
        Where-Object {
            $Process = $_
            $Process.ExecutablePath -and $Process.Name -ne "OpenProgram.exe" -and
            -not $OwnChain.Contains([int]$Process.ProcessId) -and @($Roots | Where-Object {
                [IO.Path]::GetFullPath($Process.ExecutablePath).StartsWith(
                    $_ + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)
            }).Count -gt 0
        }
}

# 1. Ask the worker to stop, so it saves what its running tasks were doing.
$Command = Join-Path $Store "bin\openprogram.cmd"
if (Test-Path -LiteralPath $Command -PathType Leaf) {
    try {
        $Stop = Start-Process -FilePath $env:ComSpec -ArgumentList "/d /s /c `"`"$Command`" worker stop`"" `
            -WindowStyle Hidden -PassThru
        if (-not $Stop.WaitForExit($StopSeconds * 1000)) {
            Stop-Process -Id $Stop.Id -Force -ErrorAction SilentlyContinue
            $Notes.Add("the worker did not answer the stop request in $StopSeconds seconds")
        }
    } catch {
        $Notes.Add("the stop request could not be sent: $($_.Exception.Message)")
    }
}

# 2. Remove the logon task, when it is the one this state directory installed.
$TaskScript = Join-Path $State $TaskScriptName
try {
    $Task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    if ($null -ne $Task) {
        $Arguments = (@($Task.Actions) | ForEach-Object { [string]$_.Arguments }) -join " "
        if ($Arguments.IndexOf($TaskScript, [StringComparison]::OrdinalIgnoreCase) -ge 0) {
            Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
            Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
            $Notes.Add("removed the logon task")
        }
    }
} catch {
    $Notes.Add("the logon task could not be removed: $($_.Exception.Message)")
}
# The worker reads this file as "a logon task is installed".
if (Test-Path -LiteralPath $TaskScript -PathType Leaf) {
    Remove-Item -LiteralPath $TaskScript -Force -ErrorAction SilentlyContinue
}

# 3. End whatever is still running from the product's folders.
$Left = @(Get-BackgroundProcess)
foreach ($Process in $Left) {
    Stop-Process -Id $Process.ProcessId -Force -ErrorAction SilentlyContinue
}
$Deadline = [DateTime]::UtcNow.AddSeconds(5)
do {
    $Remaining = @(Get-BackgroundProcess)
    if ($Remaining.Count -eq 0) { break }
    Start-Sleep -Milliseconds 100
} while ([DateTime]::UtcNow -lt $Deadline)

$Notes.Add("ended $($Left.Count) process(es) that were still running")
Write-Output ("OpenProgram background worker: " + ($Notes -join "; "))
if ($Remaining.Count -gt 0) {
    throw "OpenProgram background processes are still running: $($Remaining.ProcessId -join ', ')"
}
