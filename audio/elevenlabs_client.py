"""ElevenLabs TTS client for JARVIS.

Uses the REST streaming endpoint with raw PCM output so playback can stay on
the existing sounddevice stack without adding an MP3 decoder dependency.
"""

from __future__ import annotations

import os
import threading
from typing import Optional

import requests


_BASE_URL = "https://api.elevenlabs.io/v1"
_DEFAULT_OUTPUT_FORMAT = "pcm_24000"
_DEFAULT_SAMPLE_RATE = 24000
_DEFAULT_MODEL = "eleven_flash_v2_5"


def _sample_rate_from_format(output_format: str) -> int:
    try:
        return int(output_format.rsplit("_", 1)[1])
    except Exception:
        return _DEFAULT_SAMPLE_RATE


class ElevenLabsTTS:
    """Small ElevenLabs text-to-speech wrapper."""

    def __init__(self, api_key: str, config: Optional[dict] = None):
        self._key = api_key
        self._config = config or {}
        self._stop = threading.Event()
        self._session = requests.Session()
        self._session.headers.update({
            "xi-api-key": api_key,
            "Accept": "application/octet-stream",
            "Content-Type": "application/json",
        })

    @classmethod
    def from_config(cls, config: dict) -> Optional["ElevenLabsTTS"]:
        key = (
            os.getenv("ELEVENLABS_API_KEY", "").strip()
            or config.get("elevenlabs_api_key", "").strip()
        )
        voice_id = config.get("elevenlabs_voice_id", "").strip()
        if not key or not voice_id:
            return None
        return cls(key, config)

    def stop(self) -> None:
        self._stop.set()
        try:
            import sounddevice as sd
            sd.stop()
        except Exception:
            pass

    def reset(self) -> None:
        self._stop.clear()

    def speak(self, text: str) -> None:
        voice_id = self._config.get("elevenlabs_voice_id", "").strip()
        if not voice_id:
            raise RuntimeError("elevenlabs_voice_id is not configured")

        output_format = self._config.get(
            "elevenlabs_output_format", _DEFAULT_OUTPUT_FORMAT
        )
        sample_rate = _sample_rate_from_format(output_format)
        model_id = self._config.get("elevenlabs_model", _DEFAULT_MODEL)

        payload = {
            "text": text,
            "model_id": model_id,
            "voice_settings": {
                "stability": self._config.get("elevenlabs_stability", 0.45),
                "similarity_boost": self._config.get("elevenlabs_similarity", 0.85),
                "style": self._config.get("elevenlabs_style", 0.0),
                "use_speaker_boost": self._config.get("elevenlabs_speaker_boost", True),
            },
        }
        url = f"{_BASE_URL}/text-to-speech/{voice_id}/stream"
        self._stop.clear()
        resp = self._session.post(
            url,
            params={"output_format": output_format},
            json=payload,
            stream=True,
            timeout=45,
        )
        resp.raise_for_status()

        import numpy as np
        import sounddevice as sd

        pending = b""
        with sd.OutputStream(samplerate=sample_rate, channels=1, dtype="float32") as out:
            for chunk in resp.iter_content(chunk_size=1024):
                if self._stop.is_set():
                    return
                if not chunk:
                    continue
                pending += chunk
                playable_len = len(pending) - (len(pending) % 2)
                if playable_len <= 0:
                    continue
                pcm = np.frombuffer(pending[:playable_len], dtype=np.int16)
                pending = pending[playable_len:]
                audio = pcm.astype(np.float32) / 32768.0
                out.write(audio.reshape(-1, 1))
