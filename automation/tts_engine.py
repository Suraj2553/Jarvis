"""Voice prompt playback for the human side of the demo."""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import subprocess
import tempfile
from pathlib import Path

import config

LOG = logging.getLogger(__name__)


class PromptTTS:
    def __init__(self) -> None:
        self._edge_available: bool | None = None
        self._pyttsx3 = None

    def speak_prompt(self, text: str, language: str = "en") -> None:
        if not config.TTS_ENABLED or text.startswith("/"):
            return
        provider = getattr(config, "TTS_PROVIDER", "auto").lower()
        if provider == "pyttsx3":
            self._speak_pyttsx3(text)
            return
        if provider in {"auto", "edge"} and self._can_edge():
            try:
                asyncio.run(self._speak_edge(text, language))
                return
            except Exception as exc:
                LOG.warning("edge-tts failed, falling back to pyttsx3: %s", exc)
        self._speak_pyttsx3(text)

    def _can_edge(self) -> bool:
        if self._edge_available is not None:
            return self._edge_available
        try:
            import edge_tts  # noqa: F401
            self._edge_available = True
        except Exception:
            self._edge_available = False
        return self._edge_available

    async def _speak_edge(self, text: str, language: str) -> None:
        import edge_tts

        voice = config.EDGE_TTS_VOICE_HI if language == "hi" else config.EDGE_TTS_VOICE_EN
        digest = hashlib.sha1(f"{voice}|{text}".encode("utf-8")).hexdigest()[:16]
        out = Path(tempfile.gettempdir()) / f"jarvis_prompt_{digest}.mp3"
        if not out.exists():
            communicate = edge_tts.Communicate(
                text,
                voice,
                rate=config.TTS_RATE,
                volume=config.TTS_VOLUME,
            )
            await communicate.save(str(out))
        self._play_audio(out)

    def _play_audio(self, path: Path) -> None:
        try:
            import winsound
            winsound.PlaySound(str(path), winsound.SND_FILENAME)
            return
        except Exception:
            pass
        # winsound cannot play MP3 on many Windows builds. MediaPlayer gives us
        # blocking playback without adding another dependency.
        safe_path = str(path).replace("'", "''")
        ps = (
            "Add-Type -AssemblyName PresentationCore; "
            f"$p='{safe_path}'; "
            "$m=New-Object System.Windows.Media.MediaPlayer; "
            "$m.Open([uri]$p); Start-Sleep -Milliseconds 250; "
            "$d=$m.NaturalDuration.TimeSpan.TotalMilliseconds; "
            "$m.Play(); "
            "if($d -gt 0){ Start-Sleep -Milliseconds ([int]($d + 300)) } "
            "else { Start-Sleep -Seconds 3 }"
        )
        try:
            subprocess.run(
                ["powershell", "-Sta", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", ps],
                check=False,
                timeout=45,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except Exception:
            os.startfile(str(path))  # type: ignore[attr-defined]

    def _speak_pyttsx3(self, text: str) -> None:
        try:
            if self._pyttsx3 is None:
                import pyttsx3
                self._pyttsx3 = pyttsx3.init()
                self._pyttsx3.setProperty("rate", getattr(config, "PYTTSX3_RATE", 138))
                self._pyttsx3.setProperty("volume", getattr(config, "PYTTSX3_VOLUME", 1.0))
            self._pyttsx3.say(text)
            self._pyttsx3.runAndWait()
        except Exception as exc:
            LOG.warning("pyttsx3 prompt playback failed: %s", exc)
