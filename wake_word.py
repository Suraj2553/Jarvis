"""Always-on voice wake-word listener.

How it works
------------
1. A VAD (energy-based) watches the mic continuously in small chunks.
2. When speech energy is detected, it accumulates audio into a segment.
3. When the segment ends (silence for 0.8 s or > 4 s total), it
   transcribes the segment with the tiny.en Whisper model (~40 MB, fast).
4. If any known wake phrase is found → fires callback(command_text).
   command_text is whatever the user said AFTER the wake phrase, so the
   main pipeline can skip re-listening if the command is already known.

This runs in its own daemon thread and does NOT block the clap listener.
Both activation paths work simultaneously.
"""

import threading
import time

import numpy as np
import sounddevice as sd

try:
    import audio.noise_pipeline as _np_mod
except Exception:
    _np_mod = None


def _echo_gate():
    """Return the live echo gate instance (created lazily — may be None)."""
    return _np_mod._sys_audio_gate if _np_mod is not None else None

# Pre-import on main thread to avoid COM apartment violation (0xC0000005).
try:
    from faster_whisper import WhisperModel as _WhisperModel
except ImportError:
    _WhisperModel = None  # type: ignore

# Phrases that activate JARVIS — includes common mishears of "JARVIS"
WAKE_PHRASES = {
    "hey jarvis", "jarvis", "hey travis", "hey paris",
    "ok jarvis", "okay jarvis", "hi jarvis", "hello jarvis",
    # Hindi/JARVIS equivalents
    "ey jarvis", "jarvis sun",
    # Friday persona (Hindi mode)
    "hey friday", "friday", "aye friday", "friday sun", "friday suno",
    "hi friday", "hello friday", "ok friday",
}


class WakeWordListener:
    # ── tunables ─────────────────────────────────────────────────────── #
    _SR          = 16_000
    _CHUNK_MS    = 80           # ms per chunk
    _MIN_SEG_S   = 0.4          # ignore segments shorter than this
    _MAX_SEG_S   = 5.0          # stop recording after this many seconds
    _SILENCE_S   = 0.8          # end segment after this many seconds of quiet
    _VAD_MULT    = 1.8          # speech = ambient_rms * this multiplier

    def __init__(self, callback, ambient_rms: float, config: dict):
        # callback(command_text: str) — text after wake phrase, may be empty
        self._callback     = callback
        self._ambient_rms  = max(ambient_rms, 0.005)
        self._config       = config
        self._vad_thresh   = self._ambient_rms * self._VAD_MULT
        self._running      = False
        self._paused       = False
        self._pause_gen    = 0
        self._model        = None
        self._model_lock   = threading.Lock()
        self._thread: threading.Thread | None = None

        print(f"[WakeWord] VAD threshold: {self._vad_thresh:.5f}")

    # ------------------------------------------------------------------ #
    #  Model (lazy load)                                                   #
    # ------------------------------------------------------------------ #

    def _load_model(self) -> None:
        if self._model is not None:
            return
        # COM per-thread init required on Windows.
        try:
            import pythoncom
            pythoncom.CoInitializeEx(pythoncom.COINIT_MULTITHREADED)
        except Exception:
            pass
        with self._model_lock:
            if self._model is not None:
                return
            if _WhisperModel is None:
                return
            print("[WakeWord] Loading tiny.en Whisper model for wake-word detection…")
            self._model = _WhisperModel("tiny.en", device="cpu", compute_type="int8")
            print("[WakeWord] Wake-word model ready.")

    def _transcribe(self, audio: np.ndarray) -> str:
        try:
            segs, _ = self._model.transcribe(audio, language="en", beam_size=1)
            return " ".join(s.text for s in segs).lower().strip()
        except Exception:
            return ""

    # ------------------------------------------------------------------ #
    #  Lifecycle                                                           #
    # ------------------------------------------------------------------ #

    def start(self) -> None:
        # Load model in background so startup is snappy
        threading.Thread(target=self._load_model, daemon=True).start()
        self._running = True
        self._thread  = threading.Thread(
            target=self._run, daemon=True, name="WakeWordListener"
        )
        self._thread.start()

    def stop(self) -> None:
        self._running = False

    def pause(self) -> None:
        self._paused    = True
        self._pause_gen += 1

    def resume(self) -> None:
        gen = self._pause_gen
        def _resume_delayed():
            time.sleep(1.5)
            if self._pause_gen == gen:
                self._paused = False
        threading.Thread(target=_resume_delayed, daemon=True).start()

    # ------------------------------------------------------------------ #
    #  Main loop                                                           #
    # ------------------------------------------------------------------ #

    def _run(self) -> None:
        # Initialise COM for this thread (required for sounddevice / pycaw on Windows).
        try:
            import pythoncom
            pythoncom.CoInitializeEx(pythoncom.COINIT_MULTITHREADED)
            _com_inited = True
        except Exception:
            _com_inited = False

        chunk_frames = int(self._SR * self._CHUNK_MS / 1000)
        max_chunks   = int(self._MAX_SEG_S * 1000 / self._CHUNK_MS)
        silent_need  = int(self._SILENCE_S  * 1000 / self._CHUNK_MS)

        while self._running:
            try:
                with sd.InputStream(
                    samplerate=self._SR,
                    channels=1,
                    dtype="float32",
                    blocksize=chunk_frames,
                ) as stream:
                    while self._running:
                        try:
                            # Drain without processing when paused or model not ready
                            if self._paused or self._model is None:
                                stream.read(chunk_frames)
                                time.sleep(0.02)
                                continue

                            chunk, _ = stream.read(chunk_frames)

                            # Echo gate: speakers are playing TTS/media — discard
                            if (lambda g: g is not None and g.is_playing())(_echo_gate()):
                                continue

                            rms = float(np.sqrt(np.mean(chunk ** 2)))

                            if rms < self._vad_thresh:
                                continue

                            frames = [chunk.copy()]
                            silent_count = 0
                            echo_abort = False

                            for _ in range(max_chunks - 1):
                                if self._paused:
                                    echo_abort = True
                                    break
                                if (lambda g: g is not None and g.is_playing())(_echo_gate()):
                                    echo_abort = True
                                    break
                                chunk, _ = stream.read(chunk_frames)
                                frames.append(chunk.copy())
                                rms = float(np.sqrt(np.mean(chunk ** 2)))

                                if rms < self._vad_thresh:
                                    silent_count += 1
                                    if silent_count >= silent_need:
                                        break
                                else:
                                    silent_count = 0

                            if echo_abort:
                                continue

                            audio = np.concatenate(frames).flatten()

                            if len(audio) < self._SR * self._MIN_SEG_S:
                                continue

                            text = self._transcribe(audio)
                            if not text:
                                continue

                            print(f"[WakeWord] Heard: '{text}'")

                            matched_phrase = next(
                                (p for p in WAKE_PHRASES if p in text), None
                            )
                            if matched_phrase:
                                idx = text.find(matched_phrase) + len(matched_phrase)
                                command_text = text[idx:].strip(" .,!?-")
                                for filler in ("please", "can you", "could you", "i want you to"):
                                    if command_text.startswith(filler):
                                        command_text = command_text[len(filler):].strip()
                                self._paused = True
                                threading.Thread(
                                    target=self._callback, args=(command_text,), daemon=True
                                ).start()

                        except Exception as e:
                            print(f"[WakeWord] Stream read error: {e} — reopening stream")
                            break   # break inner loop → reopen stream

            except Exception as e:
                print(f"[WakeWord] Stream open error: {e}")
                time.sleep(2.0)
