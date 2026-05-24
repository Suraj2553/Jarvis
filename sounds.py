"""JARVIS sound design — all tones synthesized with numpy + sounddevice.

No audio files needed. Every sound is a pure waveform generated at runtime.
Each state transition has a unique signature:
  activation   — ascending two-tone chime (440→880Hz)
  listening    — soft ping + white noise burst
  thinking     — subtle 2Hz rhythmic pulse (nearly subliminal)
  response     — descending resolve (880→440Hz)
  error        — low double buzz (200Hz x2)
  notification — single soft bell (660Hz)
"""

import threading
import numpy as np

try:
    import sounddevice as sd
    _SD = True
except ImportError:
    _SD = False

_SR = 44_100
_VOL = 0.30   # master volume for UI sounds (separate from TTS)

# Global lock — sounddevice is not thread-safe; only one tone plays at a time
_AUDIO_LOCK = threading.Lock()


def _tone(freq: float, duration: float, vol: float = _VOL,
          fade_ms: float = 10.0) -> np.ndarray:
    t = np.linspace(0, duration, int(_SR * duration), endpoint=False)
    wave = vol * np.sin(2 * np.pi * freq * t)
    fade = int(_SR * fade_ms / 1000)
    if fade > 0 and len(wave) > fade * 2:
        wave[:fade]  *= np.linspace(0, 1, fade)
        wave[-fade:] *= np.linspace(1, 0, fade)
    return wave.astype(np.float32)


def _play(wave: np.ndarray) -> None:
    if not _SD:
        return
    # Non-blocking try: if lock is already held, drop this sound rather than queue
    if not _AUDIO_LOCK.acquire(blocking=False):
        return
    try:
        sd.play(wave, _SR)
        sd.wait()
    except Exception:
        pass
    finally:
        _AUDIO_LOCK.release()


def _play_async(wave: np.ndarray) -> None:
    threading.Thread(target=_play, args=(wave,), daemon=True).start()


# ── Public sound effects ──────────────────────────────────────────── #

def play_activation() -> None:
    """Rising two-tone chime: 440Hz → 880Hz, 80ms each."""
    wave = np.concatenate([_tone(440, 0.08), _tone(880, 0.08)])
    _play_async(wave)


def play_listening_start() -> None:
    """Soft ping at 1100Hz — short and clean."""
    _play_async(_tone(1100, 0.06, vol=_VOL * 0.8))


def play_response_start() -> None:
    """Descending resolve: 880Hz → 440Hz, signals JARVIS is speaking."""
    wave = np.concatenate([_tone(880, 0.06), _tone(440, 0.08)])
    _play_async(wave)


def play_error() -> None:
    """Low double buzz at 200Hz — signals failure."""
    buzz = _tone(200, 0.15, vol=_VOL * 0.9)
    silence = np.zeros(int(_SR * 0.08), dtype=np.float32)
    wave = np.concatenate([buzz, silence, buzz])
    _play_async(wave)


def play_notification() -> None:
    """Single soft bell at 660Hz — proactive alert."""
    _play_async(_tone(660, 0.12, vol=_VOL * 0.6))


def play_processing_pulse() -> None:
    """Nearly-inaudible 2Hz pulse — runs during thinking state."""
    # Very low volume — more felt than heard
    _play_async(_tone(80, 0.08, vol=0.04))
