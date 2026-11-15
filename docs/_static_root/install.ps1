# Everything runs inside this block, so under `irm | iex` the settings and
# variables below end with the install instead of staying in the person's window.
# Piped that way `exit` would close the window, so only a script file hands back
# an exit code.
& {
    param([bool]$RunFromFile)

    $ErrorActionPreference = "Stop"
    Set-StrictMode -Version Latest

    $Repository = if ($env:OPENPROGRAM_REPOSITORY) { $env:OPENPROGRAM_REPOSITORY } else { "FuzzyKuji/OpenProgram" }
    $Version = $env:OPENPROGRAM_VERSION
    if ($Repository -notmatch "^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$") {
        throw "invalid OpenProgram repository: $Repository"
    }
    $ReleasesPage = "https://github.com/$Repository/releases"
    $InstallGuide = "https://github.com/FuzzyKuji/OpenProgram/blob/main/docs/install/install.html"
    $ProxyHelp = "If this computer reaches the internet through a proxy, set HTTPS_PROXY (and NO_PROXY if some hosts skip the proxy), for example: `$env:HTTPS_PROXY = 'http://proxy.example:8080'. Then run this command again."
    $Curl = (Get-Command curl.exe -ErrorAction SilentlyContinue).Source
    if (-not $Curl) {
        throw "curl.exe is required to install OpenProgram"
    }
    if (-not $Version) {
        $Version = (& $Curl --disable --proto "=https" --tlsv1.2 --location `
            --silent --show-error --fail --retry 3 --output NUL --write-out "%{url_effective}" `
            "https://github.com/$Repository/releases/latest").Trim().Split("/")[-1]
        if ($LASTEXITCODE -ne 0) {
            throw "Could not reach GitHub to find the latest OpenProgram release. Check that this computer is online. $ProxyHelp"
        }
    }
    $Version = $Version.TrimStart("v")
    if ($Version -notmatch "^\d+\.\d+\.\d+$") {
        throw "invalid OpenProgram release version: $Version. Published releases are listed at $ReleasesPage"
    }

    $Native = if ($env:PROCESSOR_ARCHITEW6432) { $env:PROCESSOR_ARCHITEW6432 } else { $env:PROCESSOR_ARCHITECTURE }
    $Arch = "$Native".ToLowerInvariant()
    try {
        $Arch = [Runtime.InteropServices.RuntimeInformation]::OSArchitecture.ToString().ToLowerInvariant()
    } catch {
        Write-Verbose "RuntimeInformation.OSArchitecture is unavailable: $($_.Exception.Message)"
    }
    $Arch = switch ($Arch) {
        { $_ -in @("x64", "amd64") } { "x86_64"; break }
        "arm64" { "arm64"; break }
        default { $Arch }
    }

    # The manifest lists every file of the release, so a system the release does not
    # cover stops here with its name instead of failing later on a missing download.
    $Manifest = (& $Curl --disable --proto "=https" --tlsv1.2 --location --silent --fail `
        --connect-timeout 15 "https://github.com/$Repository/releases/download/v$Version/release-manifest.json" | Out-String)
    if ($LASTEXITCODE -eq 0 -and $Manifest.Trim()) {
        $Listed = $Manifest.Contains("OpenProgram-$Version-runtime-windows-$Arch.zip") -or
            $Manifest.Contains("OpenProgram-$Version-seed-windows-$Arch.tar.gz")
        # Windows 11 on Arm (build 22000 and later) runs the x64 package under
        # emulation; the installer picks it and says so.
        $Emulated = $Arch -eq "arm64" -and [Environment]::OSVersion.Version.Build -ge 22000 -and (
            $Manifest.Contains("OpenProgram-$Version-runtime-windows-x86_64.zip") -or
            $Manifest.Contains("OpenProgram-$Version-seed-windows-x86_64.tar.gz"))
        if (-not $Listed -and $Emulated) {
            # The installer comes from the release's own tag, and only one that
            # picks the x64 package itself can use it; an earlier one would
            # download and then stop on the missing Arm package.
            $Tagged = (& $Curl --disable --proto "=https" --tlsv1.2 --location --silent --fail `
                --connect-timeout 15 --retry 3 `
                "https://raw.githubusercontent.com/$Repository/v$Version/scripts/release/install-release.ps1" | Out-String)
            $Emulated = $LASTEXITCODE -eq 0 -and $Tagged.Contains("function Select-PackageArchitecture")
        }
        if (-not $Listed -and -not $Emulated) {
            throw "OpenProgram $Version has no command-line package for Windows on $Arch. What this release includes is listed at $ReleasesPage/tag/v$Version. To install from source instead, see $InstallGuide#development-checkout"
        }
    }

    $InstallerExit = 0
    $CallerVersion = $env:OPENPROGRAM_VERSION
    $CallerRepository = $env:OPENPROGRAM_REPOSITORY
    $Temporary = Join-Path ([IO.Path]::GetTempPath()) ("openprogram-install-" + [guid]::NewGuid().ToString("N") + ".ps1")
    try {
        $Url = "https://raw.githubusercontent.com/$Repository/v$Version/scripts/install-release.ps1"
        & $Curl --disable --proto "=https" --tlsv1.2 --fail --silent --show-error `
            --connect-timeout 15 --speed-limit 1024 --speed-time 120 --retry 3 `
            --output $Temporary $Url
        if ($LASTEXITCODE -ne 0) {
            throw "OpenProgram $Version has no complete Windows release installer. Select a release with Windows runtime assets from $ReleasesPage, or use the source-development installation at $InstallGuide#development-checkout. $ProxyHelp"
        }
        $FirstLine = Get-Content -LiteralPath $Temporary -TotalCount 1 -Encoding UTF8
        if ($FirstLine -ne '$ErrorActionPreference = "Stop"') {
            throw "downloaded OpenProgram installer is invalid"
        }
        $env:OPENPROGRAM_VERSION = $Version
        $env:OPENPROGRAM_REPOSITORY = $Repository
        & powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File $Temporary
        $InstallerExit = $LASTEXITCODE
    } finally {
        # These are for the installer only; left set, a later run in this window
        # would stay pinned to this release.
        $env:OPENPROGRAM_VERSION = $CallerVersion
        $env:OPENPROGRAM_REPOSITORY = $CallerRepository
        Remove-Item -LiteralPath $Temporary -Force -ErrorAction SilentlyContinue
    }
    if ($InstallerExit -ne 0) {
        if ($RunFromFile) {
            exit $InstallerExit
        }
        $global:LASTEXITCODE = $InstallerExit
        throw "OpenProgram was not installed (exit code $InstallerExit). The message above says why."
    }
} $(try { $MyInvocation.MyCommand.CommandType -eq "ExternalScript" } catch { $false })
