"""audio/turn_detector.py — Event-driven turn detector.

No timeouts. No fixed delays. VAD confidence drives all state transitions.
Same approach as Google Meet's turn detection system.

State machine:
  IDLE       → (speech detected, conf >= SPEECH_START_CONFIDENCE)  → LISTENING
  LISTENING  → (350ms silence after speech, utterance >= MIN_MS)   → PROCESSING
  PROCESSING → (TTS about to start, call mark_processing_done())   → SPEAKING
  SPEAKING   → (human speaks, conf >= INTERRUPT_CONFIDENCE)        → LISTENING
  SPEAKING   → (TTS done, call mark_tts_done())                    → IDLE

Smart Turn v3 (optional):
  When the pipecat-ai/smart-turn-v3 ONNX model is available, the silence
  timer is used as a gate only — the semantic EOT classifier decides if the
  user is truly done before firing on_speech_end.  Falls back gracefully to
  pure silence detection if the model is absent.

Usage:
    detector = TurnDetector(sample_rate=16000)
    detector.on_speech_start = lambda: ui.set_state("listening")
    detector.on_speech_end   = lambda audio: asyncio.run(handle_utterance(audio))
    detector.on_interrupt    = lambda: tts_engine.stop_immediately()

    # In your mic stream callback (called every 30ms):
    detector.feed(audio_chunk)
"""

import threading
import time
import numpy as np
from audio.vad import is_speech
import logging

logger = logging.getLogger(__name__)

# ── Pipecat Smart Turn v3 (semantic EOT) ──────────────────────────── #
# Download: huggingface-cli download pipecat-ai/smart-turn-v3
# Provides semantic end-of-turn detection in ~12ms on CPU (8M params).
_smart_turn_session = None
_smart_turn_extractor = None
_USE_SMART_TURN = False

try:
    import os as _os
    import json as _json
    import onnxruntime as _ort
    from transformers import AutoFeatureExtractor as _AFE

    _MODEL_DIR = _os.path.join(
        _os.path.expandvars("%USERPROFILE%"),
        ".cache", "huggingface", "hub",
        "models--pipecat-ai--smart-turn-v3", "snapshots",
    )
    # Find the most recent snapshot
    if _os.path.isdir(_MODEL_DIR):
        _snaps = sorted(_os.listdir(_MODEL_DIR))
        if _snaps:
            _snap = _os.path.join(_MODEL_DIR, _snaps[-1])
            _onnx = _os.path.join(_snap, "model.onnx")
            if _os.path.exists(_onnx):
                _smart_turn_session = _ort.InferenceSession(
                    _onnx,
                    providers=["CPUExecutionProvider"],
                )
                _smart_turn_extractor = _AFE.from_pretrained(_snap)
                _USE_SMART_TURN = True
                print("[TurnDetector] Smart Turn v3 loaded — semantic EOT active.")
except Exception as _e:
    print(f"[TurnDetector] Smart Turn v3 not available ({_e}) — using silence timer.")


class TurnDetector:
    # --- Tunable constants ---
    SPEECH_START_CONFIDENCE  = 0.45   # VAD threshold to enter LISTENING
    SPEECH_END_SILENCE_MS    = 350    # ms of silence that signals end of turn
    MIN_UTTERANCE_MS         = 200    # ignore noise bursts shorter than this
    INTERRUPT_CONFIDENCE     = 0.75   # raised: prevents TTS echo from self-triggering
    CHUNK_MS                 = 30     # expected audio chunk duration in ms

    def __init__(self, sample_rate: int = 16000):
        self.sample_rate = sample_rate
        self.chunk_size  = int(sample_rate * self.CHUNK_MS / 1000)
        self._silence_threshold_chunks = self.SPEECH_END_SILENCE_MS // self.CHUNK_MS

        self._state      = "IDLE"
        self._state_lock = threading.Lock()

        self._speech_buffer: list[np.ndarray] = []
        self._silence_chunks = 0
        self._utterance_start = 0.0
        self._processing_interrupted = False

        # Callbacks — assign before calling start()
        self.on_speech_start: "callable | None" = None  # () -> None
        self.on_speech_end:   "callable | None" = None  # (audio: np.ndarray) -> None
        self.on_interrupt:    "callable | None" = None  # () -> None

        logger.info("[TurnDetector] Initialized")

    # --- Public API ---

    def get_state(self) -> str:
        with self._state_lock:
            return self._state

    def set_state(self, new_state: str) -> None:
        with self._state_lock:
            old = self._state
            if old != new_state:
                self._state = new_state
                logger.info(f"[TurnDetector] {old} → {new_state}")

    def mark_tts_done(self) -> None:
        """Call when TTS finishes playing. Returns to IDLE."""
        if self.get_state() == "SPEAKING":
            self.set_state("IDLE")
            self._speech_buffer = []
            self._silence_chunks = 0

    def mark_processing_done(self) -> None:
        """Call when LLM response is ready and TTS is about to start."""
        if self.get_state() == "PROCESSING":
            self.set_state("SPEAKING")

    def was_interrupted_while_processing(self) -> bool:
        """True if user spoke while we were in PROCESSING state."""
        return self._processing_interrupted

    def clear_processing_interrupt(self) -> None:
        self._processing_interrupted = False

    def _smart_turn_eot_prob(self, audio: np.ndarray) -> float:
        """Return probability (0-1) that the user has finished their turn.
        Returns 1.0 (always fire) when Smart Turn v3 is not loaded.
        """
        if not _USE_SMART_TURN or _smart_turn_session is None or _smart_turn_extractor is None:
            return 1.0
        try:
            inputs = _smart_turn_extractor(
                audio.astype(np.float32),
                sampling_rate=self.sample_rate,
                return_tensors="np",
            )
            ort_inputs = {k: v for k, v in inputs.items()}
            outputs = _smart_turn_session.run(None, ort_inputs)
            # Model output: logits [not_done, done] — softmax to get probability
            logits = outputs[0][0]
            exp_logits = np.exp(logits - np.max(logits))
            probs = exp_logits / exp_logits.sum()
            return float(probs[1])   # probability that turn is complete
        except Exception as e:
            logger.debug("[TurnDetector] Smart Turn inference error: %s", e)
            return 1.0

    def feed(self, audio_chunk: np.ndarray) -> None:
        """Feed every mic chunk here — ~30ms at 16kHz.

        Drives the entire state machine. Non-blocking.
        Heavy callbacks are dispatched to daemon threads.
        """
        speech_detected, confidence = is_speech(audio_chunk, self.sample_rate)
        state = self.get_state()

        if state == "IDLE":
            if speech_detected and confidence >= self.SPEECH_START_CONFIDENCE:
                self.set_state("LISTENING")
                self._speech_buffer = [audio_chunk.copy()]
                self._silence_chunks = 0
                self._utterance_start = time.monotonic()
                if self.on_speech_start:
                    threading.Thread(
                        target=self.on_speech_start, daemon=True, name="SpeechStart"
                    ).start()

        elif state == "LISTENING":
            self._speech_buffer.append(audio_chunk.copy())

            if speech_detected:
                self._silence_chunks = 0
            else:
                self._silence_chunks += 1

            utterance_ms = (time.monotonic() - self._utterance_start) * 1000
            if (self._silence_chunks >= self._silence_threshold_chunks
                    and utterance_ms >= self.MIN_UTTERANCE_MS):
                # Smart Turn v3: confirm user is actually done before firing
                if _USE_SMART_TURN and utterance_ms < 8000:
                    complete_audio_check = np.concatenate(self._speech_buffer)
                    eot_prob = self._smart_turn_eot_prob(complete_audio_check)
                    if eot_prob < 0.5:
                        # User likely still thinking — reset silence counter, keep buffering
                        self._silence_chunks = 0
                        return
                complete_audio = np.concatenate(self._speech_buffer)
                self._speech_buffer = []
                self.set_state("PROCESSING")
                if self.on_speech_end:
                    threading.Thread(
                        target=self.on_speech_end,
                        args=(complete_audio,),
                        daemon=True,
                        name="SpeechEnd",
                    ).start()

        elif state == "PROCESSING":
            # User spoke while LLM is generating — flag it so pipeline can log/react
            if speech_detected and confidence >= self.INTERRUPT_CONFIDENCE:
                if not self._processing_interrupted:
                    logger.info("[TurnDetector] Speech during PROCESSING — flagged")
                    self._processing_interrupted = True

        elif state == "SPEAKING":
            if speech_detected and confidence >= self.INTERRUPT_CONFIDENCE:
                logger.info("[TurnDetector] Interrupt detected")
                if self.on_interrupt:
                    threading.Thread(
                        target=self.on_interrupt, daemon=True, name="Interrupt"
                    ).start()
                self.set_state("LISTENING")
                self._speech_buffer = [audio_chunk.copy()]
                self._silence_chunks = 0
                self._utterance_start = time.monotonic()
