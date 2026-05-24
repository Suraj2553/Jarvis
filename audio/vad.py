"""audio/vad.py — Layer 3: Silero VAD (Neural Voice Activity Detection).

Far superior to webrtcvad for Indian accents, Hinglish, and noisy environments.
Threshold tuned to 0.45 (below default 0.5) to avoid missing soft/accented speech.

Falls back to energy-based VAD if Silero cannot be loaded.
Thread-safe.
"""

import torch
import numpy as np
import logging
import threading

logger = logging.getLogger(__name__)

_model = None
_utils = None
_lock = threading.Lock()
_load_failed = False


def _load_model() -> None:
    global _model, _utils, _load_failed
    if _load_failed or _model is not None:
        return
    try:
        _model, _utils = torch.hub.load(
            repo_or_dir='snakers4/silero-vad',
            model='silero_vad',
            force_reload=False,
            onnx=False,
            verbose=False,
            trust_repo=True,
        )
        logger.info("[VAD] Silero VAD loaded successfully")
    except Exception as e:
        logger.error(f"[VAD] Silero VAD load failed: {e}")
        _load_failed = True


def preload() -> None:
    """Start Silero model loading in background. Call AFTER torch is pre-imported
    on the main thread (i.e. from _preload_native_extensions in main.py)."""
    if _model is None and not _load_failed:
        threading.Thread(target=_load_model, daemon=True,
                         name="SileroVAD-Load").start()

# NOTE: preload() is called explicitly from main._preload_native_extensions()
# so torch is fully initialised before the background thread starts.
# Do NOT call it here at import time — that races with ctranslate2 / other
# DLL initialisers and causes a 0xC0000005 ACCESS_VIOLATION.


def is_speech(
    audio: np.ndarray,
    sample_rate: int = 16000,
    threshold: float = 0.45,
) -> tuple[bool, float]:
    """Return (speech_detected, confidence 0-1).

    threshold=0.45 tuned for Indian accents — lower than the default 0.5
    to avoid missing soft-spoken or accented speech.
    """
    global _model, _utils, _load_failed

    if _load_failed or _model is None:
        # Energy-based fallback
        energy = float(np.sqrt(np.mean(audio.astype(np.float32) ** 2)))
        detected = energy > 0.01
        return detected, min(1.0, energy * 10)

    try:
        with _lock:
            (get_speech_timestamps, _, _, _, _) = _utils
            tensor = torch.FloatTensor(audio.astype(np.float32))
            timestamps = get_speech_timestamps(
                tensor,
                _model,
                threshold=threshold,
                sampling_rate=sample_rate,
                min_speech_duration_ms=200,
                min_silence_duration_ms=80,
            )
        confidence = min(1.0, len(timestamps) * 0.3 + (0.7 if timestamps else 0.0))
        return len(timestamps) > 0, confidence
    except Exception as e:
        logger.debug(f"[VAD] Error: {e}")
        energy = float(np.sqrt(np.mean(audio.astype(np.float32) ** 2)))
        return energy > 0.01, min(1.0, energy * 10)
