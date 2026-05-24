"""awareness/emotion_engine.py — Voice and face emotion detection.

Voice emotion: extracted from audio AFTER transcription.
  Pitch, RMS energy, speech rate, spectral centroid → FOCUSED/STRESSED/TIRED/FRUSTRATED/EXCITED

Face emotion: every 90 seconds, non-blocking, single frame.
  Uses FER (fer library) with MTCNN=False for Ryzen 5 efficiency.

Only acts on emotion if BOTH voice and face agree, OR one has very high confidence.
Threshold: confidence > 0.65 to act.
"""

import threading
import time
from typing import Optional

import numpy as np

# ── Optional imports ──────────────────────────────────────────────── #
try:
    import librosa as _librosa
    _HAS_LIBROSA = True
except Exception:
    _librosa = None
    _HAS_LIBROSA = False

try:
    import cv2 as _cv2
    _HAS_CV2 = True
except Exception:
    _cv2 = None
    _HAS_CV2 = False

try:
    try:
        from fer import FER as _FER          # fer < 25.x
    except ImportError:
        from fer.fer import FER as _FER      # fer >= 25.x
    _HAS_FER = True
except Exception:
    _FER = None
    _HAS_FER = False

# Emotion states
EMOTIONS = ("focused", "stressed", "tired", "frustrated", "excited", "neutral")

# Behavior map for each emotion
EMOTION_BEHAVIORS: dict[str, dict] = {
    "neutral": {"tts_mode": "normal", "brevity": "normal"},
    "focused": {"tts_mode": "quiet", "brevity": "minimal"},
    "stressed": {"tts_mode": "normal", "brevity": "short"},
    "tired": {"tts_mode": "quiet", "brevity": "short"},
    "frustrated": {"tts_mode": "normal", "brevity": "patient"},
    "excited": {"tts_mode": "normal", "brevity": "normal"},
}

_FACE_INTERVAL = 90.0  # seconds between face scans
_CONFIDENCE_THRESHOLD = 0.65


class EmotionEngine:
    """Detects user emotion from voice and (optionally) face."""

    def __init__(self, config: Optional[dict] = None, speak_fn=None):
        self._config = config or {}
        self._speak = speak_fn

        self.current_emotion = "neutral"
        self.emotion_confidence = 0.0

        self._voice_emotion = "neutral"
        self._voice_confidence = 0.0
        self._face_emotion = "neutral"
        self._face_confidence = 0.0

        self._running = False
        self._face_thread: Optional[threading.Thread] = None
        self._face_detector = None
        self._last_face_scan = 0.0
        self._face_present = True

        self._lock = threading.Lock()

        # HUD dimming callback
        self._hud_dim_callback = None

    def set_hud_dim_callback(self, cb) -> None:
        self._hud_dim_callback = cb

    def start(self) -> None:
        if not self._config.get("emotion_detection", True):
            return

        if _HAS_FER and self._config.get("face_detection", True):
            self._face_thread = threading.Thread(
                target=self._face_loop, daemon=True, name="FaceEmotion"
            )
            self._face_thread.start()
            print("[EmotionEngine] Face emotion detection started.")
        else:
            print("[EmotionEngine] Face detection disabled or FER unavailable.")

    def stop(self) -> None:
        self._running = False

    # ------------------------------------------------------------------ #
    #  Voice emotion (called after each transcription)                    #
    # ------------------------------------------------------------------ #

    def analyze_voice(self, audio: np.ndarray, transcript: str) -> str:
        """Analyze voice audio for emotion. Returns detected emotion string."""
        if not _HAS_LIBROSA or audio is None or len(audio) == 0:
            return "neutral"

        try:
            features = self._extract_voice_features(audio, transcript)
            emotion, confidence = self._classify_voice(features)

            with self._lock:
                self._voice_emotion = emotion
                self._voice_confidence = confidence

            self._update_combined_emotion()
            return emotion
        except Exception as e:
            print(f"[EmotionEngine] Voice analysis error: {e}")
            return "neutral"

    def _extract_voice_features(self, audio: np.ndarray, transcript: str) -> dict:
        f32 = audio.astype(np.float32)
        sr = 16000

        features = {}

        # Pitch (fundamental frequency)
        try:
            f0, voiced_flag, _ = _librosa.pyin(
                f32, fmin=60, fmax=400, sr=sr,
                frame_length=2048, hop_length=512
            )
            voiced_f0 = f0[voiced_flag] if voiced_flag is not None else f0
            voiced_f0 = voiced_f0[~np.isnan(voiced_f0)] if len(voiced_f0) > 0 else np.array([150.0])
            features["pitch_mean"] = float(np.mean(voiced_f0)) if len(voiced_f0) > 0 else 150.0
            features["pitch_var"] = float(np.var(voiced_f0)) if len(voiced_f0) > 0 else 0.0
        except Exception:
            features["pitch_mean"] = 150.0
            features["pitch_var"] = 0.0

        # Energy (RMS)
        try:
            rms = _librosa.feature.rms(y=f32, frame_length=2048, hop_length=512)[0]
            features["rms_mean"] = float(np.mean(rms))
            features["rms_var"] = float(np.var(rms))
        except Exception:
            features["rms_mean"] = 0.05
            features["rms_var"] = 0.0

        # Speech rate
        words = transcript.split() if transcript else []
        duration = len(f32) / sr
        features["speech_rate"] = len(words) / max(duration, 0.1)

        # Spectral centroid
        try:
            centroid = _librosa.feature.spectral_centroid(y=f32, sr=sr, hop_length=512)[0]
            features["centroid_mean"] = float(np.mean(centroid))
        except Exception:
            features["centroid_mean"] = 2000.0

        return features

    def _classify_voice(self, features: dict) -> tuple[str, float]:
        """Rule-based emotion classification from voice features."""
        pitch_mean = features.get("pitch_mean", 150)
        pitch_var = features.get("pitch_var", 0)
        rms_mean = features.get("rms_mean", 0.05)
        speech_rate = features.get("speech_rate", 3.0)  # words/sec
        centroid = features.get("centroid_mean", 2000)

        scores: dict[str, float] = {e: 0.0 for e in EMOTIONS}

        # STRESSED: high pitch var, high RMS, fast speech
        if pitch_var > 2000:
            scores["stressed"] += 0.35
        if rms_mean > 0.12:
            scores["stressed"] += 0.25
        if speech_rate > 4.5:
            scores["stressed"] += 0.20

        # TIRED: low pitch, low RMS, slow speech
        if pitch_mean < 100:
            scores["tired"] += 0.30
        if rms_mean < 0.03:
            scores["tired"] += 0.25
        if speech_rate < 2.0:
            scores["tired"] += 0.25

        # EXCITED: high pitch, high energy, fast speech
        if pitch_mean > 200:
            scores["excited"] += 0.30
        if rms_mean > 0.10 and speech_rate > 4.0:
            scores["excited"] += 0.35

        # FRUSTRATED: clipped speech, high centroid, variable energy
        if centroid > 3500:
            scores["frustrated"] += 0.25
        if features.get("rms_var", 0) > 0.01:
            scores["frustrated"] += 0.20

        # FOCUSED: steady pitch, moderate energy, moderate speech rate
        if 80 < pitch_mean < 180 and 0.03 < rms_mean < 0.08 and 2.5 < speech_rate < 4.0:
            scores["focused"] += 0.40

        # Default to neutral
        scores["neutral"] = 0.1

        best = max(scores, key=lambda k: scores[k])
        confidence = scores[best]

        if confidence < _CONFIDENCE_THRESHOLD:
            return "neutral", confidence

        return best, confidence

    # ------------------------------------------------------------------ #
    #  Face emotion (every 90 seconds)                                    #
    # ------------------------------------------------------------------ #

    def _face_loop(self) -> None:
        self._running = True
        time.sleep(30)  # initial delay

        while self._running:
            try:
                self._scan_face()
            except Exception as e:
                print(f"[EmotionEngine] Face scan error: {e}")
            time.sleep(_FACE_INTERVAL)

    def _scan_face(self) -> None:
        if not _HAS_CV2 or not _HAS_FER:
            return

        try:
            # Initialize detector lazily
            if self._face_detector is None:
                self._face_detector = _FER(mtcnn=False)

            cap = _cv2.VideoCapture(0)
            if not cap.isOpened():
                return
            ret, frame = cap.read()
            cap.release()  # Release immediately

            if not ret or frame is None:
                return

            # Check face presence
            faces = self._face_detector.detect_emotions(frame)
            face_present = len(faces) > 0

            with self._lock:
                prev_face = self._face_present
                self._face_present = face_present

            # HUD dimming on face absence
            if not face_present and prev_face:
                if self._hud_dim_callback:
                    self._hud_dim_callback(True)
            elif face_present and not prev_face:
                if self._hud_dim_callback:
                    self._hud_dim_callback(False)

            if not faces:
                return

            # Get dominant emotion from first face
            emotions = faces[0].get("emotions", {})
            if emotions:
                dominant = max(emotions, key=lambda k: emotions[k])
                confidence = emotions[dominant]

                # Map FER emotion labels to our labels
                label_map = {
                    "happy": "excited",
                    "sad": "tired",
                    "angry": "frustrated",
                    "fear": "stressed",
                    "surprise": "excited",
                    "disgust": "frustrated",
                    "neutral": "neutral",
                }
                our_emotion = label_map.get(dominant, "neutral")

                with self._lock:
                    self._face_emotion = our_emotion
                    self._face_confidence = confidence

                self._update_combined_emotion()

        except Exception as e:
            print(f"[EmotionEngine] Face error: {e}")

    # ------------------------------------------------------------------ #
    #  Combine voice + face                                                #
    # ------------------------------------------------------------------ #

    def _update_combined_emotion(self) -> None:
        with self._lock:
            v_em = self._voice_emotion
            v_conf = self._voice_confidence
            f_em = self._face_emotion
            f_conf = self._face_confidence

        # Both agree → high confidence
        if v_em == f_em and v_em != "neutral":
            combined = v_em
            conf = (v_conf + f_conf) / 2
        # One very high confidence
        elif v_conf > 0.80:
            combined = v_em
            conf = v_conf
        elif f_conf > 0.80:
            combined = f_em
            conf = f_conf
        else:
            combined = "neutral"
            conf = 0.5

        with self._lock:
            self.current_emotion = combined
            self.emotion_confidence = conf

    @property
    def face_present(self) -> bool:
        with self._lock:
            return self._face_present

    def get_behavior(self) -> dict:
        return EMOTION_BEHAVIORS.get(self.current_emotion, EMOTION_BEHAVIORS["neutral"])
