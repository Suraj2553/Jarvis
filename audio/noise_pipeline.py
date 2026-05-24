"""audio/noise_pipeline.py — Noise cancellation + VAD + echo-gate pipeline.

Pipeline order: Raw mic → rnnoise/noisereduce → WebRTC VAD gate → faster-whisper

Echo gating (Windows-only):
  A background thread opens a WASAPI loopback stream on the default speaker
  device and continuously measures its RMS. When system audio (JARVIS TTS,
  Teams/GMeet call audio, music, etc.) is above a threshold the mic chunk is
  flagged as non-speech, preventing JARVIS from responding to its own voice or
  to other people's voices coming through the speakers.
  This is the same principle that Teams/WhatsApp use — they just do it at the
  driver level; we do it in software with a 100 ms latency penalty.

On Windows use webrtcvad-wheels (not webrtcvad).
"""

import time
import threading
from typing import Optional
import numpy as np


# ── WASAPI loopback echo gate ─────────────────────────────────────── #

class _SystemAudioGate:
    """Measures RMS of system speaker output via WASAPI loopback.

    When the system is playing audio (JARVIS TTS, video-call remote audio,
    music) the gate returns is_playing()=True so the STT pipeline can
    suppress those chunks as non-speech.
    """

    _RMS_THRESHOLD = 0.018   # empirical — silence is ~0.002, speech ~0.05+

    def __init__(self):
        self._rms    = 0.0
        self._active = False   # True once loopback opened successfully
        self._stop   = threading.Event()
        threading.Thread(
            target=self._run, daemon=True, name="SysAudioGate"
        ).start()

    def _run(self) -> None:
        # COM must be initialised on every thread that touches WASAPI/soundcard.
        # 0x800401F0 = CO_E_NOTINITIALIZED — this call prevents that error.
        try:
            import pythoncom
            pythoncom.CoInitialize()
        except Exception:
            pass

        # Try WASAPI loopback via sounddevice first (requires sounddevice >= 0.4.5)
        try:
            import sounddevice as sd

            out_idx = sd.default.device[1]
            if isinstance(out_idx, (list, tuple)):
                out_idx = out_idx[0]
            if not isinstance(out_idx, int) or out_idx < 0:
                raise ValueError("no valid output device index")

            extra = sd.WasapiSettings(loopback=True)
            with sd.InputStream(
                device=out_idx,
                samplerate=16000,
                channels=1,
                dtype="float32",
                blocksize=1600,
                extra_settings=extra,
            ) as stream:
                self._active = True
                print("[EchoGate] WASAPI loopback active (sounddevice) — echo gating ON.")
                while not self._stop.is_set():
                    data, _ = stream.read(1600)
                    self._rms = float(np.sqrt(np.mean(data.astype(np.float32) ** 2)))
            return
        except Exception as exc:
            print(f"[EchoGate] sounddevice loopback failed ({exc}) — trying soundcard…")

        # Fallback: soundcard library
        try:
            import soundcard as _sc
            chunk = 1600   # 100 ms at 16 kHz
            speaker_name = str(_sc.default_speaker().name)
            loopback_mic = _sc.get_microphone(id=speaker_name, include_loopback=True)
            with loopback_mic.recorder(samplerate=16000, channels=1) as rec:
                self._active = True
                print("[EchoGate] soundcard loopback active — echo gating ON.")
                while not self._stop.is_set():
                    try:
                        data = rec.record(numframes=chunk)
                        self._rms = float(np.sqrt(np.mean(data ** 2)))
                    except Exception:
                        self._rms = 0.0
        except Exception as exc2:
            print(f"[EchoGate] Loopback unavailable ({exc2}) — echo gating OFF.")

    def is_playing(self) -> bool:
        """True when meaningful system audio is detected on the speakers."""
        return self._active and self._rms > self._RMS_THRESHOLD

    @property
    def rms(self) -> float:
        return self._rms

    def stop(self) -> None:
        self._stop.set()


# Module-level singleton — created lazily via start_echo_gate() so it does NOT
# spawn a background thread at import time (which races with DLL initialisation
# and causes 0xC0000005). main._preload_native_extensions() calls start_echo_gate()
# after all native DLLs are loaded on the main thread.
_sys_audio_gate: "_SystemAudioGate | None" = None


def start_echo_gate() -> None:
    """Create and start the echo gate. Call once from main thread after preload."""
    global _sys_audio_gate
    if _sys_audio_gate is None:
        _sys_audio_gate = _SystemAudioGate()

# ── rnnoise (preferred, < 1ms per chunk) ─────────────────────────── #
try:
    from rnnoise_wrapper import RNNoise as _RNNoise
    _rnnoise = _RNNoise()
    _HAS_RNNOISE = True
    print("[NoisePipeline] rnnoise loaded.")
except Exception:
    _rnnoise = None
    _HAS_RNNOISE = False

# ── noisereduce (fallback, ~5-15ms per call) ──────────────────────── #
try:
    import noisereduce as _nr
    _HAS_NOISEREDUCE = True
    print("[NoisePipeline] noisereduce loaded (rnnoise fallback).")
except Exception:
    _nr = None
    _HAS_NOISEREDUCE = False

# ── VAD: delegate to audio.vad (single shared thread-safe Silero instance) ── #
# Do NOT load a second Silero model here — two PyTorch instances on separate
# threads causes C++ memory corruption (0xC0000005 access violation).
try:
    from audio.vad import is_speech as _vad_is_speech
    _HAS_SILERO = True
    _HAS_VAD = True
    print("[NoisePipeline] VAD: using audio.vad singleton (thread-safe Silero).")
except Exception:
    _vad_is_speech = None
    _HAS_SILERO = False

    # ── WebRTC VAD (fallback — rule-based) ───────────────────────── #
    try:
        import webrtcvad as _webrtcvad
        _vad = _webrtcvad.Vad(2)   # 0=least, 3=most aggressive; 2=balanced
        _HAS_VAD = True
        print("[NoisePipeline] WebRTC VAD loaded (fallback).")
    except Exception:
        _vad = None
        _HAS_VAD = False

_SAMPLE_RATE = 16000
_CHUNK_MS = 30        # VAD requires 10, 20, or 30ms frames
_CHUNK_SAMPLES = (_SAMPLE_RATE * _CHUNK_MS) // 1000  # 480 samples for 30ms


def _time_it(fn, *args, **kwargs):
    t0 = time.perf_counter()
    result = fn(*args, **kwargs)
    elapsed_ms = (time.perf_counter() - t0) * 1000
    return result, elapsed_ms


class NoisePipeline:
    """Stateful noise cancellation + VAD pipeline.

    Usage:
        pipeline = NoisePipeline()
        pipeline.calibrate(ambient_audio)   # 2-second ambient sample
        clean, is_speech = pipeline.process(raw_chunk)
    """

    def __init__(self, config: Optional[dict] = None):
        self._config = config or {}
        self._noise_profile: Optional[np.ndarray] = None
        self._noise_cancellation = self._config.get("noise_cancellation", True)
        self._echo_cancellation = self._config.get("echo_cancellation", True)
        self._enabled = True

        # Performance stats
        self._process_times: list[float] = []
        self._skip_count = 0

    def calibrate(self, ambient_audio: np.ndarray) -> None:
        """Calibrate noise profile from ambient sample (typically 2-2.5 seconds)."""
        if ambient_audio is None or len(ambient_audio) == 0:
            return
        # Store ambient RMS for reference
        self._ambient_rms = float(np.sqrt(np.mean(ambient_audio.astype(np.float32) ** 2)))
        # Store noise profile for noisereduce
        if _HAS_NOISEREDUCE:
            self._noise_profile = ambient_audio.astype(np.float32)
        print(f"[NoisePipeline] Calibrated. Ambient RMS: {self._ambient_rms:.6f}")

    def process(self, audio_chunk: np.ndarray) -> tuple[np.ndarray, bool]:
        """Process one chunk. Returns (cleaned_audio, is_speech).

        If processing takes > 50ms: skip noise cancellation, return raw + VAD result.
        Echo gate: if speakers are playing (WASAPI loopback RMS > threshold),
        the chunk is always flagged non-speech — JARVIS won't respond to its own
        voice or to remote-call audio bleeding through the laptop speakers.
        """
        if not self._enabled or audio_chunk is None or len(audio_chunk) == 0:
            return audio_chunk, True

        t_start = time.perf_counter()

        # Step 1: Noise cancellation
        try:
            cleaned = self._denoise(audio_chunk)
        except Exception:
            cleaned = audio_chunk

        elapsed_ms = (time.perf_counter() - t_start) * 1000
        if elapsed_ms > 45:
            self._skip_count += 1
            cleaned = audio_chunk

        # Step 2: Echo gate — check speaker output BEFORE VAD
        if self._echo_cancellation and _sys_audio_gate.is_playing():
            if self._config.get("debug_audio_timing"):
                print(f"[EchoGate] speaker RMS={_sys_audio_gate.rms:.4f} — gating mic")
            return cleaned, False   # treat as non-speech; do NOT break silence counter

        # Step 3: VAD gate
        is_speech = self._vad_check(cleaned)

        total_ms = (time.perf_counter() - t_start) * 1000
        if self._config.get("debug_audio_timing") and total_ms > 20:
            print(f"[NoisePipeline] chunk: {total_ms:.1f}ms (denoise: {elapsed_ms:.1f}ms)")

        return cleaned, is_speech

    def _denoise(self, audio: np.ndarray) -> np.ndarray:
        """Apply noise cancellation. Tries rnnoise first, then noisereduce."""
        if not self._noise_cancellation:
            return audio

        # rnnoise operates on 480-sample frames at 16kHz (30ms)
        if _HAS_RNNOISE and _rnnoise:
            f32 = audio.astype(np.float32)
            # rnnoise needs exactly 480 samples per chunk; pad/split as needed
            output = np.zeros_like(f32)
            frame_size = 480
            i = 0
            while i + frame_size <= len(f32):
                frame = f32[i: i + frame_size]
                # rnnoise returns float values 0-1 (not audio) — use as vad
                try:
                    cleaned = _rnnoise.process_chunk(frame)
                    if cleaned is not None:
                        output[i: i + frame_size] = cleaned
                    else:
                        output[i: i + frame_size] = frame
                except Exception:
                    output[i: i + frame_size] = frame
                i += frame_size
            # Copy any remainder
            if i < len(f32):
                output[i:] = f32[i:]
            return output

        # noisereduce fallback — stationary=True is fast and safe on short chunks
        if _HAS_NOISEREDUCE and _nr and self._noise_profile is not None:
            f32 = audio.astype(np.float32)
            try:
                strength = self._config.get("noise_strength", 0.8)
                cleaned = _nr.reduce_noise(
                    y=f32,
                    sr=_SAMPLE_RATE,
                    y_noise=self._noise_profile,
                    stationary=True,
                    prop_decrease=strength,
                )
                return cleaned
            except Exception:
                pass

        return audio

    def _vad_check(self, audio: np.ndarray) -> bool:
        """Return True if audio contains speech.

        Priority: Silero via audio.vad singleton → WebRTC (rule-based) → energy.
        """
        # ── Silero VAD — delegate to audio.vad singleton ─────────── #
        if _HAS_SILERO and _vad_is_speech is not None:
            try:
                detected, _conf = _vad_is_speech(audio, _SAMPLE_RATE)
                return detected
            except Exception:
                pass  # fall through to WebRTC

        # ── WebRTC VAD (rule-based fallback) ──────────────────────── #
        if not _HAS_SILERO:
            try:
                import webrtcvad as _wv
                vad = _wv.Vad(2)
                f32 = audio.astype(np.float32)
                i16 = (f32 * 32767).clip(-32768, 32767).astype(np.int16)
                for i in range(0, len(i16) - _CHUNK_SAMPLES + 1, _CHUNK_SAMPLES):
                    frame = i16[i: i + _CHUNK_SAMPLES]
                    frame_bytes = frame.tobytes()
                    if len(frame_bytes) == _CHUNK_SAMPLES * 2:
                        if vad.is_speech(frame_bytes, _SAMPLE_RATE):
                            return True
                return False
            except Exception:
                pass

        # ── Energy-based fallback ─────────────────────────────────── #
        rms = float(np.sqrt(np.mean(audio.astype(np.float32) ** 2)))
        threshold = getattr(self, "_ambient_rms", 0.01) * 1.8
        return rms > threshold

    def enable(self) -> None:
        self._enabled = True

    def disable(self) -> None:
        self._enabled = False

    @property
    def stats(self) -> dict:
        return {
            "has_rnnoise":     _HAS_RNNOISE,
            "has_noisereduce": _HAS_NOISEREDUCE,
            "has_vad":         _HAS_VAD,
            "has_silero_vad":  _HAS_SILERO,
            "skip_count":      self._skip_count,
        }


# ── Global singleton ──────────────────────────────────────────────── #
_pipeline: Optional[NoisePipeline] = None


def get_pipeline(config: Optional[dict] = None) -> NoisePipeline:
    global _pipeline
    if _pipeline is None:
        _pipeline = NoisePipeline(config)
    return _pipeline
