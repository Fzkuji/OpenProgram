"""The channel and speech links: what the core sees with and without the interfaces."""
from __future__ import annotations

import pytest

from openprogram.runtime import channel_link, speech_link


@pytest.fixture
def no_channels():
    registered = channel_link.current()
    channel_link.unregister()
    yield
    channel_link.register(registered)


@pytest.fixture
def no_speech():
    registered = speech_link.current()
    speech_link.unregister()
    yield
    speech_link.register(registered)


def test_importing_the_interfaces_registers_their_links():
    import openprogram.interfaces.channels  # noqa: F401
    import openprogram.interfaces.speech  # noqa: F401
    from openprogram.interfaces.channels.link_registration import Channels
    from openprogram.interfaces.speech.link_registration import SpeechToText

    assert isinstance(channel_link.current(), Channels)
    assert isinstance(speech_link.current(), SpeechToText)


def test_without_channels_every_operation_names_the_missing_interface(no_channels):
    for call in (
        lambda: channel_link.send_message("telegram", "default", "1", "hi"),
        lambda: channel_link.post_message("telegram", "default", "1", "hi"),
        lambda: channel_link.account_ids("telegram"),
        lambda: channel_link.peer_bindings("main"),
        lambda: channel_link.active_turn("s"),
        lambda: channel_link.take_send("s"),
        lambda: channel_link.sends_per_turn(),
    ):
        with pytest.raises(channel_link.ChannelsUnavailable, match="no channel interface"):
            call()


def test_chat_send_without_channels_reports_it(no_channels):
    from openprogram.programs.tools.interaction.chat_send import _chat_send_impl

    assert _chat_send_impl("hello").startswith("Error: no channel interface is registered")


def test_a_reminder_without_channels_reports_the_failed_send(no_channels, caplog):
    from openprogram.runtime.scheduler import reminders

    with caplog.at_level("WARNING", logger="openprogram.runtime.scheduler.reminders"):
        reminders._send_to_channel({"channel": "telegram", "peer_id": "1"}, "hello")
    assert "ChannelsUnavailable" in caplog.text


def test_without_speech_a_transcription_names_the_missing_interface(no_speech):
    with pytest.raises(speech_link.SpeechUnavailable, match="no speech interface"):
        speech_link.transcribe(b"RIFF")
    assert issubclass(speech_link.SpeechUnavailable, speech_link.TranscriptionError)


def test_the_speech_interface_errors_are_transcription_errors():
    from openprogram.interfaces.speech import stt

    assert issubclass(stt.VoiceError, speech_link.TranscriptionError)


def test_spawned_children_reimport_the_registered_link_modules(no_channels, no_speech):
    """A spawn child starts without the parent's registrations; importing the
    modules the parent names registers the same implementations again."""
    import sys
    from openprogram.runtime.conversation import process_runner

    assert process_runner._link_registration_modules() == []
    channel_link.register(_fresh_registration("openprogram.interfaces.channels.link_registration"))
    speech_link.register(_fresh_registration("openprogram.interfaces.speech.link_registration"))
    modules = process_runner._link_registration_modules()
    assert modules == [
        "openprogram.interfaces.channels.link_registration",
        "openprogram.interfaces.speech.link_registration",
    ]
    channel_link.unregister()
    speech_link.unregister()
    for module in modules:
        sys.modules.pop(module, None)  # as in a fresh interpreter
    process_runner._import_link_registrations(modules)
    assert type(channel_link.current()).__module__ == modules[0]
    assert type(speech_link.current()).__module__ == modules[1]


def _fresh_registration(module_name: str):
    """The link object a module registers when imported, without touching the
    current registration."""
    import importlib
    import sys

    registered = (channel_link.current(), speech_link.current())
    sys.modules.pop(module_name, None)
    module = importlib.import_module(module_name)
    link = channel_link.current() if module_name.startswith("openprogram.interfaces.channels") else speech_link.current()
    channel_link.unregister(); speech_link.unregister()
    if registered[0] is not None:
        channel_link.register(registered[0])
    if registered[1] is not None:
        speech_link.register(registered[1])
    assert type(link).__module__ == module.__name__
    return link


# -- what applications reach through the link (capability ``speech``) --------

def test_without_speech_files_cannot_be_transcribed_and_readiness_says_so(no_speech):
    with pytest.raises(speech_link.SpeechUnavailable):
        speech_link.transcribe_file("/nowhere.webm")
    with pytest.raises(speech_link.SpeechUnavailable):
        speech_link.diarize([(0, "/nowhere.wav")])
    assert speech_link.readiness() == {"speech": False, "ffmpeg": False, "ffmpeg_missing": ""}


def test_a_silent_recording_file_is_not_sent_to_speech_to_text(tmp_path, monkeypatch):
    import wave
    from openprogram.interfaces.speech import audio, stt
    from openprogram.interfaces.speech.link_registration import SpeechToText

    path = tmp_path / "quiet.wav"
    with wave.open(str(path), "wb") as writer:
        writer.setnchannels(1)
        writer.setsampwidth(2)
        writer.setframerate(16000)
        writer.writeframes(b"\0\0" * 16000)
    monkeypatch.setattr(audio, "ffmpeg", lambda: None)              # speech-form WAV needs no ffmpeg
    monkeypatch.setattr(stt, "transcribe_segments", lambda *a, **k: pytest.fail("silence must not be transcribed"))
    assert SpeechToText().transcribe_file(str(path)) == {
        "duration_ms": 1000, "silent": True, "segments": [], "engine": "", "model": ""}


def test_speaker_turns_are_placed_on_the_tracks_clock_and_split_at_chunk_gaps():
    from openprogram.interfaces.speech import diarize
    # Two chunks, concatenated back to back: 0–2 s is the track's 0–2 s, 2–4 s is its 30–32 s.
    spans = [(0, 0, 2000), (2000, 30_000, 2000)]
    assert diarize.to_track_clock([(1.0, 3.0, 4), (3.5, 4.0, 2)], spans) == [
        (1000, 2000, 4), (30_000, 31_000, 4), (31_500, 32_000, 2)]
