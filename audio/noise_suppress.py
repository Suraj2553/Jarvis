"""audio/noise_suppress.py — Layer 2: Neural Noise Suppression.

RNNoise-based neural noise suppression removes background noise
(fans, AC, keyboard, street noise) in real-time.

Degrades gracefully to pass-through if rnnoise is unavailable.
"""

import numpy as np
import logging

logger = logging.getLogger(__name__)

_rnnoise = None
_rnnoise_failed = False


def _get_denoiser():
    global _rnnoise, _rnnoise_failed
    if _rnnoise_failed:
        return None
    if _rnnoise is None:
        try:
            from rnnoise_wrapper import RNNoise
            _rnnoise = RNNoise()
            logger.info("[NoiseSuppressor] RNNoise loaded")
        except Exception as e:
            logger.warning(f"[NoiseSuppressor] RNNoise unavailable, using pass-through: {e}")
            _rnnoise_failed = True
            return None
    return _rnnoise


def suppress_noise(audio: np.ndarray, sample_rate: int = 16000) -> np.ndarray:
    """Apply RNNoise neural noise suppression.

    RNNoise requires 48kHz input — resamples internally if needed.
    Returns denoised audio at the original sample_rate.
    Passes through unchanged if RNNoise is unavailable.
    """
    denoiser = _get_denoiser()
    if denoiser is None:
        return audio

    try:
        from scipy import signal as scipy_signal

        audio_f32 = audio.astype(np.float32)

        if sample_rate != 48000:
            audio_48k = scipy_signal.resample_poly(
                audio_f32, up=48000, down=sample_rate
            ).astype(np.float32)
        else:
            audio_48k = audio_f32

        frame_size = 480  # 10ms at 48kHz — RNNoise requirement
        output_48k = np.zeros_like(audio_48k)
        for i in range(0, len(audio_48k) - frame_size, frame_size):
            frame = audio_48k[i:i + frame_size]
            try:
                processed = denoiser.process_chunk(frame)
                if processed is not None:
                    output_48k[i:i + frame_size] = processed
                else:
                    output_48k[i:i + frame_size] = frame
            except Exception:
                output_48k[i:i + frame_size] = frame

        if sample_rate != 48000:
            output = scipy_signal.resample_poly(
                output_48k, up=sample_rate, down=48000
            ).astype(np.float32)
        else:
            output = output_48k

        return output

    except Exception as e:
        logger.debug(f"[NoiseSuppressor] Processing error, pass-through: {e}")
        return audio
