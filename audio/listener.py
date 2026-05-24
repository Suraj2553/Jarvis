"""audio/listener.py — Enhanced clap listener  v3.0

Fix for jarvis_err.log: "data discontinuity in recording"
──────────────────────────────────────────────────────────
The original _loopback_loop() opened and closed a soundcard recorder
context every 100 ms.  MediaFoundation (Windows audio engine) treats
each open/close as a device reconnect — hence the stream of
SoundcardRuntimeWarning messages — and the resulting buffer
discontinuities are audible as pops and robotic artefacts in both
the loopback feed AND the microphone capture path (they share the
same audio driver).

Fix: open the loopback recorder ONCE and keep it open.  Read small
chunks in a tight loop inside the same context.  The recorder is only
closed on stop().

Everything else (clap biometrics, PTT, media detection) is unchanged.
"""

import threading
import time
from typing import Callable, Optional

import numpy as np
import sounddevice as sd

try:
    import soundcard as _sc
    _HAS_SOUNDCARD = True
except Exception:
    _sc = None
    _HAS_SOUNDCARD = False

try:
    import keyboard as _keyboard
    _HAS_KEYBOARD = True
except Exception:
    _keyboard = None
    _HAS_KEYBOARD = False

try:
    import psutil as _psutil
    _HAS_PSUTIL = True
except Exception:
    _psutil = None
    _HAS_PSUTIL = False

try:
    from pycaw.pycaw import AudioUtilities as _AU
    _HAS_PYCAW = True
except Exception:
    _AU = None
    _HAS_PYCAW = False

_MEDIA_PROCESSES = {
    "chrome.exe", "msedge.exe", "firefox.exe",
    "vlc.exe", "wmplayer.exe", "spotify.exe",
    "obs64.exe", "streamlabs.exe",
    "discord.exe", "teams.exe", "zoom.exe",
}

_SAMPLE_RATE    = 16_000
_CHUNK_FRAMES   = 512       # 32 ms at 16 kHz
_CLAP_WINDOW_S  = 0.70
_SPIKE_COOLDOWN = 0.10

_MIN_INTER_CLAP_S          = 0.20
_MAX_ATTACK_S              = 0.015
_MAX_SPIKE_DUR_S           = 0.100
_MIN_SILENCE_BEFORE_S      = 0.15
_AMP_THRESHOLD_MULTIPLIER  = 2.5
_NORMAL_CONFIDENCE         = 0.65
_MEDIA_CONFIDENCE          = 0.85

# Loopback chunk: 50 ms — small enough to stay reactive,
# large enough to avoid MediaFoundation buffer churn
_LOOPBACK_CHUNK_S = 0.05


class EnhancedClapListener:
    """Detects double-claps with biometric validation and echo cancellation.

    v3.0: persistent loopback context eliminates data discontinuity warnings.
    """

    def __init__(self, callback: Callable, config: Optional[dict] = None):
        self._callback = callback
        self._config   = config or {}
        self._running  = False
        self._paused   = False
        self._lock     = threading.Lock()

        # Reuse the WASAPI loopback gate from noise_pipeline (if available).
        # This gates clap detection when speakers are playing — same fix as STT.
        try:
            from audio.noise_pipeline import _sys_audio_gate
            self._sys_gate = _sys_audio_gate
        except Exception:
            self._sys_gate = None

        self._last_spike_time  = 0.0
        self._first_clap_time  = 0.0
        self._waiting_second   = False
        self._last_pause_time  = 0.0
        self._pause_gen        = 0   # increments on each pause(); resume() checks it

        self.ambient_rms  = 0.02
        self.current_rms  = 0.0
        self._rolling_rms = 0.02
        self._rms_history: list[float] = []

        self._loopback_rms = 0.0
        self._media_mode   = False
        self._ptt_active   = False

        self._stream:             Optional[sd.InputStream] = None
        self._loopback_thread:    Optional[threading.Thread] = None
        self._media_check_thread: Optional[threading.Thread] = None
        self._recal_thread:       Optional[threading.Thread] = None

    # ── Calibration ────────────────────────────────────────────────── #

    def calibrate(self) -> None:
        print("[ClapListener] Calibrating ambient noise…")
        try:
            samples = sd.rec(
                int(2.5 * _SAMPLE_RATE),
                samplerate=_SAMPLE_RATE, channels=1, dtype="float32",
            )
            sd.wait()
            audio = samples.flatten()
            self.ambient_rms  = float(np.sqrt(np.mean(audio ** 2))) + 0.001
            self._rolling_rms = self.ambient_rms
            print(f"[ClapListener] Ambient RMS: {self.ambient_rms:.6f}")
            try:
                from audio.noise_pipeline import get_pipeline
                get_pipeline(self._config).calibrate(audio)
            except Exception:
                pass
        except Exception as e:
            print(f"[ClapListener] Calibration error: {e}")

    def _recalibrate_loop(self) -> None:
        while self._running:
            time.sleep(1800)
            if self._running and not self._paused:
                self.calibrate()

    # ── Start / Stop / Pause ───────────────────────────────────────── #

    def start(self) -> None:
        self._running = True
        self._stream  = sd.InputStream(
            samplerate=_SAMPLE_RATE, channels=1, dtype="float32",
            blocksize=_CHUNK_FRAMES, callback=self._audio_callback,
        )
        self._stream.start()

        if _HAS_SOUNDCARD:
            self._loopback_thread = threading.Thread(
                target=self._loopback_loop, daemon=True, name="ClapLoopback"
            )
            self._loopback_thread.start()

        self._media_check_thread = threading.Thread(
            target=self._media_check_loop, daemon=True, name="ClapMediaCheck"
        )
        self._media_check_thread.start()

        self._recal_thread = threading.Thread(
            target=self._recalibrate_loop, daemon=True, name="ClapRecal"
        )
        self._recal_thread.start()

        if _HAS_KEYBOARD:
            ptt_key = self._config.get("ptt_hotkey", "ctrl+space")
            try:
                _keyboard.add_hotkey(ptt_key, self._on_ptt_press)
            except Exception:
                pass

        print("[ClapListener] Enhanced clap listener started.")

    def stop(self) -> None:
        self._running = False
        if self._stream:
            try:
                self._stream.stop()
                self._stream.close()
            except Exception:
                pass

    def pause(self) -> None:
        self._paused          = True
        self._pause_gen      += 1
        self._last_pause_time = time.monotonic()

    def resume(self) -> None:
        gen = self._pause_gen   # snapshot — stale threads won't match after next pause()
        def _resume_delayed():
            time.sleep(1.5)
            if self._pause_gen == gen:   # only unpause if no new pause() happened since
                self._paused          = False
                self._waiting_second  = False
                self._first_clap_time = 0.0
        threading.Thread(target=_resume_delayed, daemon=True).start()

    # ── Audio callback ─────────────────────────────────────────────── #

    def _audio_callback(self, indata, frames, time_info, status) -> None:
        audio = indata[:, 0]
        rms   = float(np.sqrt(np.mean(audio ** 2)))
        peak  = float(np.max(np.abs(audio)))
        self.current_rms = rms

        self._rms_history.append(rms)
        if len(self._rms_history) > 156:
            self._rms_history.pop(0)
        self._rolling_rms = (
            float(np.mean(self._rms_history)) if self._rms_history else rms
        )

        if self._paused:
            return

        # Echo gate — if WASAPI loopback shows speakers are active, skip entirely
        if self._sys_gate is not None and self._sys_gate.is_playing():
            return

        threshold = max(
            self._rolling_rms * _AMP_THRESHOLD_MULTIPLIER,
            self.ambient_rms  * self._config.get("mic_sensitivity", 3.5),
        )

        now = time.monotonic()
        if peak < threshold:
            return
        if now - self._last_spike_time < _SPIKE_COOLDOWN:
            return

        confidence = self._score_clap(audio, peak, rms)
        required   = _MEDIA_CONFIDENCE if self._media_mode else _NORMAL_CONFIDENCE

        if self._loopback_rms > 0.12:
            return
        if self._loopback_rms > 0.06:
            required = _MEDIA_CONFIDENCE

        if confidence < required:
            return

        self._last_spike_time = now

        if not self._waiting_second:
            self._first_clap_time = now
            self._waiting_second  = True
        else:
            gap = now - self._first_clap_time
            if _MIN_INTER_CLAP_S <= gap <= _CLAP_WINDOW_S:
                self._waiting_second  = False
                self._first_clap_time = 0.0
                self._last_spike_time = now + 0.5
                print(f"[ClapListener] Double clap! Confidence: {confidence:.2f}")
                threading.Thread(
                    target=self._callback, daemon=True, name="ClapCallback"
                ).start()
            elif gap > _CLAP_WINDOW_S:
                self._first_clap_time = now

    def _score_clap(self, audio: np.ndarray, peak: float, rms: float) -> float:
        score = 0.0

        amp_ratio = peak / max(self._rolling_rms, 0.001)
        score += 0.25 if amp_ratio > 5.0 else (0.15 if amp_ratio > 2.5 else 0)

        try:
            abs_audio = np.abs(audio)
            peak_idx  = int(np.argmax(abs_audio))
            start_t   = peak * 0.1
            rise_start = peak_idx
            for i in range(peak_idx, max(0, peak_idx - 40), -1):
                if abs_audio[i] < start_t:
                    rise_start = i
                    break
            attack_ms = (peak_idx - rise_start) / _SAMPLE_RATE * 1000
            score += 0.25 if attack_ms < 8 else (0.15 if attack_ms < 15 else 0)
        except Exception:
            pass

        try:
            thresh = peak * 0.15
            above  = np.where(np.abs(audio) > thresh)[0]
            if len(above) > 0:
                dur_ms = (above[-1] - above[0]) / _SAMPLE_RATE * 1000
                score += 0.25 if dur_ms < 50 else (0.15 if dur_ms < 100 else 0)
        except Exception:
            pass

        try:
            from numpy.fft import rfft
            spec  = np.abs(rfft(audio.astype(np.float32)))
            n     = len(spec)
            low   = np.mean(spec[:n // 4])
            mid   = np.mean(spec[n // 4: n // 2])
            high  = np.mean(spec[n // 2:])
            if low > 0 and mid > 0 and high > 0:
                if max(low, mid, high) / (min(low, mid, high) + 1e-10) < 10:
                    score += 0.25
        except Exception:
            score += 0.15

        return min(score, 1.0)

    # ── Loopback loop (FIXED: persistent context, no per-chunk open/close) #

    def _loopback_loop(self) -> None:
        """Read system audio output in a persistent recorder context."""
        if not _HAS_SOUNDCARD or not _sc:
            return
        # COM must be initialised per thread for WASAPI (0x800401F0 otherwise)
        try:
            import pythoncom
            pythoncom.CoInitialize()
        except Exception:
            pass
        chunk_frames = int(_LOOPBACK_CHUNK_S * _SAMPLE_RATE)
        while self._running:
            try:
                speaker_name = str(_sc.default_speaker().name)
                loopback = _sc.get_microphone(
                    id=speaker_name, include_loopback=True
                )
                # Open context ONCE — keep it alive for the session
                with loopback.recorder(
                    samplerate=_SAMPLE_RATE, channels=1
                ) as rec:
                    print("[ClapListener] Loopback recorder opened (persistent).")
                    while self._running:
                        try:
                            samples = rec.record(numframes=chunk_frames)
                            rms = float(np.sqrt(np.mean(samples ** 2)))
                            self._loopback_rms = rms
                        except Exception as inner:
                            # Short sleep then retry within the same context
                            time.sleep(0.1)
            except Exception as outer:
                # Context failed (device change, etc.) — wait and reopen
                print(f"[ClapListener] Loopback context error: {outer} — retrying in 5s")
                time.sleep(5)

    # ── Media detection ────────────────────────────────────────────── #

    def _media_check_loop(self) -> None:
        while self._running:
            try:
                self._media_mode = self._detect_media_playing()
            except Exception:
                pass
            time.sleep(2)

    def _detect_media_playing(self) -> bool:
        if _HAS_PYCAW and _AU:
            try:
                import pythoncom
                pythoncom.CoInitialize()
                for s in _AU.GetAllSessions():
                    proc = s.Process
                    if proc and proc.name().lower() in _MEDIA_PROCESSES:
                        from pycaw.pycaw import ISimpleAudioVolume
                        try:
                            vol = s._ctl.QueryInterface(ISimpleAudioVolume)
                            if vol.GetMasterVolume() > 0.01:
                                return True
                        except Exception:
                            pass
            except Exception:
                pass
        if _HAS_PSUTIL and _psutil:
            for proc in _psutil.process_iter(["name"]):
                try:
                    if proc.info["name"].lower() in _MEDIA_PROCESSES:
                        return True
                except Exception:
                    pass
        return False

    # ── PTT ────────────────────────────────────────────────────────── #

    def _on_ptt_press(self) -> None:
        if not self._paused:
            self._ptt_active = True
            threading.Thread(
                target=self._callback, daemon=True, name="PTTCallback"
            ).start()


class ClapListener(EnhancedClapListener):
    """Drop-in replacement — keeps main.py import unchanged."""
    pass
