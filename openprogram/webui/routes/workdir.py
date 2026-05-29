"""Workdir / folder picker endpoints + history / canvas readers.

Filesystem-side endpoints used by the UI for picking working directories,
listing prior conversation history, and reading the canvas markdown file.
"""
from __future__ import annotations

import json
import os
import sys

from fastapi.responses import JSONResponse


def _pick_folder_native(start: str):
    """Open the OS-native directory chooser and block until the user
    picks or cancels.

    Returns ``(chosen_path_or_None, unsupported)``:
      * ``("/abs/path", False)`` — user picked a folder
      * ``(None, False)``        — user cancelled (native dialog ran)
      * ``(None, True)``         — no native dialog available on this OS

    Cross-platform: macOS ``osascript`` / Windows ``FolderBrowserDialog``
    via PowerShell (STA) / Linux ``zenity`` → ``kdialog``. In every case
    we try to bring the dialog to the foreground, since the worker is a
    detached process and its windows otherwise open *behind* the browser.
    """
    import base64
    import subprocess

    plat = sys.platform
    if plat == "darwin":
        safe = start.replace("\\", "\\\\").replace('"', '\\"')
        script = (
            'tell application "System Events"\n'
            '  activate\n'
            '  set chosenFolder to choose folder with prompt '
            '"Select working directory" default location '
            f'POSIX file "{safe}"\n'
            'end tell\n'
            'return POSIX path of chosenFolder'
        )
        r = subprocess.run(
            ["osascript", "-e", script], capture_output=True, text=True, timeout=600
        )
        if r.returncode != 0:
            return None, False  # cancelled
        return (r.stdout.strip().rstrip("/") or None), False

    if plat == "win32":
        # Modern folder picker. We drive the Vista+ Common Item Dialog
        # (IFileOpenDialog with FOS_PICKFOLDERS) over COM interop, so the
        # user gets the rich Explorer-style chooser — address bar, sidebar,
        # search, quick-access, New folder — instead of the legacy
        # SHBrowseForFolder tree that WinForms FolderBrowserDialog shows
        # under .NET Framework / Windows PowerShell 5.1. This needs no
        # PowerShell 7. A zero-opacity TopMost form owns the dialog so it
        # surfaces above the browser (the worker is a detached process).
        safe = start.replace("'", "''")
        cs = """
using System;
using System.Runtime.InteropServices;
namespace OPFolder {
  [ComImport, Guid("42f85136-db7e-439c-85f1-e4075d135fc8"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
  public interface IFileDialog {
    [PreserveSig] int Show(IntPtr parent);
    void SetFileTypes(uint c, IntPtr p);
    void SetFileTypeIndex(uint i);
    void GetFileTypeIndex(out uint pi);
    void Advise(IntPtr e, out uint c);
    void Unadvise(uint c);
    void SetOptions(uint o);
    void GetOptions(out uint o);
    void SetDefaultFolder(IShellItem psi);
    void SetFolder(IShellItem psi);
    void GetFolder(out IShellItem ppsi);
    void GetCurrentSelection(out IShellItem ppsi);
    void SetFileName([MarshalAs(UnmanagedType.LPWStr)] string n);
    void GetFileName([MarshalAs(UnmanagedType.LPWStr)] out string n);
    void SetTitle([MarshalAs(UnmanagedType.LPWStr)] string t);
    void SetOkButtonLabel([MarshalAs(UnmanagedType.LPWStr)] string t);
    void SetFileNameLabel([MarshalAs(UnmanagedType.LPWStr)] string l);
    void GetResult(out IShellItem ppsi);
    void AddPlace(IShellItem psi, int fdap);
    void SetDefaultExtension([MarshalAs(UnmanagedType.LPWStr)] string e);
    void Close(int hr);
    void SetClientGuid(ref Guid g);
    void ClearClientData();
    void SetFilter(IntPtr f);
  }
  [ComImport, Guid("43826d1e-e718-42ee-bc55-a1e261c37bfe"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
  public interface IShellItem {
    void BindToHandler(IntPtr pbc, ref Guid bhid, ref Guid riid, out IntPtr ppv);
    void GetParent(out IShellItem ppsi);
    void GetDisplayName(uint sigdn, [MarshalAs(UnmanagedType.LPWStr)] out string name);
    void GetAttributes(uint mask, out uint attribs);
    void Compare(IShellItem psi, uint hint, out int order);
  }
  [ComImport, Guid("DC1C5A9C-E88A-4dde-A5A1-60F82A20AEF7")]
  public class FileOpenDialog { }
  public static class Picker {
    const uint FOS_PICKFOLDERS = 0x20;
    const uint FOS_FORCEFILESYSTEM = 0x40;
    const uint SIGDN_FILESYSPATH = 0x80058000;
    [DllImport("shell32.dll", CharSet=CharSet.Unicode)]
    static extern int SHCreateItemFromParsingName([MarshalAs(UnmanagedType.LPWStr)] string path, IntPtr pbc, ref Guid riid, [MarshalAs(UnmanagedType.Interface)] out IShellItem ppv);
    [DllImport("user32.dll")] static extern IntPtr SetThreadDpiAwarenessContext(IntPtr ctx);
    [DllImport("user32.dll")] static extern bool SetProcessDPIAware();
    public static void Dpi() {
      // Render crisp on high-DPI / scaled displays. Per-thread DPI context
      // can be set at runtime (the process-wide call fails once the host
      // already owns windows, e.g. the PowerShell console). -4 =
      // PER_MONITOR_AWARE_V2, -3 = PER_MONITOR_AWARE; fall back to
      // system-DPI awareness on older Windows.
      try {
        IntPtr r = SetThreadDpiAwarenessContext((IntPtr)(-4));
        if (r == IntPtr.Zero) r = SetThreadDpiAwarenessContext((IntPtr)(-3));
        if (r == IntPtr.Zero) SetProcessDPIAware();
      } catch { try { SetProcessDPIAware(); } catch {} }
    }
    public static string Pick(string start, IntPtr owner) {
      IFileDialog dlg = (IFileDialog)(new FileOpenDialog());
      uint opts; dlg.GetOptions(out opts);
      dlg.SetOptions(opts | FOS_PICKFOLDERS | FOS_FORCEFILESYSTEM);
      try { dlg.SetTitle("Select working directory"); } catch {}
      if (!String.IsNullOrEmpty(start)) {
        try {
          Guid iid = new Guid("43826d1e-e718-42ee-bc55-a1e261c37bfe");
          IShellItem si;
          if (SHCreateItemFromParsingName(start, IntPtr.Zero, ref iid, out si) == 0 && si != null) dlg.SetFolder(si);
        } catch {}
      }
      int hr = dlg.Show(owner);
      if (hr != 0) return null;
      IShellItem res; dlg.GetResult(out res);
      string p; res.GetDisplayName(SIGDN_FILESYSPATH, out p);
      return p;
    }
  }
}
""".strip("\n")
        ps_script = "\n".join([
            "Add-Type -AssemblyName System.Windows.Forms | Out-Null",
            "Add-Type -TypeDefinition @'",
            cs,
            "'@ -ErrorAction Stop | Out-Null",
            "[OPFolder.Picker]::Dpi()",
            "$t = New-Object System.Windows.Forms.Form",
            "$t.TopMost = $true; $t.ShowInTaskbar = $false; $t.Opacity = 0",
            "$t.Show() | Out-Null; $t.Activate()",
            f"$p = [OPFolder.Picker]::Pick('{safe}', $t.Handle)",
            "$t.Close()",
            "if ($p) { [Console]::Out.Write($p) }",
        ])
        encoded = base64.b64encode(ps_script.encode("utf-16-le")).decode("ascii")
        # IFileOpenDialog is a UI COM object → needs an STA apartment.
        # powershell.exe (always present) renders it richly; pwsh works too.
        for exe in ("powershell.exe", "pwsh.exe"):
            try:
                r = subprocess.run(
                    [exe, "-NoProfile", "-STA", "-EncodedCommand", encoded],
                    capture_output=True, text=True, timeout=600,
                )
            except FileNotFoundError:
                continue  # this shell isn't installed — try the next
            if r.returncode != 0:
                continue  # this shell errored — try the next
            return ((r.stdout or "").strip() or None), False
        return None, True  # no usable PowerShell found

    # Linux / other: GTK (zenity) then KDE (kdialog).
    for cmd in (
        ["zenity", "--file-selection", "--directory",
         "--title=Select working directory", f"--filename={start}/"],
        ["kdialog", "--getexistingdirectory", start],
    ):
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
        except FileNotFoundError:
            continue
        if r.returncode == 0:
            return (r.stdout.strip() or None), False
        return None, False  # tool present → non-zero means cancelled
    return None, True  # no native chooser found


def register(app):
    @app.post("/api/pick-folder")
    async def pick_folder(body: dict = None):
        """Open the OS-native folder chooser (macOS / Windows / Linux).

        Returns ``{"path": "<chosen>"}`` on success, ``{"path": null}``
        if the user cancelled, or ``{"path": null, "unsupported": true}``
        when no native dialog is available — the UI then falls back to a
        manual path input.
        """
        import asyncio
        import pathlib
        start = (body or {}).get("start") or str(pathlib.Path.home())
        start = os.path.abspath(os.path.expanduser(start))
        if not os.path.isdir(start):
            start = str(pathlib.Path.home())
        try:
            # Run the blocking dialog off the event loop so the worker
            # stays responsive while it's open (it can be open for a
            # while).
            loop = asyncio.get_running_loop()
            path, unsupported = await loop.run_in_executor(
                None, _pick_folder_native, start
            )
        except Exception as exc:  # noqa: BLE001
            return JSONResponse(
                content={"path": None, "error": str(exc), "unsupported": True}
            )
        return JSONResponse(content={"path": path, "unsupported": unsupported})

    @app.get("/api/browse")
    async def browse_directory(path: str = None):
        """List subdirectories of a path for the workdir picker."""
        import pathlib
        home = str(pathlib.Path.home())
        target = path or home
        target = os.path.abspath(os.path.expanduser(target))
        if not os.path.isdir(target):
            target = home
        try:
            entries = sorted(os.listdir(target))
        except PermissionError:
            return JSONResponse(
                content={"error": f"Permission denied: {target}"},
                status_code=403,
            )
        subdirs = []
        for name in entries:
            if name.startswith("."):
                continue
            full = os.path.join(target, name)
            if os.path.isdir(full):
                subdirs.append({"name": name, "path": full})
        parent = os.path.dirname(target) if target != "/" else None
        return JSONResponse(content={
            "path": target,
            "parent": parent if parent and parent != target else None,
            "subdirs": subdirs,
            "home": home,
        })

    @app.get("/api/workdir/defaults")
    async def workdir_defaults(session_id: str = None, function_name: str = None):
        import pathlib
        from openprogram.webui import server as _s
        repo_root = os.path.abspath(os.path.join(
            os.path.dirname(_s.__file__), "..", ".."
        ))
        last = None
        if session_id and function_name:
            with _s._sessions_lock:
                conv = _s._sessions.get(session_id)
                if conv:
                    last = conv.get("last_workdirs", {}).get(function_name)
        return JSONResponse(content={
            "last": last,
            "repo": repo_root,
            "home": str(pathlib.Path.home()),
        })

    @app.get("/api/history")
    async def get_history():
        from openprogram.webui import server as _s
        with _s._sessions_lock:
            history = [
                {"id": c["id"], "title": c["title"], "created_at": c["created_at"],
                 "messages": c.get("messages", []),
                 "message_count": len(c.get("messages", []))}
                for c in sorted(_s._sessions.values(), key=lambda c: c["created_at"], reverse=True)
            ]
        return JSONResponse(content=history)

    @app.post("/api/history")
    async def save_history(body: dict = None):
        from openprogram.webui import server as _s
        if body and "session_id" in body:
            session_id = body["session_id"]
            with _s._sessions_lock:
                if session_id in _s._sessions:
                    return JSONResponse(content={"saved": True})
        return JSONResponse(content={"saved": False})

    @app.get("/api/canvas")
    async def get_canvas(path: str = None):
        """Return the current canvas.md content + path + mtime."""
        import os as _os
        from openprogram.functions.tools.canvas.canvas import _resolve_path, _BLOCK_RE
        resolved = _resolve_path(path)
        try:
            st = _os.stat(resolved)
            mtime = int(st.st_mtime * 1000)
            with open(resolved, "r", encoding="utf-8") as f:
                content = f.read()
        except FileNotFoundError:
            return JSONResponse(content={
                "path": resolved, "content": "", "mtime": 0,
                "blocks": [], "exists": False,
            })
        blocks = [
            {"id": m.group("id"), "length": len(m.group("body"))}
            for m in _BLOCK_RE.finditer(content)
        ]
        return JSONResponse(content={
            "path": resolved,
            "content": content,
            "mtime": mtime,
            "blocks": blocks,
            "exists": True,
        })
