"""Record one app's own sound on Windows through WASAPI process loopback.

``ActivateAudioInterfaceAsync`` on the ``VAD\\Process_Loopback`` device with a
target process id gives an audio client that captures what that process tree
plays (Windows 10 2004 and later); the user keeps hearing it. The client
converts to the recorder's format, 16 kHz mono float32, which ``audio.wav``
turns into the WAV the tool returns.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import ctypes
from ctypes import POINTER, byref, c_uint32, c_uint64, c_void_p, wintypes
import threading
import time

from . import audio

LOOPBACK_DEVICE = "VAD\\Process_Loopback"
ACTIVATION_PROCESS_LOOPBACK = 1
INCLUDE_TARGET_PROCESS_TREE = 0
VT_BLOB = 65
STREAMFLAGS = 0x00020000 | 0x00040000 | 0x80000000 | 0x08000000  # loopback, event, autoconvert, SRC quality
BUFFER_100NS = 2_000_000  # 200 ms
FLAG_SILENT = 0x2


@dataclass
class Recording:
    app: str
    pid: int
    limit: float
    chunks: list[bytes] = field(default_factory=list)
    frames: int = 0
    thread: threading.Thread | None = None
    stop: threading.Event = field(default_factory=threading.Event)
    ready: threading.Event = field(default_factory=threading.Event)
    stopped: bool = False
    error: str = ""

    def take(self, data: bytes) -> None:
        room = int(self.limit * audio.SAMPLE_RATE) - self.frames
        if room <= 0:
            self.stopped = True
            return
        data = data[: room * 4]
        self.chunks.append(data)
        self.frames += len(data) // 4

    @property
    def seconds(self) -> float:
        return self.frames / audio.SAMPLE_RATE


def _interfaces():
    """comtypes declarations of the WASAPI interfaces process loopback needs."""
    import comtypes
    from comtypes import COMMETHOD, GUID, HRESULT, IUnknown

    class IAudioCaptureClient(IUnknown):
        _iid_ = GUID("{C8ADBD64-E71E-48a0-A4DE-185C395CD317}")
        _methods_ = [
            COMMETHOD([], HRESULT, "GetBuffer", (["out"], POINTER(POINTER(ctypes.c_byte)), "data"),
                      (["out"], POINTER(c_uint32), "frames"), (["out"], POINTER(wintypes.DWORD), "flags"),
                      (["out"], POINTER(c_uint64), "position"), (["out"], POINTER(c_uint64), "qpc")),
            COMMETHOD([], HRESULT, "ReleaseBuffer", (["in"], c_uint32, "frames")),
            COMMETHOD([], HRESULT, "GetNextPacketSize", (["out"], POINTER(c_uint32), "frames")),
        ]

    class IAudioClient(IUnknown):
        _iid_ = GUID("{1CB9AD4C-DBFA-4c32-B178-C2F568A703B2}")
        _methods_ = [
            COMMETHOD([], HRESULT, "Initialize", (["in"], ctypes.c_int, "mode"), (["in"], wintypes.DWORD, "flags"),
                      (["in"], ctypes.c_longlong, "buffer"), (["in"], ctypes.c_longlong, "period"),
                      (["in"], c_void_p, "format"), (["in"], c_void_p, "session")),
            COMMETHOD([], HRESULT, "GetBufferSize", (["out"], POINTER(c_uint32), "frames")),
            COMMETHOD([], HRESULT, "GetStreamLatency", (["out"], POINTER(ctypes.c_longlong), "latency")),
            COMMETHOD([], HRESULT, "GetCurrentPadding", (["out"], POINTER(c_uint32), "frames")),
            COMMETHOD([], HRESULT, "IsFormatSupported", (["in"], ctypes.c_int, "mode"), (["in"], c_void_p, "format"),
                      (["out"], POINTER(c_void_p), "closest")),
            COMMETHOD([], HRESULT, "GetMixFormat", (["out"], POINTER(c_void_p), "format")),
            COMMETHOD([], HRESULT, "GetDevicePeriod", (["out"], POINTER(ctypes.c_longlong), "default"),
                      (["out"], POINTER(ctypes.c_longlong), "minimum")),
            COMMETHOD([], HRESULT, "Start"),
            COMMETHOD([], HRESULT, "Stop"),
            COMMETHOD([], HRESULT, "Reset"),
            COMMETHOD([], HRESULT, "SetEventHandle", (["in"], wintypes.HANDLE, "event")),
            COMMETHOD([], HRESULT, "GetService", (["in"], POINTER(GUID), "iid"),
                      (["out"], POINTER(POINTER(IUnknown)), "service")),
        ]

    class IActivateAudioInterfaceAsyncOperation(IUnknown):
        _iid_ = GUID("{72A22D78-CDE4-431D-B8CC-843A71199B6D}")
        _methods_ = [
            COMMETHOD([], HRESULT, "GetActivateResult", (["out"], POINTER(HRESULT), "result"),
                      (["out"], POINTER(POINTER(IUnknown)), "interface")),
        ]

    class IActivateAudioInterfaceCompletionHandler(IUnknown):
        _iid_ = GUID("{41D949AB-9862-444A-80F6-C261334DA5EB}")
        _methods_ = [
            COMMETHOD([], HRESULT, "ActivateCompleted",
                      (["in"], POINTER(IActivateAudioInterfaceAsyncOperation), "operation")),
        ]

    class IAgileObject(IUnknown):
        # The handler is called on a worker thread; agile objects need no marshalling.
        _iid_ = GUID("{94ea2b94-e9cc-49e0-c0ff-ee64ca8f5b90}")
        _methods_ = []

    class Handler(comtypes.COMObject):
        _com_interfaces_ = [IActivateAudioInterfaceCompletionHandler, IAgileObject]

        def __init__(self) -> None:
            super().__init__()
            self.done = threading.Event()

        def ActivateCompleted(self, operation):  # noqa: N802 - COM method name
            self.done.set()
            return 0

    return IAudioClient, IAudioCaptureClient, IActivateAudioInterfaceAsyncOperation, \
        IActivateAudioInterfaceCompletionHandler, Handler


class _ProcessLoopbackParams(ctypes.Structure):
    _fields_ = [("TargetProcessId", wintypes.DWORD), ("ProcessLoopbackMode", ctypes.c_int)]


class _ActivationParams(ctypes.Structure):
    _fields_ = [("ActivationType", ctypes.c_int), ("ProcessLoopbackParams", _ProcessLoopbackParams)]


class _Blob(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.ULONG), ("pBlobData", c_void_p)]


class _PropVariant(ctypes.Structure):
    _fields_ = [("vt", ctypes.c_ushort), ("r1", ctypes.c_ushort), ("r2", ctypes.c_ushort), ("r3", ctypes.c_ushort),
                ("blob", _Blob), ("pad", c_void_p)]


class _WaveFormatEx(ctypes.Structure):
    _pack_ = 1
    _fields_ = [("wFormatTag", ctypes.c_ushort), ("nChannels", ctypes.c_ushort), ("nSamplesPerSec", ctypes.c_uint32),
                ("nAvgBytesPerSec", ctypes.c_uint32), ("nBlockAlign", ctypes.c_ushort),
                ("wBitsPerSample", ctypes.c_ushort), ("cbSize", ctypes.c_ushort)]


def recorder_format() -> _WaveFormatEx:
    """16 kHz mono IEEE float, the format every backend records in."""
    return _WaveFormatEx(3, 1, audio.SAMPLE_RATE, audio.SAMPLE_RATE * 4, 4, 32, 0)


def _activate(pid: int):
    import comtypes
    IAudioClient, _, Operation, CompletionHandler, Handler = _interfaces()
    params = _ActivationParams(ACTIVATION_PROCESS_LOOPBACK, _ProcessLoopbackParams(pid, INCLUDE_TARGET_PROCESS_TREE))
    variant = _PropVariant(vt=VT_BLOB, blob=_Blob(ctypes.sizeof(params), ctypes.cast(byref(params), c_void_p)))
    mmdevapi = ctypes.WinDLL("Mmdevapi")
    activate = mmdevapi.ActivateAudioInterfaceAsync
    activate.argtypes = [wintypes.LPCWSTR, POINTER(comtypes.GUID), POINTER(_PropVariant),
                         POINTER(CompletionHandler), POINTER(POINTER(Operation))]
    activate.restype = ctypes.HRESULT
    handler = Handler()
    operation = POINTER(Operation)()
    activate(LOOPBACK_DEVICE, byref(IAudioClient._iid_), byref(variant),
             handler.QueryInterface(CompletionHandler), byref(operation))
    if not handler.done.wait(5):
        raise RuntimeError("Windows did not open the app's audio stream")
    result, client = operation.GetActivateResult()
    if result < 0:
        raise OSError(f"process loopback is unavailable (HRESULT {result & 0xFFFFFFFF:#010x}); it needs Windows 10 "
                      "version 2004 or later")
    return client.QueryInterface(IAudioClient)


def _run(recording: Recording) -> None:
    import comtypes
    comtypes.CoInitializeEx(comtypes.COINIT_MULTITHREADED)
    kernel32 = ctypes.WinDLL("kernel32")
    event = kernel32.CreateEventW(None, False, False, None)
    try:
        _, CaptureClient, *_ = _interfaces()
        client = _activate(recording.pid)
        fmt = recorder_format()
        client.Initialize(0, STREAMFLAGS, BUFFER_100NS, 0, ctypes.cast(byref(fmt), c_void_p), None)
        client.SetEventHandle(event)
        capture = client.GetService(byref(CaptureClient._iid_)).QueryInterface(CaptureClient)
        client.Start()
        recording.ready.set()
        try:
            while not recording.stop.is_set() and not recording.stopped:
                kernel32.WaitForSingleObject(event, 100)
                while capture.GetNextPacketSize():
                    data, frames, flags, _, _ = capture.GetBuffer()
                    if flags & FLAG_SILENT or not data:
                        recording.take(bytes(frames * 4))
                    else:
                        recording.take(ctypes.string_at(data, frames * 4))
                    capture.ReleaseBuffer(frames)
        finally:
            client.Stop()
    except Exception as exc:  # noqa: BLE001 - reported by start() or finish()
        recording.error = str(exc) or type(exc).__name__
        recording.ready.set()
    finally:
        recording.stopped = True
        kernel32.CloseHandle(event)
        comtypes.CoUninitialize()


def start(pid: int, app: str, limit: float) -> Recording:
    recording = Recording(app=app, pid=pid, limit=limit)
    recording.thread = threading.Thread(target=_run, args=(recording,), name="computer-use-audio", daemon=True)
    recording.thread.start()
    recording.ready.wait(8)
    if recording.error:
        raise RuntimeError(recording.error)
    if not recording.ready.is_set():
        recording.stop.set()
        raise RuntimeError("the audio stream did not start")
    return recording


def finish(recording: Recording) -> tuple[bytes, float]:
    recording.stop.set()
    if recording.thread is not None:
        recording.thread.join(timeout=3)
    time.sleep(0)
    return audio.wav(b"".join(recording.chunks))


__all__ = ["Recording", "finish", "recorder_format", "start"]
