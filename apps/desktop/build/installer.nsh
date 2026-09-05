; Stop background processes that run from the managed application runtime
; before electron-builder launches the previous uninstaller. `customInit` runs
; after initMultiUser has restored the existing InstallLocation, so $INSTDIR is
; authoritative for both default and user-selected installation directories.
;
; The visible Electron process remains handled by electron-builder's default
; CHECK_APP_RUNNING implementation. This hook only owns embedded Python
; processes, which otherwise keep resources\runtime DLLs open on Windows.
!macro customInit
  DetailPrint "Stopping OpenProgram background processes..."
  InitPluginsDir
  File /oname=$PLUGINSDIR\prepare-upgrade.ps1 "${BUILD_RESOURCES_DIR}\prepare-upgrade.ps1"
  nsExec::ExecToStack `"$SYSDIR\WindowsPowerShell\v1.0\powershell.exe" -NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -File "$PLUGINSDIR\prepare-upgrade.ps1" -InstallRoot "$INSTDIR"`
  Pop $0
  Pop $1
  ${if} $0 != 0
    DetailPrint "Unable to stop OpenProgram background processes: $1"
    MessageBox MB_OK|MB_ICONSTOP "OpenProgram could not stop its background worker. Close OpenProgram and try the installer again." /SD IDOK
    SetErrorLevel 12
    Quit
  ${endif}
!macroend
