"""audio/kokoro_client.py — Kokoro-82M ONNX local TTS.

Free, fully local, ~80-200ms first audio, 54 voices.
Best English voice for JARVIS: bm_george (British male).

Install:
    pip install kokoro-onnx huggingface_hub sounddevice

Models auto-downloaded from HuggingFace on first use (~300 MB total).
Cached at: %USERPROFILE%\.cache\huggingface\hub\
"""

from __future__ import annotations

import logging
import os
import threading
import time
from typing import Optional

import numpy as np

logger = logging.getLogger(__name__)

_RELEASE_BASE = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0"
_MODEL_FILENAME  = "kokoro-v1.0.onnx"
_VOICES_FILENAME = "voices-v1.0.bin"
_CACHE_DIR = os.path.join(os.path.expandvars("%USERPROFILE%"), ".cache", "kokoro-onnx")

# Map voice code → phoneme language for Kokoro
_VOICE_LANG: dict[str, str] = {
    "bm_george":    "en-gb",   # British male   — recommended for JARVIS
    "bm_lewis":     "en-gb",
    "bf_emma":      "en-gb",   # British female
    "bf_isabella":  "en-gb",
    "am_adam":      "en-us",
    "am_michael":   "en-us",
    "af_bella":     "en-us",
    "af_sarah":     "en-us",
    "af_sky":       "en-us",
}
_DEFAULT_VOICE = "bm_george"


class KokoroTTS:
    """Kokoro-82M ONNX wrapper — speak() is blocking and respects stop()."""

    def __init__(self, config: dict):
        self._config      = config
        self._voice       = config.get("kokoro_voice", _DEFAULT_VOICE)
        self._speed       = float(config.get("kokoro_speed", 1.05))
        self._stop        = threading.Event()
        self._kokoro      = None
        self._sample_rate = 24000
        self._load_lock   = threading.Lock()
        self._ready       = threading.Event()
        self._startup_delay = 15  # seconds to wait before loading (avoids race with PyTorch init)
        threading.Thread(target=self._load, daemon=True, name="Kokoro-Load").start()

    # ------------------------------------------------------------------ #

    def _ensure_model_files(self) -> tuple[str, str]:
        """Download model + voices from GitHub releases if not cached. Returns (model_path, voices_path)."""
        import urllib.request
        os.makedirs(_CACHE_DIR, exist_ok=True)
        model_path  = os.path.join(_CACHE_DIR, _MODEL_FILENAME)
        voices_path = os.path.join(_CACHE_DIR, _VOICES_FILENAME)

        for path, filename in ((model_path, _MODEL_FILENAME), (voices_path, _VOICES_FILENAME)):
            if not os.path.exists(path):
                url = f"{_RELEASE_BASE}/{filename}"
                size_mb = 310 if "onnx" in filename else 5
                print(f"[KokoroTTS] Downloading {filename} (~{size_mb} MB)…")
                urllib.request.urlretrieve(url, path)
                print(f"[KokoroTTS] {filename} saved to {path}")

        return model_path, voices_path

    def _load(self) -> None:
        import time
        time.sleep(self._startup_delay)
        with self._load_lock:
            try:
                from kokoro_onnx import Kokoro

                logger.info("[KokoroTTS] Loading models…")
                model_path, voices_path = self._ensure_model_files()
                k = Kokoro(model_path, voices_path)
                self._kokoro = k
                self._ready.set()
                logger.info("[KokoroTTS] Ready (voice=%s sr=%d)", self._voice, self._sample_rate)
                print(f"[KokoroTTS] Ready — voice={self._voice}  sr={self._sample_rate}")
            except Exception as e:
                logger.error("[KokoroTTS] Load failed: %s", e)
                print(f"[KokoroTTS] Load failed: {e}")

    # ------------------------------------------------------------------ #

    def speak(self, text: str) -> None:
        """Generate and play text. Blocks until done or stop() called."""
        if not self._ready.wait(timeout=30):
            raise RuntimeError("Kokoro model not ready after 30 s")

        lang = _VOICE_LANG.get(self._voice, "en-gb")
        samples, sr = self._kokoro.create(
            text, voice=self._voice, speed=self._speed, lang=lang
        )
        samples = samples.astype(np.float32)
        if samples.ndim == 1:
            samples = samples.reshape(-1, 1)
        elif samples.shape[0] == 1:
            samples = samples.T

        import sounddevice as sd
        self._stop.clear()
        chunk_size = int(sr * 0.08)   # 80ms chunks — smooth without overhead

        with sd.OutputStream(samplerate=sr, channels=1, dtype="float32") as stream:
            offset = 0
            while offset < len(samples):
                if self._stop.is_set():
                    return
                chunk = samples[offset: offset + chunk_size]
                stream.write(chunk)
                offset += chunk_size

    def stop(self) -> None:
        self._stop.set()
        try:
            import sounddevice as sd
            sd.stop()
        except Exception:
            pass

    def reset(self) -> None:
        self._stop.clear()

    def is_ready(self) -> bool:
        return self._kokoro is not None
