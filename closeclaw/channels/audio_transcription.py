from __future__ import annotations

import asyncio
import logging
import re
import tempfile
import time
from pathlib import Path
from typing import Any, Awaitable, Callable, Optional

import httpx

logger = logging.getLogger(__name__)


class AudioTranscriptionError(RuntimeError):
    """Raised when audio transcription fails."""


def _lazy_import_whisper_model():
    try:
        from faster_whisper import WhisperModel  # type: ignore
    except Exception as exc:  # pragma: no cover - runtime dependency
        raise AudioTranscriptionError(
            "faster-whisper is not available. Install with: pip install faster-whisper"
        ) from exc
    return WhisperModel


def _read_voice_config(channel_config: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(channel_config, dict):
        return {}
    metadata = channel_config.get("metadata")
    if isinstance(metadata, dict):
        cfg = metadata.get("voice_transcription")
        if isinstance(cfg, dict):
            return cfg
    cfg = channel_config.get("voice_transcription")
    if isinstance(cfg, dict):
        return cfg
    return {}


def looks_like_audio(content_type: str, filename: str) -> bool:
    ct = (content_type or "").strip().lower()
    if ct.startswith("audio/"):
        return True
    name = (filename or "").strip().lower()
    return name.endswith((".ogg", ".opus", ".mp3", ".m4a", ".wav", ".aac", ".webm", ".flac"))


def guess_audio_suffix(content_type: str, filename: str = "") -> str:
    ct = (content_type or "").strip().lower()
    if "ogg" in ct or "opus" in ct:
        return ".ogg"
    if "mpeg" in ct or "mp3" in ct:
        return ".mp3"
    if "wav" in ct:
        return ".wav"
    if "m4a" in ct or "mp4" in ct or "aac" in ct:
        return ".m4a"
    if "webm" in ct:
        return ".webm"
    name = (filename or "").strip().lower()
    for ext in (".ogg", ".opus", ".mp3", ".m4a", ".wav", ".aac", ".webm", ".flac"):
        if name.endswith(ext):
            return ext
    return ".ogg"


class AudioTranscriptionService:
    """Shared audio transcription service for all channels."""

    def __init__(self, config: dict[str, Any] | None = None, channel_name: str = "unknown") -> None:
        cfg = config or {}
        self.channel_name = channel_name
        self.model_size = str(cfg.get("model_size", "base"))
        self.compute_type = str(cfg.get("compute_type", "int8"))
        self.language = cfg.get("language")
        self.timeout_seconds = float(cfg.get("timeout_seconds", 90.0))
        self.download_timeout_seconds = float(cfg.get("download_timeout_seconds", 30.0))
        self.max_audio_bytes = int(cfg.get("max_audio_bytes", 25 * 1024 * 1024))
        self.keep_temp_files = bool(cfg.get("keep_temp_files", False))
        work_dir = cfg.get("work_dir")
        self.work_dir = Path(work_dir) if work_dir else Path(tempfile.gettempdir()) / "closeclaw_audio"
        self.work_dir.mkdir(parents=True, exist_ok=True)
        self._model = None

    @classmethod
    def from_channel_config(
        cls, channel_config: dict[str, Any] | None, channel_name: str
    ) -> Optional["AudioTranscriptionService"]:
        voice_cfg = _read_voice_config(channel_config)
        if not voice_cfg:
            return None
        if not bool(voice_cfg.get("enabled", True)):
            return None
        try:
            return cls(config=voice_cfg, channel_name=channel_name)
        except Exception as exc:
            logger.warning("Audio transcription init failed for channel=%s: %s", channel_name, exc)
            return None

    def _sanitize_source_id(self, source_id: str) -> str:
        safe = re.sub(r"[^A-Za-z0-9._-]+", "_", source_id or "audio")
        return (safe or "audio")[:96]

    def _new_temp_path(self, source_id: str, suffix: str) -> Path:
        safe_id = self._sanitize_source_id(source_id)
        ts = int(time.time() * 1000)
        return self.work_dir / f"{safe_id}_{ts}{suffix}"

    def _get_model(self):
        if self._model is None:
            WhisperModel = _lazy_import_whisper_model()
            logger.info(
                "Loading faster-whisper model (channel=%s, model=%s, compute=%s)",
                self.channel_name,
                self.model_size,
                self.compute_type,
            )
            self._model = WhisperModel(self.model_size, compute_type=self.compute_type)
        return self._model

    def _validate_size(self, size_bytes: int) -> None:
        if size_bytes <= 0:
            raise AudioTranscriptionError("Downloaded audio is empty.")
        if size_bytes > self.max_audio_bytes:
            raise AudioTranscriptionError(
                f"Audio file is too large ({size_bytes} bytes > {self.max_audio_bytes} bytes limit)."
            )

    def _transcribe_local_file(self, local_path: str) -> str:
        model = self._get_model()
        try:
            segments, info = model.transcribe(local_path, language=self.language, vad_filter=True)
            text = " ".join(segment.text.strip() for segment in segments if segment.text.strip()).strip()
        except FileNotFoundError as exc:
            raise AudioTranscriptionError(
                "ffmpeg is required by faster-whisper but was not found in PATH."
            ) from exc
        except Exception as exc:
            raise AudioTranscriptionError(f"Audio transcription failed: {exc}") from exc

        if not text:
            raise AudioTranscriptionError("Transcription completed but produced empty text.")

        logger.info(
            "Audio transcription succeeded (channel=%s, language=%s, duration=%s)",
            self.channel_name,
            getattr(info, "language", None),
            getattr(info, "duration", None),
        )
        return text

    async def transcribe_via_downloader(
        self,
        source_id: str,
        downloader: Callable[[Path], Awaitable[None]],
        *,
        mime_type: str = "",
        filename: str = "",
    ) -> str:
        suffix = guess_audio_suffix(mime_type, filename)
        local_path = self._new_temp_path(source_id=source_id, suffix=suffix)
        try:
            await asyncio.wait_for(downloader(local_path), timeout=self.download_timeout_seconds)
            self._validate_size(local_path.stat().st_size)
            transcript = await asyncio.wait_for(
                asyncio.to_thread(self._transcribe_local_file, str(local_path)),
                timeout=self.timeout_seconds,
            )
            return transcript.strip()
        except asyncio.TimeoutError as exc:
            raise AudioTranscriptionError("Audio transcription timed out.") from exc
        except AudioTranscriptionError:
            raise
        except Exception as exc:
            raise AudioTranscriptionError(f"Audio transcription failed: {exc}") from exc
        finally:
            if not self.keep_temp_files:
                try:
                    local_path.unlink(missing_ok=True)
                except Exception:
                    pass

    async def transcribe_from_url(
        self,
        url: str,
        source_id: str,
        *,
        mime_type: str = "",
        filename: str = "",
        headers: dict[str, str] | None = None,
    ) -> str:
        if not url:
            raise AudioTranscriptionError("Missing audio URL.")

        async def _download(target_path: Path) -> None:
            timeout = httpx.Timeout(self.download_timeout_seconds)
            async with httpx.AsyncClient(timeout=timeout) as client:
                resp = await client.get(url, headers=headers)
                resp.raise_for_status()
                body = resp.content
                self._validate_size(len(body))
                target_path.write_bytes(body)

        return await self.transcribe_via_downloader(
            source_id=source_id,
            downloader=_download,
            mime_type=mime_type,
            filename=filename,
        )

    async def transcribe_from_bytes(
        self,
        payload: bytes,
        source_id: str,
        *,
        mime_type: str = "",
        filename: str = "",
    ) -> str:
        self._validate_size(len(payload))

        async def _download(target_path: Path) -> None:
            target_path.write_bytes(payload)

        return await self.transcribe_via_downloader(
            source_id=source_id,
            downloader=_download,
            mime_type=mime_type,
            filename=filename,
        )
