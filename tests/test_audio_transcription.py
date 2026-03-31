from __future__ import annotations

from pathlib import Path
import shutil
import uuid

import httpx
import pytest

from closeclaw.channels.audio_transcription import (
    AudioTranscriptionError,
    AudioTranscriptionService,
    guess_audio_suffix,
    looks_like_audio,
)


def _make_workspace_temp_dir() -> Path:
    base = Path("tests") / "_tmp_audio"
    base.mkdir(parents=True, exist_ok=True)
    target = base / f"case_{uuid.uuid4().hex}"
    target.mkdir(parents=True, exist_ok=True)
    return target


def test_audio_helpers_detect_audio_and_suffix() -> None:
    assert looks_like_audio("audio/ogg", "")
    assert looks_like_audio("", "voice_note.m4a")
    assert not looks_like_audio("image/png", "pic.png")
    assert guess_audio_suffix("audio/ogg") == ".ogg"
    assert guess_audio_suffix("audio/mpeg") == ".mp3"
    assert guess_audio_suffix("", "clip.webm") == ".webm"


def test_audio_service_from_channel_config_metadata_and_top_level() -> None:
    svc_meta = AudioTranscriptionService.from_channel_config(
        {"metadata": {"voice_transcription": {"enabled": True}}},
        channel_name="telegram",
    )
    assert svc_meta is not None

    svc_top = AudioTranscriptionService.from_channel_config(
        {"voice_transcription": {"enabled": True}},
        channel_name="discord",
    )
    assert svc_top is not None

    svc_disabled = AudioTranscriptionService.from_channel_config(
        {"metadata": {"voice_transcription": {"enabled": False}}},
        channel_name="qq",
    )
    assert svc_disabled is None


@pytest.mark.asyncio
async def test_transcribe_from_bytes_success_and_temp_cleanup(monkeypatch: pytest.MonkeyPatch) -> None:
    work_dir = _make_workspace_temp_dir()
    try:
        service = AudioTranscriptionService(
            config={
                "work_dir": str(work_dir),
                "keep_temp_files": False,
                "max_audio_bytes": 1024 * 1024,
                "timeout_seconds": 5,
                "download_timeout_seconds": 5,
            },
            channel_name="test",
        )

        def _fake_transcribe(local_path: str) -> str:
            assert Path(local_path).exists()
            return "hello world"

        monkeypatch.setattr(service, "_transcribe_local_file", _fake_transcribe)

        text = await service.transcribe_from_bytes(
            payload=b"fake-audio-bytes",
            source_id="msg_1",
            mime_type="audio/ogg",
            filename="voice.ogg",
        )

        assert text == "hello world"
        assert list(work_dir.iterdir()) == []
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)


@pytest.mark.asyncio
async def test_transcribe_from_bytes_reject_large_payload() -> None:
    work_dir = _make_workspace_temp_dir()
    try:
        service = AudioTranscriptionService(
            config={
                "work_dir": str(work_dir),
                "max_audio_bytes": 4,
            },
            channel_name="test",
        )

        with pytest.raises(AudioTranscriptionError):
            await service.transcribe_from_bytes(
                payload=b"12345",
                source_id="too_large",
                mime_type="audio/ogg",
            )
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)


@pytest.mark.asyncio
async def test_transcribe_from_url_success(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeAsyncClient:
        def __init__(self, timeout):
            self.timeout = timeout

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def get(self, url, headers=None):
            req = httpx.Request("GET", url)
            return httpx.Response(200, request=req, content=b"fake-url-audio")

    monkeypatch.setattr("closeclaw.channels.audio_transcription.httpx.AsyncClient", FakeAsyncClient)

    work_dir = _make_workspace_temp_dir()
    try:
        service = AudioTranscriptionService(
            config={
                "work_dir": str(work_dir),
                "max_audio_bytes": 1024 * 1024,
            },
            channel_name="test",
        )
        monkeypatch.setattr(service, "_transcribe_local_file", lambda _: "url transcript")

        text = await service.transcribe_from_url(
            url="https://example.test/audio.ogg",
            source_id="url_1",
            mime_type="audio/ogg",
        )
        assert text == "url transcript"
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)


@pytest.mark.asyncio
async def test_transcribe_from_url_http_error(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeAsyncClient:
        def __init__(self, timeout):
            self.timeout = timeout

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def get(self, url, headers=None):
            req = httpx.Request("GET", url)
            return httpx.Response(500, request=req, content=b"boom")

    monkeypatch.setattr("closeclaw.channels.audio_transcription.httpx.AsyncClient", FakeAsyncClient)

    work_dir = _make_workspace_temp_dir()
    try:
        service = AudioTranscriptionService(
            config={
                "work_dir": str(work_dir),
                "max_audio_bytes": 1024 * 1024,
            },
            channel_name="test",
        )

        with pytest.raises(AudioTranscriptionError):
                await service.transcribe_from_url(
                    url="https://example.test/audio.ogg",
                    source_id="url_fail",
                    mime_type="audio/ogg",
                )
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)
