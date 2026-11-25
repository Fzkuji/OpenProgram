"""Text in an image, read by the OCR engine Windows ships (``Windows.Media.Ocr``).

It needs no download: Windows Media OCR reads the languages of the user's
profile whose recognizer is installed (Chinese on a Chinese Windows,
English everywhere). PowerShell 5.1, present on every Windows 10 and 11,
reaches the WinRT API without any Python binding.
"""
from __future__ import annotations

import base64
import json
import os
import re
import subprocess
import tempfile

_SCRIPT = r"""
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
Add-Type -AssemblyName System.Runtime.WindowsRuntime
$null = [Windows.Storage.StorageFile, Windows.Storage, ContentType = WindowsRuntime]
$null = [Windows.Media.Ocr.OcrEngine, Windows.Foundation, ContentType = WindowsRuntime]
$null = [Windows.Graphics.Imaging.BitmapDecoder, Windows.Graphics, ContentType = WindowsRuntime]
$asTask = ([System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object {
  $_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 -and $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1'
})[0]
function Await($op, $type) { $t = $asTask.MakeGenericMethod($type).Invoke($null, @($op)); $t.Wait(-1) | Out-Null; $t.Result }
$file = Await ([Windows.Storage.StorageFile]::GetFileFromPathAsync($env:OPENPROGRAM_OCR_IMAGE)) ([Windows.Storage.StorageFile])
$stream = Await ($file.OpenAsync([Windows.Storage.FileAccessMode]::Read)) ([Windows.Storage.Streams.IRandomAccessStream])
$decoder = Await ([Windows.Graphics.Imaging.BitmapDecoder]::CreateAsync($stream)) ([Windows.Graphics.Imaging.BitmapDecoder])
$bitmap = Await ($decoder.GetSoftwareBitmapAsync()) ([Windows.Graphics.Imaging.SoftwareBitmap])
$engine = [Windows.Media.Ocr.OcrEngine]::TryCreateFromUserProfileLanguages()
if ($null -eq $engine) { '[]'; exit 0 }
$result = Await ($engine.RecognizeAsync($bitmap)) ([Windows.Media.Ocr.OcrResult])
$lines = @(foreach ($line in $result.Lines) {
  $r = @($line.Words | ForEach-Object { $_.BoundingRect })
  $x0 = ($r | Measure-Object -Property X -Minimum).Minimum
  $y0 = ($r | Measure-Object -Property Y -Minimum).Minimum
  $x1 = ($r | ForEach-Object { $_.X + $_.Width } | Measure-Object -Maximum).Maximum
  $y1 = ($r | ForEach-Object { $_.Y + $_.Height } | Measure-Object -Maximum).Maximum
  [pscustomobject]@{ text = $line.Text; box = @($x0, $y0, ($x1 - $x0), ($y1 - $y0)) }
})
ConvertTo-Json -InputObject $lines -Compress -Depth 4
"""

# Windows Media OCR puts a space between every CJK character of a line.
_CJK_GAP = re.compile(r"(?<=[　-鿿가-힯＀-￯]) (?=[　-鿿가-힯＀-￯])")


def tidy(text: str) -> str:
    return _CJK_GAP.sub("", " ".join(str(text).split()))


def lines_from_json(output: str) -> list[dict]:
    """The script's JSON as ``[{text, box: [x, y, w, h]}]``, blank lines dropped."""
    try:
        found = json.loads(output or "[]")
    except ValueError:
        return []
    if isinstance(found, dict):
        found = [found]
    out = []
    for item in found if isinstance(found, list) else []:
        box = item.get("box") if isinstance(item, dict) else None
        text = tidy(item.get("text", "")) if isinstance(item, dict) else ""
        if text and isinstance(box, list) and len(box) == 4:
            out.append({"text": text, "box": [float(v or 0) for v in box]})
    return out


def recognize(png: bytes) -> list[dict]:
    """Lines of text in a PNG, in its pixels; empty where the engine is missing."""
    if not png:
        return []
    handle, path = tempfile.mkstemp(suffix=".png")
    try:
        with os.fdopen(handle, "wb") as file:
            file.write(png)
        command = base64.b64encode(_SCRIPT.encode("utf-16-le")).decode("ascii")
        done = subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
             "-EncodedCommand", command],
            capture_output=True, timeout=60, env={**os.environ, "OPENPROGRAM_OCR_IMAGE": path},
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        return lines_from_json(done.stdout.decode("utf-8", "replace")) if done.returncode == 0 else []
    except (OSError, subprocess.SubprocessError):
        return []
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass


__all__ = ["lines_from_json", "recognize", "tidy"]
