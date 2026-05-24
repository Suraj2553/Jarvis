"""audio/pipeline.py — Three-layer audio pipeline entry point.

Call process_mic_chunk() for every 30ms mic chunk.
Each layer degrades gracefully — never crashes the caller.
Target: complete in < 50ms on Ryzen 5 / equivalent CPU.

Pipeline order:
  Raw mic → echo_cancel → noise_suppress → VAD → (cleaned, speech_flag, confidence)
"""

import numpy as np
import time
import logging
from audio.echo_cancel import get_loopback_audio, apply_echo_cancellation
from audio.noise_suppress import suppress_noise
from audio.vad import is_speech

logger = logging.getLogger(__name__)

_last_latency_ms = 0.0


def process_mic_chunk(
    raw_audio: np.ndarray,
    sample_rate: int = 16000,
    echo_cancel: bool = True,
    noise_suppress: bool = True,
) -> tuple[np.ndarray, bool, float]:
    """Full 3-layer pipeline for a single audio chunk.

    Args:
        raw_audio:      float32 numpy array from mic
        sample_rate:    audio sample rate (default 16000)
        echo_cancel:    apply LMS acoustic echo cancellation
        noise_suppress: apply RNNoise neural noise suppression

    Returns:
        (processed_audio, speech_detected, vad_confidence)
    """
    global _last_latency_ms
    t0 = time.monotonic()

    audio = raw_audio.astype(np.float32)

    if echo_cancel:
        reference = get_loopback_audio(len(audio), sample_rate)
        audio = apply_echo_cancellation(audio, reference)

    if noise_suppress:
        audio = suppress_noise(audio, sample_rate)

    speech, confidence = is_speech(audio, sample_rate)

    elapsed = (time.monotonic() - t0) * 1000
    _last_latency_ms = elapsed

    if elapsed > 50:
        logger.warning(f"[Pipeline] Chunk took {elapsed:.1f}ms — above 50ms target")

    return audio, speech, confidence


def get_pipeline_latency() -> float:
    """Returns last pipeline latency in milliseconds."""
    return _last_latency_ms
