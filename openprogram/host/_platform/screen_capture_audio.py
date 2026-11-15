"""Audio-only ScreenCaptureKit streams on macOS, and the WAV their samples become.

ScreenCaptureKit hands over what a content filter plays without capturing
pixels for anyone and without touching the output device. A stream opened
here asks for 16 kHz mono float32 samples (what speech recognition wants)
and passes each buffer's bytes to a ``sink``; :func:`wav` turns collected
samples into 16-bit WAV. Callers choose the filter: computer use records
one application, Meetings everything the display plays. PyObjC is imported
only when a stream opens, so importing this module costs nothing elsewhere.
"""
from __future__ import annotations

import contextlib
import io
import struct
import threading
from array import array
from typing import Any, Callable

SAMPLE_RATE = 16_000


def wav(samples: bytes, rate: int = SAMPLE_RATE) -> tuple[bytes, float]:
    """16-bit mono WAV of native float32 ``samples``, and the peak level (0..1)."""
    floats = array("f")
    floats.frombytes(samples[: len(samples) - len(samples) % 4])
    peak = 0.0
    ints = array("h", bytes(2 * len(floats)))
    for i, value in enumerate(floats):
        value = -1.0 if value < -1.0 else 1.0 if value > 1.0 else value
        if value > peak:
            peak = value
        elif -value > peak:
            peak = -value
        ints[i] = int(value * 32767)
    if ints.itemsize != 2 or struct.pack("=h", 1) != struct.pack("<h", 1):
        ints.byteswap()
    data = ints.tobytes()
    out = io.BytesIO()
    out.write(b"RIFF" + struct.pack("<I", 36 + len(data)) + b"WAVE")
    out.write(b"fmt " + struct.pack("<IHHIIHH", 16, 1, 1, rate, rate * 2, 2, 16))
    out.write(b"data" + struct.pack("<I", len(data)) + data)
    return out.getvalue(), peak


def wait(start: Callable, timeout: float = 8.0):
    """Run a call that reports through a completion handler: (value, error) or (error)."""
    done, box = threading.Event(), []

    def callback(*args):
        box.extend(args)
        done.set()

    start(callback)
    if not done.wait(timeout):
        raise RuntimeError("the audio capture did not answer in time")
    value, error = (box[0], box[1]) if len(box) == 2 else (None, box[0] if box else None)
    if error is not None:
        raise RuntimeError(str(error))
    return value


_output_class = None


def _output(sc, appkit, cm, sink: Callable[[bytes], None]):
    """An SCStreamOutput that hands each audio buffer's bytes to ``sink``."""
    global _output_class
    if _output_class is None:
        import objc

        class _AudioOutput(appkit.NSObject, protocols=[objc.protocolNamed("SCStreamOutput")]):
            def stream_didOutputSampleBuffer_ofType_(self, stream, buffer, kind):
                if kind != sc.SCStreamOutputTypeAudio:
                    return
                block = cm.CMSampleBufferGetDataBuffer(buffer)
                if block is None:
                    return
                length = cm.CMBlockBufferGetDataLength(block)
                status, data = cm.CMBlockBufferCopyDataBytes(block, 0, length, None)
                if status == 0 and data:
                    self.sink(bytes(data))

        _output_class = _AudioOutput
    output = _output_class.alloc().init()
    output.sink = sink
    return output


def frameworks():
    """``(ScreenCaptureKit, CoreMedia, AppKit)``; ImportError without PyObjC."""
    import AppKit as appkit
    import CoreMedia as cm
    import ScreenCaptureKit as sc
    return sc, cm, appkit


def shareable_content():
    """What ScreenCaptureKit may capture; asks for Screen & System Audio Recording the first time."""
    sc, _cm, _appkit = frameworks()
    return wait(lambda cb: sc.SCShareableContent
                .getShareableContentExcludingDesktopWindows_onScreenWindowsOnly_completionHandler_(True, False, cb))


class AudioStream:
    """A running audio-only stream; keep it (and its output) alive until :meth:`stop`."""

    def __init__(self, stream: Any, output: Any):
        self.stream = stream
        self.output = output
        self._stopped = False
        self._lock = threading.Lock()

    def stop(self) -> None:
        with self._lock:
            if self._stopped:
                return
            self._stopped = True
        stream, self.stream = self.stream, None
        if stream is not None:
            with contextlib.suppress(Exception):
                wait(lambda cb: stream.stopCaptureWithCompletionHandler_(cb))


def open_audio(content_filter: Any, sink: Callable[[bytes], None]) -> AudioStream:
    """Start an audio-only stream for ``content_filter``; each buffer's float32 bytes go to ``sink``."""
    sc, cm, appkit = frameworks()
    config = sc.SCStreamConfiguration.alloc().init()
    config.setCapturesAudio_(True)
    config.setSampleRate_(SAMPLE_RATE)
    config.setChannelCount_(1)
    config.setExcludesCurrentProcessAudio_(True)
    # Audio only: the smallest frame, once a second, with no screen output attached.
    config.setWidth_(2)
    config.setHeight_(2)
    config.setMinimumFrameInterval_(cm.CMTimeMake(1, 1))
    config.setShowsCursor_(False)
    output = _output(sc, appkit, cm, sink)
    stream = sc.SCStream.alloc().initWithFilter_configuration_delegate_(content_filter, config, None)
    added, error = stream.addStreamOutput_type_sampleHandlerQueue_error_(
        output, sc.SCStreamOutputTypeAudio, None, None)
    if not added:
        raise RuntimeError(f"audio capture could not start: {error}")
    wait(lambda cb: stream.startCaptureWithCompletionHandler_(cb))
    return AudioStream(stream, output)


__all__ = ["AudioStream", "SAMPLE_RATE", "frameworks", "open_audio", "shareable_content", "wait", "wav"]
