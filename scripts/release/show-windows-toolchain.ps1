# Print what a Windows runner gives the release steps: the tools and their
# versions, where they resolve, the paths the steps write under, and the
# switches that change how long paths and links behave. It never fails, so
# a workflow can run it first and read a later failure against it in one run.
#
#   show-windows-toolchain.ps1
#
# Run by .github/workflows/release.yml and windows-install-smoke.yml. ASCII
# only, so Windows PowerShell 5.1 reads it the same as PowerShell 7.
$ErrorActionPreference = "Continue"

function Show-Command([string]$Name, [string[]]$Arguments) {
    $Found = Get-Command $Name -ErrorAction SilentlyContinue | Select-Object -First 1
    if (-not $Found) {
        Write-Host ("{0,-10} missing" -f $Name)
        return
    }
    $Version = ""
    try {
        $Version = (& $Found.Source @Arguments 2>&1 | Select-Object -First 1 | Out-String).Trim()
    } catch {
        $Version = "(could not run: $($_.Exception.Message))"
    }
    Write-Host ("{0,-10} {1}  [{2}]" -f $Name, $Version, $Found.Source)
}

Write-Host "PowerShell $($PSVersionTable.PSVersion) ($($PSVersionTable.PSEdition))"
Write-Host "OS         $([Environment]::OSVersion.VersionString), $([Runtime.InteropServices.RuntimeInformation]::OSArchitecture)"
Write-Host "process    $([Runtime.InteropServices.RuntimeInformation]::ProcessArchitecture)"
foreach ($Tool in @(
    @("python", "--version"), @("py", "--version"), @("uv", "--version"), @("node", "--version"),
    @("npm.cmd", "--version"), @("git", "--version"), @("tar", "--version"),
    @("zstd", "--version"), @("cl", "/?"), @("cargo", "--version"), @("rustc", "--version")
)) {
    Show-Command $Tool[0] @($Tool[1])
}
# Every bash on PATH, in order, without running one: System32\bash.exe is the
# WSL launcher, which the command shell probe skips.
foreach ($Found in @(Get-Command bash.exe -All -ErrorAction SilentlyContinue)) {
    Write-Host ("bash       [{0}]" -f $Found.Source)
}
foreach ($Name in "GITHUB_WORKSPACE", "RUNNER_TEMP", "USERPROFILE", "APPDATA", "LOCALAPPDATA", "HOME", "TEMP",
                  "PROCESSOR_ARCHITECTURE", "PROCESSOR_ARCHITEW6432", "PYTHONUTF8") {
    Write-Host ("{0,-24} {1}" -f $Name, [Environment]::GetEnvironmentVariable($Name))
}
try {
    $LongPaths = (Get-ItemProperty -Path "HKLM:\SYSTEM\CurrentControlSet\Control\FileSystem" -Name LongPathsEnabled -ErrorAction Stop).LongPathsEnabled
    Write-Host "LongPathsEnabled         $LongPaths"
} catch {
    Write-Host "LongPathsEnabled         (not set)"
}
try {
    $Developer = (Get-ItemProperty -Path "HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\AppModelUnlock" -Name AllowDevelopmentWithoutDevLicense -ErrorAction Stop).AllowDevelopmentWithoutDevLicense
    Write-Host "Developer Mode (symlinks) $Developer"
} catch {
    Write-Host "Developer Mode (symlinks) (not set)"
}
try {
    $Defender = Get-MpComputerStatus -ErrorAction Stop
    Write-Host "Defender real-time        $($Defender.RealTimeProtectionEnabled)"
} catch {
    Write-Host "Defender real-time        (unknown)"
}
foreach ($Drive in Get-PSDrive -PSProvider FileSystem -ErrorAction SilentlyContinue) {
    if ($null -ne $Drive.Free) { Write-Host ("drive {0}:  {1:N1} GB free" -f $Drive.Name, ($Drive.Free / 1GB)) }
}
exit 0
