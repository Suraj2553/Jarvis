"""audio/echo_cancel.py — Layer 1: Acoustic Echo Cancellation.

Captures loopback audio (what speakers are playing) and subtracts it
from mic input via an LMS adaptive filter so JARVIS doesn't hear itself.
Same principle as Microsoft Teams AEC, implemented in software.

Degrades gracefully: if soundcard loopback is unavailable, passes audio through unchanged.
"""

import numpy as np
import logging

logger = logging.getLogger(__name__)

_loopback_available = False


def _init_loopback() -> None:
    global _loopback_available
    try:
        import soundcard as sc
        sc.default_speaker()
        _loopback_available = True
        logger.info("[EchoCancel] Loopback audio available")
    except Exception as e:
        logger.warning(f"[EchoCancel] Loopback unavailable, echo cancel disabled: {e}")
        _loopback_available = False


_init_loopback()


def get_loopback_audio(num_frames: int, sample_rate: int = 16000) -> np.ndarray:
    """Capture what is currently playing through speakers."""
    if not _loopback_available:
        return np.zeros(num_frames, dtype=np.float32)
    try:
        import soundcard as sc
        speaker_name = str(sc.default_speaker().name)
        loopback = sc.get_microphone(id=speaker_name, include_loopback=True)
        with loopback.recorder(samplerate=sample_rate) as rec:
            data = rec.record(numframes=num_frames)
            return data[:, 0].astype(np.float32)
    except Exception as e:
        logger.debug(f"[EchoCancel] Loopback capture failed: {e}")
        return np.zeros(num_frames, dtype=np.float32)


def apply_echo_cancellation(mic_audio: np.ndarray, reference_audio: np.ndarray) -> np.ndarray:
    """LMS adaptive filter — removes echo from mic signal.

    mic_audio:       raw mic input (speech + echo mixed)
    reference_audio: what the speakers are playing (pure echo source)
    Returns:         cleaned mic signal with echo suppressed
    """
    if len(reference_audio) == 0 or not np.any(reference_audio):
        return mic_audio

    filter_length = min(512, len(mic_audio) // 4)
    if filter_length < 16:
        return mic_audio

    w = np.zeros(filter_length, dtype=np.float64)
    mu = 0.005  # conservative step size — stable on all hardware
    output = mic_audio.copy().astype(np.float64)

    ref = reference_audio.astype(np.float64)
    mic = mic_audio.astype(np.float64)

    for i in range(filter_length, len(mic)):
        if i >= len(ref):
            break
        x = ref[i - filter_length:i][::-1]
        y_hat = np.dot(w, x)
        e = mic[i] - y_hat
        norm = np.dot(x, x) + 1e-8  # normalized LMS for stability
        w += (2 * mu / norm) * e * x
        output[i] = e

    return output.astype(np.float32)
