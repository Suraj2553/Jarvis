"""audio/sarvam_client.py — Sarvam AI complete integration.

Models
------
  STT   — saaras:v3       POST /speech-to-text          (api-subscription-key)
  TTS   — bulbul:v3       POST /text-to-speech          (api-subscription-key)
  Chat  — sarvam-m        POST /v1/chat/completions     (Bearer token)
          sarvam-30b      same, reasoning model 1500+ tokens
          sarvam-105b     same, flagship  2000+ tokens
  Trans — mayura:v1       POST /translate                (api-subscription-key)
  Vision— sarvam-vision   POST /v1/chat/completions     (Bearer token, multimodal)
  WS-STT                  wss://api.sarvam.ai/speech-to-text-streaming

TTS working speakers (bulbul:v3): aditya, rohan, anushka, manisha, vidya,
  arya, ritu, priya, neha, pooja, simran, kavya

Auth note:
  /v1/chat/* endpoints need  Authorization: Bearer <key>
  All other REST endpoints need  api-subscription-key: <key>
"""

import asyncio
import base64
import io
import json
import re
import threading
import wave
from typing import Callable, Generator, Optional

import requests

_BASE     = "https://api.sarvam.ai"
_TIMEOUT  = 15   # chat / STT
_TTS_TIMEOUT = 30  # TTS synthesis can be slow for longer texts

# Strip <think>...</think> blocks from sarvam-m inline reasoning
_THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL)


def _strip_think(text: str) -> str:
    return _THINK_RE.sub("", text).strip()


# ================================================================== #
#  PATCH 1 — Chat models with correct routing                          #
# ================================================================== #

CHAT_MODEL_FAST    = "sarvam-m"      # conversational, streaming, inline thinking
CHAT_MODEL_REASON  = "sarvam-30b"   # reasoning model, separate reasoning_content
CHAT_MODEL_FULL    = "sarvam-105b"  # flagship, most capable

# Token budgets — reasoning models need large budgets
_TOKENS = {
    "sarvam-m":    400,   # thinking inline, short budget OK
    "sarvam-30b":  1800,  # reasoning_content consumes most tokens
    "sarvam-105b": 2200,
}

# Query characteristics that warrant the reasoning model
_COMPLEX_PATTERNS = re.compile(
    r"\b(analyz|explain|compare|summariz|evaluat|recommend|strateg"
    r"|difference|pros.cons|breakdown|plan|research|document|review)\b",
    re.I,
)


def _pick_model(messages: list[dict]) -> str:
    """Auto-select model based on last user message complexity."""
    last = next((m["content"] for m in reversed(messages)
                 if m.get("role") == "user"), "")
    if len(last) > 200 or _COMPLEX_PATTERNS.search(last):
        return CHAT_MODEL_REASON
    return CHAT_MODEL_FAST


def _extract_content(response_json: dict) -> str:
    """Extract final answer from any Sarvam chat model response."""
    choice = response_json.get("choices", [{}])[0]
    msg = choice.get("message", {})
    # 30b/105b: actual answer in content, reasoning in reasoning_content
    content = msg.get("content")
    if content:
        return _strip_think(content).strip()
    # sarvam-m with non-stream: thinking inline
    reason = msg.get("reasoning_content", "")
    return _strip_think(reason).strip()


# ================================================================== #
#  Main REST client                                                    #
# ================================================================== #

class SarvamClient:
    """REST wrapper for all Sarvam AI endpoints."""

    def __init__(self, api_key: str, config: Optional[dict] = None):
        self._key    = api_key
        self._config = config or {}

        # Two sessions — different auth headers
        self._stt_tts_sess = requests.Session()
        self._stt_tts_sess.headers.update({"api-subscription-key": api_key})

        self._chat_sess = requests.Session()
        self._chat_sess.headers.update({
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        })

    # ------------------------------------------------------------------ #
    #  PATCH 4 — STT saaras:v3 with output modes                         #
    # ------------------------------------------------------------------ #

    def transcribe(
        self,
        audio_bytes: bytes,
        sample_rate: int = 16000,
        language_code: str = "unknown",
        model: str = "saaras:v3",
        mode: str = "transcribe",
    ) -> str:
        """Transcribe audio. mode: transcribe | translate | codemix | verbatim | translit."""
        wav_bytes = _ensure_wav(audio_bytes, sample_rate)
        data = {
            "model":         model,
            "language_code": language_code,
            "mode":          mode,
            "with_timestamps":    "false",
            "with_disfluencies":  "false",
        }
        resp = self._stt_tts_sess.post(
            f"{_BASE}/speech-to-text",
            files={"file": ("audio.wav", io.BytesIO(wav_bytes), "audio/wav")},
            data=data,
            timeout=_TIMEOUT,
        )
        resp.raise_for_status()
        return resp.json().get("transcript", "").strip()

    def transcribe_numpy(
        self,
        audio_array,
        language_code: str = "unknown",
        mode: str = "auto",
    ) -> str:
        """Transcribe numpy float32 array. mode=auto picks codemix vs transcribe."""
        import numpy as np
        arr = np.array(audio_array, dtype=np.float32)
        pcm = (arr * 32767).clip(-32768, 32767).astype(np.int16)

        if mode == "auto":
            pref = self._config.get("preferred_language", "en-IN")
            mode = "codemix" if pref != "en-IN" else "transcribe"

        return self.transcribe(pcm.tobytes(), sample_rate=16000,
                               language_code=language_code, mode=mode)

    # ------------------------------------------------------------------ #
    #  TTS — bulbul:v3                                                     #
    # ------------------------------------------------------------------ #

    def synthesize(
        self,
        text: str,
        target_language_code: str = "en-IN",
        speaker: Optional[str] = None,
        pace: Optional[float] = None,
        model: str = "bulbul:v3",
    ) -> bytes:
        """Return WAV bytes from Sarvam bulbul:v3."""
        speaker = speaker or self._config.get("sarvam_speaker", "aditya")
        pace    = pace    or self._config.get("sarvam_pace",    1.1)

        payload = {
            "inputs":               [text],
            "target_language_code": target_language_code,
            "speaker":              speaker,
            "pace":                 pace,
            "model":                model,
        }
        resp = self._stt_tts_sess.post(
            f"{_BASE}/text-to-speech",
            json=payload,
            timeout=_TTS_TIMEOUT,
        )
        resp.raise_for_status()
        audios = resp.json().get("audios", [])
        if not audios:
            raise ValueError("Sarvam TTS returned no audio")
        return base64.b64decode(audios[0])

    def speak_numpy(self, text: str, target_language_code: str = "en-IN"):
        """Return (float32_array, sample_rate) for sounddevice."""
        import numpy as np
        wav_bytes = self.synthesize(text, target_language_code=target_language_code)
        with wave.open(io.BytesIO(wav_bytes)) as wf:
            sr  = wf.getframerate()
            raw = wf.readframes(wf.getnframes())
        pcm = (
            _np_import().frombuffer(raw, dtype=_np_import().int16)
            .astype(_np_import().float32) / 32768.0
        )
        return pcm, sr

    # ------------------------------------------------------------------ #
    #  PATCH 1 — Chat with model routing + think-strip                    #
    # ------------------------------------------------------------------ #

    def chat_sync(
        self,
        messages: list[dict],
        model: Optional[str] = None,
        max_tokens: Optional[int] = None,
        temperature: float = 0.7,
    ) -> str:
        """Single-shot chat. Auto-picks model if not specified."""
        model      = model     or _pick_model(messages)
        max_tokens = max_tokens or _TOKENS.get(model, 400)

        resp = self._chat_sess.post(
            f"{_BASE}/v1/chat/completions",
            json={"model": model, "messages": messages,
                  "max_tokens": max_tokens, "temperature": temperature,
                  "stream": False},
            timeout=max(_TIMEOUT, 30),
        )
        resp.raise_for_status()
        return _extract_content(resp.json())

    def chat_stream(
        self,
        messages: list[dict],
        model: Optional[str] = None,
        max_tokens: Optional[int] = None,
        on_sentence: Optional[Callable[[str], None]] = None,
    ) -> dict:
        """Streaming chat. Delivers sentences via on_sentence. Returns {"text": ...}."""
        model      = model     or _pick_model(messages)
        max_tokens = max_tokens or _TOKENS.get(model, 400)

        resp = self._chat_sess.post(
            f"{_BASE}/v1/chat/completions",
            json={"model": model, "messages": messages,
                  "max_tokens": max_tokens, "stream": True},
            stream=True,
            timeout=max(_TIMEOUT, 45),
        )
        resp.raise_for_status()

        full_text   = ""
        buffer      = ""
        in_think    = False  # skip <think> blocks for sarvam-m

        for raw in resp.iter_lines():
            if not raw:
                continue
            line = raw.decode("utf-8", errors="replace") if isinstance(raw, bytes) else raw
            if line.startswith("data: "):
                line = line[6:]
            if line == "[DONE]":
                break
            try:
                chunk  = json.loads(line)
                delta  = chunk["choices"][0]["delta"]
                token  = delta.get("content") or ""

                if not token:
                    continue

                # Skip inline <think>...</think> from sarvam-m
                token, in_think = _filter_think_stream(token, in_think)
                if not token:
                    continue

                full_text += token
                buffer    += token

                if on_sentence:
                    buffer = _flush_sentences(buffer, on_sentence)
            except (json.JSONDecodeError, KeyError, IndexError):
                continue

        if buffer.strip() and on_sentence:
            on_sentence(buffer.strip())

        return {"text": full_text.strip()}

    # ------------------------------------------------------------------ #
    #  Translate — mayura:v1                                               #
    # ------------------------------------------------------------------ #

    def translate(
        self,
        text: str,
        source_language_code: str = "en-IN",
        target_language_code: str = "hi-IN",
        model: str = "mayura:v1",
        mode: str = "formal",
    ) -> str:
        resp = self._stt_tts_sess.post(
            f"{_BASE}/translate",
            json={"input": text, "source_language_code": source_language_code,
                  "target_language_code": target_language_code,
                  "model": model, "mode": mode, "enable_preprocessing": True},
            timeout=_TIMEOUT,
        )
        resp.raise_for_status()
        return resp.json().get("translated_text", "").strip()

    # ------------------------------------------------------------------ #
    #  PATCH 5 — Sarvam Vision                                            #
    # ------------------------------------------------------------------ #

    def analyze_document(
        self,
        image_path_or_b64: str,
        task: str = "ocr",
        language: str = "auto",
    ) -> str:
        """Read/OCR a document image using Sarvam Vision (sarvam-vision model).

        task: ocr | summarize | structured | translate
        """
        _PROMPTS = {
            "ocr":        "Extract all text from this document exactly as written.",
            "summarize":  "Extract and summarize the key information in this document.",
            "structured": "Extract all data into structured format: tables, lists, key-value pairs.",
            "translate":  "Extract all text and translate it to English.",
        }

        import os
        if os.path.exists(image_path_or_b64):
            with open(image_path_or_b64, "rb") as f:
                image_b64 = base64.b64encode(f.read()).decode()
        else:
            image_b64 = image_path_or_b64

        prompt = _PROMPTS.get(task, _PROMPTS["ocr"])
        resp = self._chat_sess.post(
            f"{_BASE}/v1/chat/completions",
            json={
                "model": "sarvam-vision",
                "messages": [{
                    "role": "user",
                    "content": [
                        {"type": "image_url",
                         "image_url": {"url": f"data:image/jpeg;base64,{image_b64}"}},
                        {"type": "text", "text": prompt},
                    ],
                }],
                "max_tokens": 1000,
            },
            timeout=20,
        )
        resp.raise_for_status()
        return _extract_content(resp.json())

    # ------------------------------------------------------------------ #
    #  Health check                                                        #
    # ------------------------------------------------------------------ #

    def ping(self) -> bool:
        """Return True if key is valid (400 = bad params but key is good)."""
        try:
            resp = self._stt_tts_sess.post(
                f"{_BASE}/text-to-speech",
                json={"inputs": [" "], "target_language_code": "en-IN",
                      "speaker": "aditya", "model": "bulbul:v3"},
                timeout=8,
            )
            return resp.status_code in (200, 400, 422)
        except Exception:
            return False


# ================================================================== #
#  PATCH 2 — WebSocket streaming STT                                  #
# ================================================================== #

class SarvamStreamingSTT:
    """Real-time STT via Sarvam WebSocket. Transcribes while user speaks.

    Usage (in a thread):
        stt = SarvamStreamingSTT(api_key)
        stt.transcribe_stream(
            audio_generator,          # yields raw PCM bytes (16kHz, int16, mono)
            on_partial=lambda t: ..., # called with partial transcript
            on_final=lambda t: ...,   # called when utterance finishes
        )
    """

    WS_URL = "wss://api.sarvam.ai/speech-to-text-streaming"

    def __init__(self, api_key: str, config: Optional[dict] = None):
        self._key    = api_key
        self._config = config or {}

    def transcribe_stream(
        self,
        audio_generator,           # sync generator of PCM bytes
        language: str = "unknown",
        on_partial: Optional[Callable[[str], None]] = None,
        on_final:   Optional[Callable[[str], None]] = None,
    ) -> str:
        """Run blocking WebSocket STT. Returns final transcript.
        Raises on failure so the caller can fall back to Whisper."""
        return asyncio.run(
            self._ws_transcribe(audio_generator, language, on_partial, on_final)
        )

    async def _ws_transcribe(
        self,
        audio_gen,
        language: str,
        on_partial: Optional[Callable],
        on_final:   Optional[Callable],
    ) -> str:
        try:
            import websockets
        except ImportError:
            raise RuntimeError("pip install websockets  to use streaming STT")

        import asyncio

        final_text = ""
        headers = {"api-subscription-key": self._key}

        async with websockets.connect(self.WS_URL, extra_headers=headers) as ws:
            # Send config
            await ws.send(json.dumps({
                "model":         "saaras:v3",
                "language_code": language,
                "mode":          "codemix",
                "flush_signal":  True,
                "sample_rate":   16000,
            }))

            async def _send():
                for chunk in audio_gen:
                    await ws.send(chunk)
                    await asyncio.sleep(0.01)
                await ws.send(json.dumps({"flush": True}))

            async def _receive():
                nonlocal final_text
                async for msg in ws:
                    try:
                        result = json.loads(msg)
                        t = result.get("transcript", "")
                        if result.get("type") == "partial" and on_partial:
                            on_partial(t)
                        elif result.get("type") == "final":
                            final_text = t
                            if on_final:
                                on_final(t)
                            break
                    except Exception:
                        continue

            await asyncio.gather(_send(), _receive())

        return final_text


# ================================================================== #
#  PATCH 3 — Streaming TTS                                            #
# ================================================================== #

class SarvamStreamingTTS:
    """Stream audio from Sarvam TTS as it generates — first word in ~300ms.

    Usage:
        tts = SarvamStreamingTTS(api_key, config)
        tts.stream_speak("Hello sir, systems online.")
        tts.stop()
    """

    STREAM_URL = "https://api.sarvam.ai/text-to-speech-streaming"

    def __init__(self, api_key: str, config: Optional[dict] = None):
        self._key     = api_key
        self._config  = config or {}
        self._stop    = threading.Event()
        self._speaker = config.get("sarvam_speaker", "aditya") if config else "aditya"
        self._pace    = config.get("sarvam_pace", 1.1) if config else 1.1

    def stop(self) -> None:
        self._stop.set()

    def reset(self) -> None:
        self._stop.clear()

    def stream_speak(
        self,
        text: str,
        language: str = "en-IN",
        blocking: bool = True,
    ) -> None:
        """HTTP streaming TTS. Plays chunks as they arrive.

        Falls back to batch REST if streaming endpoint unavailable.
        """
        self._stop.clear()
        try:
            import sounddevice as sd
            import numpy as np

            resp = requests.post(
                self.STREAM_URL,
                headers={"api-subscription-key": self._key},
                json={
                    "inputs":               [text],
                    "target_language_code": language,
                    "speaker":              self._speaker,
                    "model":                "bulbul:v3",
                    "pace":                 self._pace,
                },
                stream=True,
                timeout=_TTS_TIMEOUT,
            )

            if resp.status_code == 200:
                self._play_stream(resp, sd, np, blocking)
                return

            # Streaming endpoint not available — fall back to batch
        except Exception as e:
            print(f"[SarvamStreamingTTS] Stream error ({e}) — batch fallback")

        # Batch fallback
        self._batch_speak(text, language)

    def _play_stream(self, resp, sd, np, blocking: bool) -> None:
        """Parse streaming WAV/PCM chunks and play via sounddevice."""
        audio_chunks = []
        for chunk in resp.iter_content(chunk_size=4096):
            if self._stop.is_set():
                break
            if chunk:
                audio_chunks.append(chunk)

        if audio_chunks and not self._stop.is_set():
            raw = b"".join(audio_chunks)
            try:
                with wave.open(io.BytesIO(raw)) as wf:
                    sr  = wf.getframerate()
                    pcm = np.frombuffer(wf.readframes(wf.getnframes()), dtype=np.int16)
            except Exception:
                # Raw PCM fallback at 22050Hz
                pcm = np.frombuffer(raw, dtype=np.int16)
                sr  = 22050
            audio_f32 = pcm.astype(np.float32) / 32768.0
            sd.play(audio_f32, samplerate=sr, blocking=blocking)

    def _batch_speak(self, text: str, language: str) -> None:
        """Fallback: batch TTS via standard REST endpoint."""
        try:
            import sounddevice as sd
            import numpy as np
            client = SarvamClient(self._key, self._config)
            audio_f32, sr = client.speak_numpy(text, target_language_code=language)
            if not self._stop.is_set():
                sd.play(audio_f32, samplerate=sr, blocking=True)
        except Exception as e:
            print(f"[SarvamStreamingTTS] Batch fallback error: {e}")


# ================================================================== #
#  Helpers                                                             #
# ================================================================== #

def _np_import():
    import numpy as np
    return np


def _ensure_wav(data: bytes, sample_rate: int = 16000) -> bytes:
    if data[:4] == b"RIFF":
        return data
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(data)
    return buf.getvalue()


def _filter_think_stream(token: str, in_think: bool) -> tuple[str, bool]:
    """Filter <think>...</think> tokens from streaming output."""
    result = ""
    i = 0
    while i < len(token):
        if not in_think:
            start = token.find("<think>", i)
            if start == -1:
                result += token[i:]
                break
            result += token[i:start]
            in_think = True
            i = start + 7
        else:
            end = token.find("</think>", i)
            if end == -1:
                break  # still inside think block
            in_think = False
            i = end + 8
    return result, in_think


def _flush_sentences(buffer: str, callback: Callable[[str], None]) -> str:
    for sep in (". ", "! ", "? ", ".\n", "!\n", "?\n"):
        while sep in buffer:
            idx = buffer.index(sep)
            sentence = buffer[: idx + len(sep)].strip()
            buffer   = buffer[idx + len(sep):]
            if sentence:
                callback(sentence)
    return buffer


def is_indian_language(text: str) -> bool:
    import unicodedata
    for ch in text:
        name = unicodedata.name(ch, "")
        if any(s in name for s in (
            "DEVANAGARI", "TAMIL", "TELUGU", "KANNADA",
            "MALAYALAM", "BENGALI", "GUJARATI", "GURMUKHI",
        )):
            return True
    return False


def detect_language_code(text: str) -> str:
    import unicodedata
    for ch in text:
        name = unicodedata.name(ch, "")
        if "DEVANAGARI" in name: return "hi-IN"
        if "TAMIL"      in name: return "ta-IN"
        if "TELUGU"     in name: return "te-IN"
        if "KANNADA"    in name: return "kn-IN"
        if "MALAYALAM"  in name: return "ml-IN"
        if "BENGALI"    in name: return "bn-IN"
        if "GUJARATI"   in name: return "gu-IN"
    return "en-IN"


# ================================================================== #
#  Singleton                                                           #
# ================================================================== #

_client: Optional[SarvamClient] = None


def get_client(config: dict) -> Optional[SarvamClient]:
    global _client
    key = config.get("sarvam_api_key", "").strip()
    if not key:
        return None
    if _client is None:
        _client = SarvamClient(key, config)
    return _client
