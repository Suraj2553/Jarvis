"""audio/chatterbox_client.py — Chatterbox TTS (Resemble AI, May 2025).

Voice cloning from a short WAV sample (~5 seconds). Free, Apache 2.0, fully local.

Install:
    pip install chatterbox-tts sounddevice

Place your voice sample at: %APPDATA%\JARVIS\voice_sample.wav
Or configure via: config.json → "chatterbox_voice_sample": "C:/path/to/sample.wav"

Parameters (all optional in config.json):
    chatterbox_voice_sample  — path to WAV reference file (enables cloning)
    chatterbox_exaggeration  — 0.0–1.0, how strongly to apply the clone (default 0.4)
    chatterbox_cfg_weight    — classifier-free guidance strength (default 0.5)
    chatterbox_device        — "cpu" or "cuda" (default auto)
"""

from __future__ import annotations

import logging
import os
import threading
from typing import Optional

import numpy as np

logger = logging.getLogger(__name__)

_DEFAULT_VOICE_SAMPLE = os.path.join(
    os.environ.get("APPDATA", ""), "JARVIS", "voice_sample.wav"
)


class ChatterboxTTS:
    """Chatterbox TTS wrapper — speak() is blocking and respects stop()."""

    def __init__(self, config: dict):
        self._config       = config
        self._voice_sample = (
            config.get("chatterbox_voice_sample", "").strip()
            or (_DEFAULT_VOICE_SAMPLE if os.path.exists(_DEFAULT_VOICE_SAMPLE) else "")
        )
        self._exaggeration = float(config.get("chatterbox_exaggeration", 0.4))
        self._cfg_weight   = float(config.get("chatterbox_cfg_weight", 0.5))
        self._stop         = threading.Event()
        self._model        = None
        self._sample_rate  = 24000
        self._ready        = threading.Event()
        self._startup_delay = 25  # seconds — loads after Kokoro to avoid simultaneous C-ext init
        threading.Thread(target=self._load, daemon=True, name="CB-Load").start()

    # ------------------------------------------------------------------ #

    def _load(self) -> None:
        import time
        time.sleep(self._startup_delay)
        try:
            import torch
            device = self._config.get("chatterbox_device", "")
            if not device:
                device = "cuda" if torch.cuda.is_available() else "cpu"

            from chatterbox.tts import ChatterboxTTS as _CB
            logger.info("[ChatterboxTTS] Loading model on %s…", device)
            model = _CB.from_pretrained(device=device)
            self._model       = model
            self._sample_rate = int(model.sr)
            self._ready.set()

            sample_info = f"  voice_sample={self._voice_sample!r}" if self._voice_sample else "  (no voice sample — using default voice)"
            logger.info("[ChatterboxTTS] Ready  sr=%d%s", self._sample_rate, sample_info)
            print(f"[ChatterboxTTS] Ready — sr={self._sample_rate}{sample_info}")
        except Exception as e:
            logger.error("[ChatterboxTTS] Load failed: %s", e)
            print(f"[ChatterboxTTS] Load failed: {e}")

    # ------------------------------------------------------------------ #

    def speak(self, text: str) -> None:
        """Generate and play text. Blocks until done or stop() called."""
        if not self._ready.wait(timeout=60):
            raise RuntimeError("Chatterbox model not ready after 60 s")

        kwargs: dict = {
            "exaggeration": self._exaggeration,
            "cfg_weight":   self._cfg_weight,
        }
        if self._voice_sample and os.path.exists(self._voice_sample):
            kwargs["audio_prompt_path"] = self._voice_sample

        self._stop.clear()
        wav = self._model.generate(text, **kwargs)

        # wav is a torch tensor (1, samples) — convert to float32 numpy
        try:
            audio = wav.squeeze().cpu().numpy().astype(np.float32)
        except Exception:
            audio = np.array(wav, dtype=np.float32).flatten()

        if audio.ndim == 1:
            audio = audio.reshape(-1, 1)

        import sounddevice as sd
        sr = self._sample_rate
        chunk_size = int(sr * 0.08)

        with sd.OutputStream(samplerate=sr, channels=1, dtype="float32") as stream:
            offset = 0
            while offset < len(audio):
                if self._stop.is_set():
                    return
                chunk = audio[offset: offset + chunk_size]
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
        return self._model is not None

    @property
    def voice_sample_path(self) -> str:
        return self._voice_sample
