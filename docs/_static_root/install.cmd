@echo off
setlocal DisableDelayedExpansion
rem Installs OpenProgram from Command Prompt.
rem One command per line and no labels, because this file is served with LF line endings.
set "OPENPROGRAM_INSTALL_SOURCE=%OPENPROGRAM_REPOSITORY%"
if not defined OPENPROGRAM_INSTALL_SOURCE set "OPENPROGRAM_INSTALL_SOURCE=FuzzyKuji/OpenProgram"
set "OPENPROGRAM_BOOTSTRAP=%TEMP%\openprogram-install-%RANDOM%%RANDOM%.ps1"
curl.exe --disable --proto "=https" --tlsv1.2 --fail --location --silent --show-error --connect-timeout 15 --retry 3 --output "%OPENPROGRAM_BOOTSTRAP%" "https://raw.githubusercontent.com/%OPENPROGRAM_INSTALL_SOURCE%/main/docs/_static_root/install.ps1"
if errorlevel 1 echo Could not download the OpenProgram installer. Check that this computer is online. If it reaches the internet through a proxy, set HTTPS_PROXY first, for example: set HTTPS_PROXY=http://proxy.example:8080
if errorlevel 1 exit /b 1
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%OPENPROGRAM_BOOTSTRAP%"
set "OPENPROGRAM_INSTALL_EXIT=%ERRORLEVEL%"
del /q "%OPENPROGRAM_BOOTSTRAP%" >nul 2>nul
exit /b %OPENPROGRAM_INSTALL_EXIT%
