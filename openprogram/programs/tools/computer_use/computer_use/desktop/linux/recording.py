"""Recording the sound one application plays, from its PulseAudio or PipeWire streams."""
from __future__ import annotations

import re
import shutil
import subprocess
import threading
import time

from ..common import audio
from ..common.base import AudioClip, ComputerUseError, Report, save_recording
from .x11 import _Recording, _descendants


class Recording:
    """The start_audio and stop_audio calls."""

    @staticmethod
    def _streams(pids: set[int]) -> list[int]:
        """PulseAudio / PipeWire sink inputs that ``pids`` play."""
        try:
            listed = subprocess.run(["pactl", "list", "sink-inputs"], capture_output=True, text=True, timeout=5).stdout
        except (OSError, subprocess.TimeoutExpired):
            return []
        out = []
        for block in re.split(r"\n(?=Sink Input #)", listed):
            head = re.match(r"Sink Input #(\d+)", block.strip())
            owner = re.search(r'application\.process\.id = "(\d+)"', block)
            if head and owner and int(owner.group(1)) in pids:
                out.append(int(head.group(1)))
        return out

    def start_audio(self, session: str, query: str, max_duration=None) -> Report:
        try:
            limit = audio.seconds(max_duration)
        except ValueError as exc:
            raise ComputerUseError(str(exc)) from exc
        if shutil.which("parec") is None or shutil.which("pactl") is None:
            raise ComputerUseError("Recording needs PulseAudio or PipeWire with pulseaudio-utils (parec, pactl).")
        with self._lock:
            running = self._recordings.get(session)
            if running is not None and not running.stopped:
                raise ComputerUseError(f"Already recording {running.app}; call stop_audio_recording first.")
            app = self.resolve_app(query)
            recording = _Recording(app=app.name, pid=app.pid, limit=limit)
            recording.thread = threading.Thread(target=self._record, args=(recording,), daemon=True,
                                                name=f"computer-use-audio-{app.pid}")
            recording.thread.start()
            self._recordings[session] = recording
            return Report(f"Recording the sound {app.name} plays, for up to {limit:g} seconds; the user still hears "
                          "it and nothing else is recorded. Act as usual, then call stop_audio_recording.")

    def _record(self, recording: _Recording) -> None:
        """Wait for the app to open a stream, then read it until stopped or the limit."""
        deadline = time.monotonic() + recording.limit
        stream = None
        while stream is None and not recording.stopped and time.monotonic() < deadline:
            # Sound often comes from a child started after recording began (a player, a renderer).
            found = self._streams(_descendants(recording.pid))
            stream = found[0] if found else None
            if stream is None:
                time.sleep(0.25)
        if stream is None:
            return
        try:
            process = subprocess.Popen(
                ["parec", f"--monitor-stream={stream}", "--format=float32le", f"--rate={audio.SAMPLE_RATE}",
                 "--channels=1", "--raw", "--latency-msec=50"], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        except OSError as exc:
            recording.error = str(exc)
            return
        recording.process = process
        # Read until parec ends (stop terminates it), so the samples it still buffers are kept.
        while recording.seconds < recording.limit:
            chunk = process.stdout.read1(6400)
            if not chunk:
                break
            recording.take(chunk)
        process.terminate()

    def stop_audio(self, session: str, query: str = "") -> AudioClip:
        with self._lock:
            recording = self._recordings.get(session)
            if recording is None:
                raise ComputerUseError("Nothing is being recorded; call start_audio_recording first.")
            if query:
                app = self._running_match(query)
                if app is not None and app.pid != recording.pid:
                    raise ComputerUseError(f"The recording is of {recording.app}, not {query}.")
            del self._recordings[session]
        recording.stopped = True
        if recording.process is not None:
            recording.process.terminate()
        if recording.thread is not None:
            recording.thread.join(timeout=3)
        if recording.error:
            raise ComputerUseError(f"Could not record {recording.app}: {recording.error}")
        with recording.lock:
            data, peak = audio.wav(b"".join(recording.chunks))
        return AudioClip(app=recording.app, path=save_recording(recording.app, data), data=data,
                         seconds=round(recording.seconds, 2), peak=peak)
