"""audio/tts_engine.py — Unified interrupt-safe TTS engine.

English priority chain:
  1. Chatterbox TTS  — voice cloning from WAV sample (free, local, ~400ms)
  2. Kokoro-82M ONNX — built-in voices incl. bm_george British male (free, local, ~80ms)
  3. ElevenLabs      — cloud streaming, skipped after 401
  4. SAPI5           — always available on Windows (fallback)

Hindi priority chain:
  1. ElevenLabs (eleven_multilingual_v2, elevenlabs_hindi_voice_id) — sweet female cloud voice
  2. Sarvam bulbul:v3 — natural Indian speech via cloud API (fallback)
  3. SAPI5            — last resort

Public API:
    engine = TTSEngine(config)
    engine.speak(text)              # blocking, routes by language
    engine.speak_filler(text)       # non-blocking daemon thread
    engine.stop_immediately()       # interrupt mid-word
    engine.switch_language("hi")    # "en" or "hi"
    engine.is_speaking() -> bool
    engine.shutdown()
"""

import logging
import os
import queue as _queue_mod
import subprocess
import threading
from typing import Optional

logger = logging.getLogger(__name__)

EL_MODEL           = "eleven_turbo_v2_5"
SARVAM_MODEL       = "bulbul:v3"
SARVAM_SPEAKER_DEF = "kavya"    # default female — overridden by config sarvam_speaker
SARVAM_PACE        = 1.15


class TTSEngine:
    def __init__(self, config: dict):
        self._config   = config
        self._language = "en"            # "en" or "hi"
        self._persona  = "jarvis"        # "jarvis" or "friday"
        self._stop     = threading.Event()
        self._lock     = threading.Lock()
        self._speaking = threading.Event()

        self._el_client       : Optional[object] = None  # ElevenLabsTTS (English)
        self._el_hindi_client : Optional[object] = None  # ElevenLabsTTS (Hindi)
        self._sarvam_tts      : Optional[object] = None  # SarvamStreamingTTS
        self._chatterbox      : Optional[object] = None  # ChatterboxTTS
        self._kokoro          : Optional[object] = None  # KokoroTTS
        self._ps_rate_val     : int = self._calc_ps_rate()
        self._el_auth_failed  : bool = False
        self._el_hindi_failed : bool = False
        self._cb_init_tried   : bool = False
        self._ko_init_tried   : bool = False

        self._sent_q              = _queue_mod.Queue()
        self._sent_lock           = threading.Lock()
        self._sent_worker_running : bool = False

        self._init_elevenlabs()
        self._init_elevenlabs_hindi()
        self._init_sarvam()
        logger.info(
            "[TTSEngine] Initialized  lang=%s  el_en=%s  el_hi=%s  sarvam=%s",
            self._language,
            self._el_client is not None,
            self._el_hindi_client is not None,
            self._sarvam_tts is not None,
        )

    # ------------------------------------------------------------------ #
    #  Initialization helpers                                              #
    # ------------------------------------------------------------------ #

    def _init_chatterbox(self) -> None:
        try:
            import importlib.util
            if importlib.util.find_spec("chatterbox") is None:
                logger.info("[TTSEngine] Chatterbox not installed — skipped")
                return
            from audio.chatterbox_client import ChatterboxTTS
            self._chatterbox = ChatterboxTTS(self._config)
            logger.info("[TTSEngine] Chatterbox loading in background…")
        except Exception as e:
            logger.warning("[TTSEngine] Chatterbox init failed: %s", e)

    def _init_kokoro(self) -> None:
        try:
            import importlib.util
            if importlib.util.find_spec("kokoro_onnx") is None:
                logger.info("[TTSEngine] kokoro-onnx not installed — skipped")
                return
            from audio.kokoro_client import KokoroTTS
            self._kokoro = KokoroTTS(self._config)
            logger.info("[TTSEngine] Kokoro loading in background…")
        except Exception as e:
            logger.warning("[TTSEngine] Kokoro init failed: %s", e)

    def _init_elevenlabs(self) -> None:
        key = (
            os.getenv("ELEVENLABS_API_KEY", "").strip()
            or self._config.get("elevenlabs_api_key", "").strip()
        )
        voice_id = self._config.get("elevenlabs_voice_id", "").strip()
        if not key or not voice_id:
            logger.info("[TTSEngine] ElevenLabs skipped — no key/voice_id")
            return
        try:
            from audio.elevenlabs_client import ElevenLabsTTS
            cfg = dict(self._config)
            cfg["elevenlabs_model"] = EL_MODEL
            self._el_client = ElevenLabsTTS(key, cfg)
            self._el_auth_failed = False
            logger.info("[TTSEngine] ElevenLabs ready (voice=%s model=%s)", voice_id, EL_MODEL)
        except Exception as e:
            logger.warning("[TTSEngine] ElevenLabs init failed: %s", e)

    def _init_elevenlabs_hindi(self) -> None:
        key = (
            os.getenv("ELEVENLABS_API_KEY", "").strip()
            or self._config.get("elevenlabs_api_key", "").strip()
        )
        voice_id = self._config.get("elevenlabs_hindi_voice_id", "").strip()
        if not key or not voice_id:
            logger.info("[TTSEngine] ElevenLabs Hindi skipped — set elevenlabs_hindi_voice_id in config")
            return
        try:
            from audio.elevenlabs_client import ElevenLabsTTS
            cfg = dict(self._config)
            cfg["elevenlabs_voice_id"]    = voice_id
            cfg["elevenlabs_model"]       = self._config.get("elevenlabs_hindi_model", "eleven_multilingual_v2")
            cfg["elevenlabs_stability"]   = self._config.get("elevenlabs_hindi_stability", 0.50)
            cfg["elevenlabs_similarity"]  = self._config.get("elevenlabs_hindi_similarity", 0.80)
            cfg["elevenlabs_style"]       = 0.0
            cfg["elevenlabs_speaker_boost"] = True
            self._el_hindi_client = ElevenLabsTTS(key, cfg)
            self._el_hindi_failed = False
            logger.info("[TTSEngine] ElevenLabs Hindi ready (voice=%s)", voice_id)
        except Exception as e:
            logger.warning("[TTSEngine] ElevenLabs Hindi init failed: %s", e)

    def _init_sarvam(self) -> None:
        key = (
            os.getenv("SARVAM_API_KEY", "").strip()
            or self._config.get("sarvam_api_key", "").strip()
        )
        if not key:
            logger.info("[TTSEngine] Sarvam skipped — no key")
            return
        try:
            from audio.sarvam_client import SarvamStreamingTTS
            cfg = dict(self._config)
            speaker = self._config.get("sarvam_speaker", "").strip() or SARVAM_SPEAKER_DEF
            cfg["sarvam_speaker"] = speaker
            cfg["sarvam_pace"]    = self._config.get("sarvam_pace", SARVAM_PACE)
            self._sarvam_tts = SarvamStreamingTTS(key, cfg)
            logger.info("[TTSEngine] Sarvam ready (speaker=%s pace=%s)", speaker, cfg["sarvam_pace"])
        except Exception as e:
            logger.warning("[TTSEngine] Sarvam init failed: %s", e)

    def _calc_ps_rate(self) -> int:
        wpm = self._config.get("voice_rate", 155)
        return max(-3, min(8, round((wpm - 100) / 20)))

    # ------------------------------------------------------------------ #
    #  Public API                                                          #
    # ------------------------------------------------------------------ #

    def switch_language(self, lang: str) -> None:
        """Switch TTS language at runtime. lang: "en" or "hi"."""
        lang = lang.lower().strip()
        if lang not in ("en", "hi"):
            logger.warning("[TTSEngine] Unknown language %r — keeping %s", lang, self._language)
            return
        self._language = lang
        logger.info("[TTSEngine] Language switched to %s", lang)

    def switch_persona(self, persona: str) -> None:
        """Switch voice persona. "friday" uses her multilingual voice even in English."""
        self._persona = persona if persona in ("jarvis", "friday") else "jarvis"
        logger.info("[TTSEngine] Persona switched to %s", self._persona)

    def speak(self, text: str) -> None:
        """Speak text synchronously, blocking until done or interrupted."""
        if not text or not text.strip():
            return
        with self._lock:
            self._stop.clear()
            self._speaking.set()
            try:
                self._route(text)
            except Exception as e:
                logger.error("[TTSEngine] speak() error: %s", e)
                self._fallback_ps(text)
            finally:
                self._speaking.clear()

    def speak_filler(self, text: str) -> None:
        """Speak a filler phrase in a daemon thread — non-blocking.

        Fires immediately; does not block the caller. Will be cut short
        by the next stop_immediately() call (e.g. when LLM response arrives).
        """
        if not text or not text.strip():
            return
        threading.Thread(
            target=self._speak_filler_blocking,
            args=(text,),
            daemon=True,
            name="TTSFiller",
        ).start()

    def queue_sentence(self, text: str) -> None:
        """Non-blocking: enqueue a sentence for sequential TTS playback.

        Used by the streaming pipeline — sentences arrive one at a time as the
        LLM generates them and are played in order via a background worker.
        """
        if not text or not text.strip():
            return
        self._sent_q.put(text)
        with self._sent_lock:
            if not self._sent_worker_running:
                self._sent_worker_running = True
                threading.Thread(
                    target=self._sentence_worker,
                    daemon=True,
                    name="TTS-Queue",
                ).start()

    def stop_immediately(self) -> None:
        """Interrupt any active speech mid-word."""
        self._stop.set()
        while not self._sent_q.empty():
            try:
                self._sent_q.get_nowait()
                self._sent_q.task_done()
            except _queue_mod.Empty:
                break
        self._stop_backends()

    def is_speaking(self) -> bool:
        return self._speaking.is_set() or not self._sent_q.empty()

    def shutdown(self) -> None:
        self.stop_immediately()

    # ------------------------------------------------------------------ #
    #  Routing                                                             #
    # ------------------------------------------------------------------ #

    def _route(self, text: str) -> None:
        if self._language == "hi":
            self._speak_hindi(text)
        elif self._persona == "friday" and self._el_hindi_client is not None:
            # Friday in English mode — use her multilingual voice (eleven_multilingual_v2)
            self._speak_hindi(text)
        else:
            self._speak_english(text)

    def _ensure_local_tts(self) -> None:
        """Lazily start Chatterbox and Kokoro background loaders on first English speech."""
        if self._chatterbox is None and not self._cb_init_tried:
            self._cb_init_tried = True
            self._init_chatterbox()
        if self._kokoro is None and not self._ko_init_tried:
            self._ko_init_tried = True
            self._init_kokoro()

    def _speak_english(self, text: str) -> None:
        self._ensure_local_tts()
        # 1. Chatterbox (voice cloning — primary if loaded)
        if self._chatterbox is not None and self._chatterbox.is_ready():
            try:
                self._chatterbox.reset()
                self._chatterbox.speak(text)
                return
            except Exception as e:
                logger.warning("[TTSEngine] Chatterbox error: %s — trying Kokoro", e)

        # 2. Kokoro (fast built-in voices — secondary)
        if self._kokoro is not None and self._kokoro.is_ready():
            try:
                self._kokoro.reset()
                self._kokoro.speak(text)
                return
            except Exception as e:
                logger.warning("[TTSEngine] Kokoro error: %s — trying ElevenLabs", e)

        # 3. ElevenLabs (cloud, skipped after 401)
        if self._el_client is not None and not self._el_auth_failed:
            try:
                self._el_client.reset()
                self._el_client.speak(text)
                return
            except Exception as e:
                err_str = str(e)
                if "401" in err_str or "Unauthorized" in err_str:
                    self._el_auth_failed = True
                    logger.error(
                        "[TTSEngine] ElevenLabs 401 — key expired. "
                        "Update elevenlabs_api_key in config.json. Using SAPI5 for this session."
                    )
                else:
                    logger.warning("[TTSEngine] ElevenLabs error: %s — SAPI5 fallback", e)

        # 4. SAPI5 PowerShell (always available)
        self._fallback_ps(text)

    def _speak_hindi(self, text: str) -> None:
        """Hindi TTS: ElevenLabs multilingual → Sarvam bulbul:v3 → SAPI5."""
        # 1. ElevenLabs Hindi (sweet female voice, eleven_multilingual_v2)
        if self._el_hindi_client is not None and not self._el_hindi_failed:
            try:
                self._el_hindi_client.reset()
                self._el_hindi_client.speak(text)
                return
            except Exception as e:
                err_str = str(e)
                if "401" in err_str or "Unauthorized" in err_str:
                    self._el_hindi_failed = True
                    logger.error("[TTSEngine] ElevenLabs Hindi 401 — check ELEVENLABS_API_KEY. Falling back to Sarvam.")
                else:
                    logger.warning("[TTSEngine] ElevenLabs Hindi error: %s — trying Sarvam", e)

        # 2. Sarvam bulbul:v3 (native Indian female voice)
        if self._sarvam_tts is not None:
            try:
                self._sarvam_tts.reset()
                self._sarvam_tts.stream_speak(text, language="hi-IN", blocking=True)
                return
            except Exception as e:
                logger.warning("[TTSEngine] Sarvam error: %s — PS fallback", e)

        # 3. SAPI5 last resort
        self._fallback_ps(text)

    def _speak_sarvam(self, text: str, lang_code: str = "hi-IN") -> None:
        if self._sarvam_tts is not None:
            try:
                self._sarvam_tts.reset()
                self._sarvam_tts.stream_speak(text, language=lang_code, blocking=True)
                return
            except Exception as e:
                logger.warning("[TTSEngine] Sarvam error: %s — PS fallback", e)
        self._fallback_ps(text)

    # ------------------------------------------------------------------ #
    #  SAPI5 PowerShell fallback (always works on Windows)                #
    # ------------------------------------------------------------------ #

    def _fallback_ps(self, text: str) -> None:
        if self._stop.is_set():
            return
        safe = text.replace("'", "''").replace("\n", " ").replace("\r", " ")
        ps_cmd = (
            "Add-Type -AssemblyName System.Speech; "
            "$s = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
            f"$s.Volume = 100; $s.Rate = {self._ps_rate_val}; $s.Speak('{safe}');"
        )
        try:
            subprocess.run(
                ["powershell", "-NoProfile", "-NonInteractive", "-c", ps_cmd],
                capture_output=True,
                timeout=60,
            )
        except subprocess.TimeoutExpired:
            pass
        except Exception as e:
            logger.error("[TTSEngine] PowerShell fallback failed: %s", e)

    # ------------------------------------------------------------------ #
    #  Helpers                                                             #
    # ------------------------------------------------------------------ #

    def _speak_filler_blocking(self, text: str) -> None:
        """Blocking inner call used by speak_filler's daemon thread."""
        self._stop.clear()
        self._speaking.set()
        try:
            self._route(text)
        except Exception as e:
            logger.debug("[TTSEngine] Filler speak error: %s", e)
        finally:
            self._speaking.clear()

    def _sentence_worker(self) -> None:
        """Background worker: drain the sentence queue via speak()."""
        while True:
            try:
                text = self._sent_q.get(timeout=1.0)
            except _queue_mod.Empty:
                with self._sent_lock:
                    self._sent_worker_running = False
                break
            try:
                self.speak(text)
            except Exception as e:
                logger.debug("[TTSEngine] sentence_worker error: %s", e)
            finally:
                self._sent_q.task_done()

    def _stop_backends(self) -> None:
        for backend in (self._chatterbox, self._kokoro, self._el_client, self._el_hindi_client, self._sarvam_tts):
            if backend is not None:
                try:
                    backend.stop()
                except Exception:
                    pass
        try:
            import sounddevice as sd
            sd.stop()
        except Exception:
            pass
