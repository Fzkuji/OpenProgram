@echo off
rem Installed as <root>\bin\openprogram.cmd. `versions` runs outside every
rem version, so it works when the current one is broken.
rem
rem Python runs in UTF-8 mode here, as the launcher and the other installed
rem commands start it. The console reads what it prints as UTF-8 for the length
rem of the command and gets its own code page back, with Python's exit status.
rem The launcher on PATH hands over to this file after it has already switched
rem the console, so it passes the caller's code page in
rem _OPENPROGRAM_CALLER_CODEPAGE; the preamble is the one Write-CmdLauncher in
rem scripts/release/install-release.ps1 writes.
setlocal DisableDelayedExpansion
set "_OPENPROGRAM_CODEPAGE=%_OPENPROGRAM_CALLER_CODEPAGE%"
set "_OPENPROGRAM_CALLER_CODEPAGE="
if not defined _OPENPROGRAM_CODEPAGE for /f "tokens=2 delims=:." %%P in ('chcp') do set "_OPENPROGRAM_CODEPAGE=%%P"
chcp 65001 >nul
set "ROOT=%~dp0.."
for /f "usebackq tokens=1,* delims==" %%A in ("%ROOT%\current") do (
  if "%%A"=="version" set "VERSION=%%B"
  if "%%A"=="base" set "BASE=%%B"
)
if /i "%~1"=="versions" (
  set "BASE_PYTHON=%ROOT%\bases\%BASE%\python\python.exe"
  if not exist "%ROOT%\bases\%BASE%\python\python.exe" (
    for /d %%D in ("%ROOT%\bases\*") do if exist "%%D\python\python.exe" set "BASE_PYTHON=%%D\python\python.exe"
  )
  shift
  goto versions
)
"%ROOT%\versions\%VERSION%\venv\Scripts\python.exe" -I -B -X utf8 -m openprogram %*
set "_OPENPROGRAM_EXIT=%ERRORLEVEL%"
goto finish

:versions
set "ARGUMENTS="
:collect
if "%~1"=="" goto run
set "ARGUMENTS=%ARGUMENTS% %1"
shift
goto collect
:run
"%BASE_PYTHON%" -I -B -X utf8 "%ROOT%\bootstrap\versions.py" --root "%ROOT%" %ARGUMENTS%
set "_OPENPROGRAM_EXIT=%ERRORLEVEL%"

:finish
chcp %_OPENPROGRAM_CODEPAGE% >nul
exit /b %_OPENPROGRAM_EXIT%
