"""audio/stt_engine.py — Dual STT engine.

Routes:
  English / whisper mode → faster-whisper local (base model, int8, CPU)
  Hindi   / sarvam mode  → Sarvam saaras:v3 (auto-detect Hinglish)

This engine transcribes pre-recorded audio arrays delivered by TurnDetector.
It does NOT record from the mic — that is done by the pipeline + TurnDetector.

Public API:
    engine = STTEngine(config)
    text   = engine.transcribe(audio_np)   # routes by current mode
    engine.set_mode("whisper")             # or "sarvam"
    engine.set_language("hi")             # "en" or "hi"
    engine.preload()                       # warm up Whisper in background
"""

import logging
import threading
from typing import Optional

import numpy as np

logger = logging.getLogger(__name__)

# Pre-import on main thread so av/PyAV C-extensions initialize before any
# background threads start — avoids COM apartment violation (0xC0000005).
try:
    from faster_whisper import WhisperModel as _WhisperModel
except ImportError:
    _WhisperModel = None  # type: ignore

_WHISPER_MODEL_NAME = "base.en"   # English-only, ~150 MB, fastest


class STTEngine:
    def __init__(self, config: dict):
        self._config   = config
        self._mode     = config.get("stt_provider", "whisper").lower()
        self._language = "en"

        self._whisper       = None
        self._whisper_lock  = threading.Lock()
        self._whisper_failed = False

        self._sarvam_key = (
            __import__("os").getenv("SARVAM_API_KEY", "").strip()
            or config.get("sarvam_api_key", "").strip()
        )

        # Model is loaded on first use OR via explicit preload() call from main.py.
        # Do NOT start background thread here — it races with ClapListener / VAD
        # sounddevice stream init in _start_subsystems() → 0xC0000005 crash.

        logger.info("[STTEngine] Initialized  mode=%s  sarvam=%s",
                    self._mode, bool(self._sarvam_key))

    # ------------------------------------------------------------------ #
    #  Public API                                                          #
    # ------------------------------------------------------------------ #

    def set_mode(self, mode: str) -> None:
        """Switch STT backend. mode: "whisper" or "sarvam"."""
        mode = mode.lower().strip()
        if mode not in ("whisper", "sarvam"):
            logger.warning("[STTEngine] Unknown mode %r — keeping %s", mode, self._mode)
            return
        self._mode = mode
        logger.info("[STTEngine] Mode switched to %s", mode)

    def set_language(self, lang: str) -> None:
        """Set target language. lang: "en" or "hi"."""
        lang = lang.lower().strip()
        if lang not in ("en", "hi"):
            logger.warning("[STTEngine] Unknown language %r — keeping %s", lang, self._language)
            return
        self._language = lang
        # Auto-switch mode when language changes
        if lang == "hi" and self._sarvam_key:
            self._mode = "sarvam"
        elif lang == "en":
            self._mode = "whisper"
        logger.info("[STTEngine] Language=%s  mode=%s", self._language, self._mode)

    def transcribe(self, audio: np.ndarray) -> str:
        """Transcribe audio array → text. Routes by current mode.

        audio: float32 numpy array at 16kHz (from TurnDetector on_speech_end)
        Returns empty string on failure — never raises.
        """
        if audio is None or len(audio) < 100:
            return ""

        if self._mode == "sarvam" and self._sarvam_key:
            result = self._transcribe_sarvam(audio)
            if result:
                return result
            logger.warning("[STTEngine] Sarvam returned empty — Whisper fallback")

        return self._transcribe_whisper(audio)

    def preload(self) -> None:
        """Explicitly warm up Whisper (no-op if already loaded)."""
        threading.Thread(target=self._load_whisper, daemon=True, name="STT-Preload").start()

    # ------------------------------------------------------------------ #
    #  Whisper                                                             #
    # ------------------------------------------------------------------ #

    def _load_whisper(self) -> None:
        if self._whisper is not None or self._whisper_failed:
            return
        # COM must be initialised per-thread on Windows.
        try:
            import pythoncom
            pythoncom.CoInitializeEx(pythoncom.COINIT_MULTITHREADED)
        except Exception:
            pass
        with self._whisper_lock:
            if self._whisper is not None:
                return
            try:
                if _WhisperModel is None:
                    raise ImportError("faster-whisper not installed")
                model_name = self._config.get("stt_model", _WHISPER_MODEL_NAME)
                logger.info("[STTEngine] Loading Whisper model '%s'…", model_name)
                self._whisper = _WhisperModel(model_name, device="cpu", compute_type="int8")
                logger.info("[STTEngine] Whisper model ready")
            except Exception as e:
                logger.error("[STTEngine] Whisper load failed: %s", e)
                self._whisper_failed = True

    def _transcribe_whisper(self, audio: np.ndarray) -> str:
        if self._whisper_failed:
            return ""
        if self._whisper is None:
            self._load_whisper()
        if self._whisper is None:
            return ""
        try:
            model_name = self._config.get("stt_model", _WHISPER_MODEL_NAME)
            lang_kwargs = {"language": "en"} if model_name.endswith(".en") else {}
            segments, _ = self._whisper.transcribe(
                audio.astype(np.float32),
                beam_size=1,
                **lang_kwargs,
            )
            return " ".join(seg.text.strip() for seg in segments).strip()
        except Exception as e:
            logger.error("[STTEngine] Whisper transcribe error: %s", e)
            return ""

    # ------------------------------------------------------------------ #
    #  Sarvam saaras:v3                                                    #
    # ------------------------------------------------------------------ #

    def _transcribe_sarvam(self, audio: np.ndarray) -> str:
        if not self._sarvam_key:
            return ""
        try:
            from audio.sarvam_client import SarvamClient
            client = SarvamClient(self._sarvam_key, self._config)
            lang_code = "hi-IN" if self._language == "hi" else "unknown"
            return client.transcribe_numpy(audio, language_code=lang_code, mode="codemix")
        except Exception as e:
            logger.warning("[STTEngine] Sarvam transcribe error: %s", e)
            return ""

    # ------------------------------------------------------------------ #
    #  Recording (for non-VAD activation paths)                            #
    # ------------------------------------------------------------------ #

    def record(
        self,
        max_seconds: float = 8.0,
        silence_threshold: float = 0.01,
        silence_duration: float = 1.5,
    ) -> np.ndarray:
        """Record from mic until silence is detected. Returns float32 array at 16kHz."""
        import sounddevice as sd
        sr = 16000
        chunk_ms = 100
        chunk_frames = int(sr * chunk_ms / 1000)
        max_chunks = int(max_seconds * 1000 / chunk_ms)
        silence_needed = int(silence_duration * 1000 / chunk_ms)

        frames: list = []
        silent_count = 0

        try:
            with sd.InputStream(samplerate=sr, channels=1, dtype="float32",
                                blocksize=chunk_frames) as stream:
                for _ in range(max_chunks):
                    chunk, _ = stream.read(chunk_frames)
                    raw = chunk.flatten()
                    frames.append(raw)
                    rms = float(np.sqrt(np.mean(raw ** 2)))
                    if rms < silence_threshold:
                        silent_count += 1
                        if silent_count >= silence_needed:
                            break
                    else:
                        silent_count = 0
        except Exception as e:
            logger.error("[STTEngine] record() error: %s", e)

        if not frames:
            return np.array([], dtype=np.float32)
        return np.concatenate(frames, axis=0).flatten()

    def listen_sarvam_streaming(
        self,
        on_partial=None,
        on_final=None,
        max_seconds: float = 8.0,
        language_code: str = "unknown",
    ) -> str:
        """Record and stream-transcribe via Sarvam WebSocket STT.

        Calls on_partial with live partial transcripts while user speaks.
        Falls back to record() + transcribe() if Sarvam is unavailable.
        """
        if not self._sarvam_key:
            audio = self.record(max_seconds=max_seconds)
            return self.transcribe(audio)

        try:
            import sounddevice as sd
            from audio.sarvam_client import SarvamStreamingSTT
            sr = 16000
            chunk_frames = int(sr * 0.1)

            def _mic_generator():
                max_chunks = int(max_seconds / 0.1)
                with sd.InputStream(
                    samplerate=sr, channels=1, dtype="int16", blocksize=chunk_frames
                ) as stream:
                    for _ in range(max_chunks):
                        data, _ = stream.read(chunk_frames)
                        yield data.flatten().tobytes()

            streamer = SarvamStreamingSTT(self._sarvam_key, self._config)
            result = streamer.transcribe_stream(
                _mic_generator(),
                language=language_code,
                on_partial=on_partial,
                on_final=on_final,
            )
            return result or ""
        except Exception as e:
            logger.warning("[STTEngine] Streaming STT failed (%s) — Whisper fallback", e)
            audio = self.record(max_seconds=max_seconds)
            return self.transcribe(audio)
