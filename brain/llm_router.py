"""brain/llm_router.py — 5-way LLM router  v3.0

Priority cascade (auto mode, English)
──────────────────────────────────────
1. Groq          (cloud, fastest — badge: G)
2. Gemini Flash  (cloud, 2M context, free tier — badge: GF)
3. Sarvam        (cloud, Indian language native — badge: S)
4. NVIDIA NIM    (cloud, free credits, strong reasoning — badge: N)
5. OpenRouter    (cloud, multi-model fallback — badge: OR)
6. Ollama        (local, offline — badge: L)

Routing intelligence (Gemini suggestion)
─────────────────────────────────────────
• Speed-sensitive (conversation, tools)    → Groq
• Long context (docs, summaries, >4k tok)  → Gemini Flash
• Indian language input/output             → Sarvam → Groq → Ollama
• Heavy reasoning (code, analysis)         → NVIDIA NIM → Groq
• All others cascade in order above

Config keys
───────────
  llm_provider:         "auto" | "groq" | "gemini" | "sarvam" | "nvidia" | "openrouter" | "ollama"
  groq_api_key:         ""
  gemini_api_key:       ""      ← get free at aistudio.google.com
  nvidia_api_key:       ""      ← get free at build.nvidia.com
  openrouter_api_key:   ""      ← get free at openrouter.ai
  groq_model:           "llama-3.3-70b-versatile"
  gemini_model:         "gemini-1.5-flash"
  nvidia_model:         "meta/llama-3.1-70b-instruct"
  openrouter_model:     "mistralai/mistral-7b-instruct:free"
  llm_long_context_threshold: 3000   ← chars above which Gemini is preferred
"""

import json
import threading
import time
from typing import Callable, Optional

import requests
import re as _re


def _http_status(exc: Exception) -> int:
    """Extract HTTP status code from a requests.HTTPError, else 0."""
    resp = getattr(exc, "response", None)
    return getattr(resp, "status_code", 0) if resp else 0

# ── Indian language detection (unchanged from v2) ─────────────────── #
_INDIAN_RE = _re.compile(
    r"[ऀ-ॿ"   # Devanagari
    r"஀-௿"   # Tamil
    r"ఀ-౿"   # Telugu
    r"ಀ-೿"   # Kannada
    r"ഀ-ൿ"   # Malayalam
    r"ঀ-৿"   # Bengali
    r"઀-૿"   # Gujarati
    r"਀-੿]"  # Gurmukhi
)
_INDIAN_LANG_NAMES = (
    "hindi", "tamil", "telugu", "kannada", "malayalam",
    "bengali", "gujarati", "punjabi", "marathi", "urdu",
    "odia", "assamese", "sanskrit",
)
_LANG_REQUEST_RE = _re.compile(
    r"\b(?:speak|talk|reply|respond|answer|write|say|use|switch(?:\s+to)?|converse)\b"
    r"(?:\s+\w+){0,3}\s+(?:in\s+)?(" + "|".join(_INDIAN_LANG_NAMES) + r")\b"
    r"|\b(?:in|using)\s+(" + "|".join(_INDIAN_LANG_NAMES) + r")(?:\s+language)?\b",
    _re.IGNORECASE,
)

# ── Reasoning-heavy keywords (route to NVIDIA NIM) ────────────────── #
_REASONING_RE = _re.compile(
    r"\b(debug|analyse|analyze|explain why|step.?by.?step|write code|refactor|"
    r"algorithm|proof|derive|calculate|solve|architecture|design pattern)\b",
    _re.IGNORECASE,
)


def _is_indian_language(messages: list[dict]) -> bool:
    recent = [m for m in messages if m.get("role") == "user"][-4:]
    for m in recent:
        text = m.get("content", "")
        if _INDIAN_RE.search(text):
            return True
        if _LANG_REQUEST_RE.search(text):
            return True
    return False


def _is_long_context(messages: list[dict], threshold: int) -> bool:
    total = sum(len(m.get("content", "")) for m in messages)
    return total > threshold


def _is_reasoning_heavy(messages: list[dict]) -> bool:
    last_user = next(
        (m["content"] for m in reversed(messages) if m.get("role") == "user"), ""
    )
    return bool(_REASONING_RE.search(last_user))


class LLMRouter:
    # Provider constants
    GROQ       = "groq"
    GEMINI     = "gemini"
    SARVAM     = "sarvam"
    NVIDIA     = "nvidia"
    OPENROUTER = "openrouter"
    OLLAMA     = "ollama"

    BADGES = {
        GROQ: "G", GEMINI: "GF", SARVAM: "S",
        NVIDIA: "N", OPENROUTER: "OR", OLLAMA: "L",
    }

    def __init__(self, config: dict):
        self.config              = config
        self.current             = self.GROQ
        self.groq_healthy        = True
        self.gemini_healthy      = True
        self.sarvam_healthy      = True
        self.nvidia_healthy      = True
        self.openrouter_healthy  = True
        self._announced_fallback = False
        self._retry_timer: Optional[threading.Timer] = None
        self._lock                = threading.Lock()
        self._last_internet_check = 0.0
        self._internet_cache      = False
        self._sarvam_client       = None
        self._groq_session        = None   # persistent session for TCP reuse

    @property
    def backend_badge(self) -> str:
        return self.BADGES.get(self.current, "?")

    # ── Internet check ─────────────────────────────────────────────── #

    def internet_available(self) -> bool:
        now = time.monotonic()
        if now - self._last_internet_check < 10.0:
            return self._internet_cache
        try:
            requests.get("https://api.groq.com", timeout=2)
            self._internet_cache = True
        except Exception:
            self._internet_cache = False
        self._last_internet_check = now
        return self._internet_cache

    # ── Main entry ─────────────────────────────────────────────────── #

    def chat_stream(
        self,
        messages: list,
        tools:       Optional[list] = None,
        on_sentence: Optional[Callable[[str], None]] = None,
    ) -> dict:
        """Stream a chat response.  Returns {'text': ..., 'tool_calls': ...}."""

        pinned    = self.config.get("llm_provider", "auto").lower()
        indian    = _is_indian_language(messages)
        long_ctx  = _is_long_context(
            messages,
            self.config.get("llm_long_context_threshold", 3000)
        )
        reasoning = _is_reasoning_heavy(messages)
        _debug    = self.config.get("debug_llm_routing", False)

        def _has(r):
            return bool(r and (r.get("text") or r.get("tool_calls")))

        def _dbg(msg: str) -> None:
            if _debug:
                print(f"[LLMRouter] {msg}")

        # ── Pinned provider ────────────────────────────────────────── #
        if pinned == self.GROQ:
            _dbg("pinned → Groq")
            r = self._try_groq(messages, tools, on_sentence)
            return r if _has(r) else self._fallback_chain(messages, on_sentence, tools=tools, skip={self.GROQ})
        if pinned == self.GEMINI:
            _dbg("pinned → Gemini")
            r = self._try_gemini(messages, on_sentence, tools)
            return r if _has(r) else self._fallback_chain(messages, on_sentence, tools=tools, skip={self.GEMINI})
        if pinned == self.SARVAM:
            _dbg("pinned → Sarvam")
            r = self._try_sarvam(messages, on_sentence)
            return r if _has(r) else self._stream_ollama(messages, on_sentence)
        if pinned == self.NVIDIA:
            _dbg("pinned → NVIDIA")
            r = self._try_nvidia(messages, on_sentence, tools)
            return r if _has(r) else self._fallback_chain(messages, on_sentence, tools=tools, skip={self.NVIDIA})
        if pinned == self.OPENROUTER:
            _dbg("pinned → OpenRouter")
            r = self._try_openrouter(messages, on_sentence, tools)
            return r if _has(r) else self._stream_ollama(messages, on_sentence)
        if pinned == self.OLLAMA:
            _dbg("pinned → Ollama")
            with self._lock:
                self.current = self.OLLAMA
            return self._stream_ollama(messages, on_sentence)

        # ── Auto cascade ───────────────────────────────────────────── #

        # Indian language → Sarvam first (native Hindi/multilingual)
        if indian:
            _dbg("auto → Indian lang detected → Sarvam » Groq » Gemini » Ollama")
            for try_fn in [
                lambda: self._try_sarvam(messages, on_sentence),
                lambda: self._try_groq(messages, tools, on_sentence),
                lambda: self._try_gemini(messages, on_sentence, tools),
            ]:
                r = try_fn()
                if _has(r):
                    return r
            return self._stream_ollama(messages, on_sentence)

        # Tool call → Groq first (fastest function-calling)
        if tools:
            _dbg("auto → tool call → Groq » NVIDIA » Gemini » OpenRouter » Ollama")
            r = self._try_groq(messages, tools, on_sentence)
            if _has(r):
                return r
            for try_fn in [
                lambda: self._try_nvidia(messages, on_sentence, tools),
                lambda: self._try_gemini(messages, on_sentence, tools),
                lambda: self._try_openrouter(messages, on_sentence, tools),
            ]:
                r = try_fn()
                if _has(r):
                    return r
            return self._stream_ollama(messages, on_sentence)

        # Long context → Gemini first (2M context window)
        if long_ctx and self.config.get("gemini_api_key", ""):
            _dbg("auto → long context → Gemini first")
            r = self._try_gemini(messages, on_sentence, tools)
            if _has(r):
                return r

        # Reasoning heavy → NVIDIA first (strong instruction-following)
        if reasoning and self.config.get("nvidia_api_key", ""):
            _dbg("auto → reasoning detected → NVIDIA first")
            r = self._try_nvidia(messages, on_sentence, tools)
            if _has(r):
                return r

        # Default: Groq → Gemini → Sarvam → NVIDIA → OpenRouter → Ollama
        _dbg("auto → default cascade")
        return self._fallback_chain(messages, on_sentence, tools=tools, skip=set())

    def _fallback_chain(
        self,
        messages: list,
        on_sentence: Optional[Callable],
        tools: Optional[list] = None,
        skip: set = set(),
    ) -> dict:
        def _has(r):
            return bool(r and (r.get("text") or r.get("tool_calls")))

        if self.GROQ not in skip:
            r = self._try_groq(messages, tools, on_sentence)
            if _has(r):
                return r

        if self.GEMINI not in skip and self.config.get("gemini_api_key", ""):
            r = self._try_gemini(messages, on_sentence, tools)
            if _has(r):
                return r

        if self.SARVAM not in skip:
            r = self._try_sarvam(messages, on_sentence)
            if _has(r):
                return r

        if self.NVIDIA not in skip and self.config.get("nvidia_api_key", ""):
            r = self._try_nvidia(messages, on_sentence, tools)
            if _has(r):
                return r

        if self.OPENROUTER not in skip and self.config.get("openrouter_api_key", ""):
            r = self._try_openrouter(messages, on_sentence, tools)
            if _has(r):
                return r

        if not self._announced_fallback:
            self._announced_fallback = True
            if on_sentence:
                on_sentence("Running on local systems, sir.")

        with self._lock:
            self.current = self.OLLAMA
        return self._stream_ollama(messages, on_sentence)

    # ── Groq ────────────────────────────────────────────────────────── #

    def _try_groq(self, messages, tools, on_sentence):
        key = self.config.get("groq_api_key", "")
        if not key:
            return None
        if not self.groq_healthy:
            print("[LLMRouter] Groq → skipped (rate-limited, retry pending).")
            return None
        if not self.internet_available():
            return None
        try:
            with self._lock:
                self.current = self.GROQ
            return self._stream_groq(messages, tools, on_sentence)
        except Exception as exc:
            status = _http_status(exc)
            if status == 429:
                print("[LLMRouter] Groq rate-limited (429) — retrying in 15s.")
                with self._lock:
                    self.groq_healthy = False
                self._schedule_retry("groq", delay=15)
            elif status == 401:
                print("[LLMRouter] Groq auth error (401) — check GROQ_API_KEY.")
                with self._lock:
                    self.groq_healthy = False
            else:
                print(f"[LLMRouter] Groq failed ({type(exc).__name__}): {exc}")
                with self._lock:
                    self.groq_healthy = False
                self._schedule_retry("groq")
            return None

    def quick_complete(self, system: str, user: str, timeout: int = 8) -> str:
        """Non-streaming single completion — used for session summary on exit."""
        key = self.config.get("groq_api_key", "")
        if key and self.groq_healthy:
            try:
                payload = {
                    "model": self.config.get("groq_model", "llama-3.3-70b-versatile"),
                    "messages": [
                        {"role": "system", "content": system},
                        {"role": "user",   "content": user},
                    ],
                    "max_tokens": 40,
                    "temperature": 0.3,
                    "stream": False,
                }
                resp = requests.post(
                    "https://api.groq.com/openai/v1/chat/completions",
                    json=payload,
                    headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
                    timeout=timeout,
                )
                resp.raise_for_status()
                return resp.json()["choices"][0]["message"]["content"].strip()
            except Exception:
                pass
        # Gemini fallback
        gem_key = self.config.get("gemini_api_key", "")
        if gem_key:
            try:
                import google.generativeai as genai
                genai.configure(api_key=gem_key)
                model = genai.GenerativeModel(self.config.get("gemini_model", "gemini-2.0-flash"))
                resp = model.generate_content(f"{system}\n\n{user}")
                return resp.text.strip()
            except Exception:
                pass
        return ""

    def _warm_groq(self) -> None:
        """Pre-open a persistent TCP+TLS connection to Groq before STT finishes.

        Called from _on_speech_start in the VAD pipeline. The established TCP
        session is stored and reused by _stream_groq(), shaving ~300ms per turn.
        """
        key = self.config.get("groq_api_key", "")
        if not key or not self.groq_healthy or not self.internet_available():
            return
        try:
            if self._groq_session is None:
                import requests as _req
                sess = _req.Session()
                sess.headers.update({
                    "Authorization": f"Bearer {key}",
                    "Content-Type": "application/json",
                })
                # HEAD request opens TCP+TLS handshake without sending a payload
                sess.head("https://api.groq.com/openai/v1/models", timeout=4)
                self._groq_session = sess
        except Exception:
            self._groq_session = None

    def _stream_groq(self, messages, tools, on_sentence) -> dict:
        key   = self.config.get("groq_api_key", "")
        model = self.config.get("groq_model", "llama-3.3-70b-versatile")
        payload: dict = {
            "model":       model,
            "messages":    messages,
            "max_tokens":  512,
            "temperature": 0.8,
            "stream":      True,
        }
        if tools:
            payload["tools"]       = tools
            payload["tool_choice"] = "auto"

        # Reuse pre-warmed session if available (TCP connection pooling)
        if self._groq_session is not None:
            try:
                resp = self._groq_session.post(
                    "https://api.groq.com/openai/v1/chat/completions",
                    json=payload, stream=True, timeout=(4, 15),
                )
                resp.raise_for_status()
                return self._parse_openai_stream(resp, on_sentence)
            except Exception:
                self._groq_session = None  # session stale — fall through to fresh request

        resp = requests.post(
            "https://api.groq.com/openai/v1/chat/completions",
            headers={"Authorization": f"Bearer {key}",
                     "Content-Type": "application/json"},
            json=payload, stream=True, timeout=(4, 15),
        )
        resp.raise_for_status()
        return self._parse_openai_stream(resp, on_sentence)

    # ── Gemini Flash ────────────────────────────────────────────────── #

    def _try_gemini(self, messages, on_sentence, tools=None):
        key = self.config.get("gemini_api_key", "")
        if not key:
            return None
        if not self.gemini_healthy:
            print("[LLMRouter] Gemini → skipped (rate-limited, retry pending).")
            return None
        if not self.internet_available():
            return None
        try:
            with self._lock:
                self.current = self.GEMINI
            return self._stream_gemini(messages, on_sentence, tools)
        except Exception as exc:
            status = _http_status(exc)
            if status == 429:
                print("[LLMRouter] Gemini rate-limited (429) — retrying in 15s.")
                with self._lock:
                    self.gemini_healthy = False
                self._schedule_retry("gemini", delay=15)
            elif status == 401:
                print("[LLMRouter] Gemini auth error (401) — check GEMINI_API_KEY.")
                with self._lock:
                    self.gemini_healthy = False
            else:
                print(f"[LLMRouter] Gemini failed ({type(exc).__name__}): {exc}")
                with self._lock:
                    self.gemini_healthy = False
                self._schedule_retry("gemini")
            return None

    def _stream_gemini(self, messages, on_sentence, tools=None) -> dict:
        """Google Gemini via OpenAI-compatible endpoint (AI Studio).

        The /v1beta/openai/ compat layer accepts role=system in messages
        directly — do NOT use system_instruction (that's the native API only).
        """
        key   = self.config.get("gemini_api_key", "")
        model = self.config.get("gemini_model", "gemini-2.0-flash")

        payload: dict = {
            "model":    model,
            "messages": messages,   # pass as-is; system role is supported
            "stream":   True,
        }
        if tools:
            payload["tools"]       = tools
            payload["tool_choice"] = "auto"

        resp = requests.post(
            "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions",
            headers={"Authorization": f"Bearer {key}",
                     "Content-Type": "application/json"},
            json=payload, stream=True, timeout=(4, 15),
        )
        resp.raise_for_status()
        return self._parse_openai_stream(resp, on_sentence)

    # ── NVIDIA NIM ──────────────────────────────────────────────────── #

    def _try_nvidia(self, messages, on_sentence, tools=None):
        key = self.config.get("nvidia_api_key", "")
        if not key:
            return None
        if not self.nvidia_healthy:
            print("[LLMRouter] NVIDIA → skipped (rate-limited, retry pending).")
            return None
        if not self.internet_available():
            return None
        try:
            with self._lock:
                self.current = self.NVIDIA
            return self._stream_nvidia(messages, on_sentence, tools)
        except Exception as exc:
            status = _http_status(exc)
            if status == 429:
                print("[LLMRouter] NVIDIA rate-limited (429) — retrying in 15s.")
                with self._lock:
                    self.nvidia_healthy = False
                self._schedule_retry("nvidia", delay=15)
            elif status == 401:
                print("[LLMRouter] NVIDIA auth error (401) — check NVIDIA_API_KEY.")
                with self._lock:
                    self.nvidia_healthy = False
            else:
                print(f"[LLMRouter] NVIDIA NIM failed ({type(exc).__name__}): {exc}")
                with self._lock:
                    self.nvidia_healthy = False
                self._schedule_retry("nvidia")
            return None

    def _stream_nvidia(self, messages, on_sentence, tools=None) -> dict:
        key   = self.config.get("nvidia_api_key", "")
        model = self.config.get("nvidia_model", "meta/llama-3.1-70b-instruct")
        payload = {
            "model":       model,
            "messages":    messages,
            "max_tokens":  1024,
            "temperature": 0.7,
            "stream":      True,
        }
        if tools:
            payload["tools"]       = tools
            payload["tool_choice"] = "auto"
        resp = requests.post(
            "https://integrate.api.nvidia.com/v1/chat/completions",
            headers={"Authorization": f"Bearer {key}",
                     "Content-Type": "application/json"},
            json=payload, stream=True, timeout=(5, 15),
        )
        resp.raise_for_status()
        return self._parse_openai_stream(resp, on_sentence)

    # ── OpenRouter ──────────────────────────────────────────────────── #

    def _try_openrouter(self, messages, on_sentence, tools=None):
        key = self.config.get("openrouter_api_key", "")
        if not key:
            return None
        if not self.openrouter_healthy:
            print("[LLMRouter] OpenRouter → skipped (rate-limited, retry pending).")
            return None
        if not self.internet_available():
            return None
        try:
            with self._lock:
                self.current = self.OPENROUTER
            return self._stream_openrouter(messages, on_sentence, tools)
        except Exception as exc:
            status = _http_status(exc)
            if status == 429:
                print("[LLMRouter] OpenRouter rate-limited (429) — retrying in 15s.")
                with self._lock:
                    self.openrouter_healthy = False
                self._schedule_retry("openrouter", delay=15)
            elif status == 401:
                print("[LLMRouter] OpenRouter auth error (401) — check OPENROUTER_API_KEY.")
                with self._lock:
                    self.openrouter_healthy = False
            elif status == 404:
                _or_model = self.config.get("openrouter_model", "mistralai/mistral-7b-instruct:free")
                print(f"[LLMRouter] OpenRouter 404 — model not found: '{_or_model}'. "
                      "Set openrouter_model in config.json to a valid model.")
                with self._lock:
                    self.openrouter_healthy = False
                # No retry — 404 is a config error, not a transient one
            else:
                print(f"[LLMRouter] OpenRouter failed ({type(exc).__name__}): {exc}")
                with self._lock:
                    self.openrouter_healthy = False
                self._schedule_retry("openrouter")
            return None

    def _stream_openrouter(self, messages, on_sentence, tools=None) -> dict:
        key   = self.config.get("openrouter_api_key", "")
        model = self.config.get("openrouter_model", "mistralai/mistral-7b-instruct:free")
        # Ordered fallback models if primary model returns 404 (model not found)
        _FALLBACK_MODELS = [
            "meta-llama/llama-3.1-8b-instruct:free",
            "google/gemma-3-4b-it:free",
            "mistralai/mistral-7b-instruct:free",
        ]
        headers = {
            "Authorization": f"Bearer {key}",
            "Content-Type":  "application/json",
            "HTTP-Referer":  "https://jarvis.local",
            "X-Title":       "JARVIS",
        }
        for attempt_model in [model] + [m for m in _FALLBACK_MODELS if m != model]:
            payload = {
                "model":    attempt_model,
                "messages": messages,
                "stream":   True,
            }
            if tools:
                payload["tools"]       = tools
                payload["tool_choice"] = "auto"
            resp = requests.post(
                "https://openrouter.ai/api/v1/chat/completions",
                headers=headers, json=payload, stream=True, timeout=(4, 12),
            )
            if resp.status_code == 404:
                print(f"[LLMRouter] OpenRouter model '{attempt_model}' not found — trying next.")
                continue
            resp.raise_for_status()
            return self._parse_openai_stream(resp, on_sentence)
        raise RuntimeError("All OpenRouter fallback models returned 404.")

    # ── Sarvam ──────────────────────────────────────────────────────── #

    def _try_sarvam(self, messages, on_sentence):
        key = self.config.get("sarvam_api_key", "")
        if not key or not self.sarvam_healthy or not self.internet_available():
            return None
        try:
            with self._lock:
                self.current = self.SARVAM
            from audio.sarvam_client import SarvamClient
            if self._sarvam_client is None:
                self._sarvam_client = SarvamClient(key, self.config)
            result = self._sarvam_client.chat_stream(messages, on_sentence=on_sentence)
            return result or {"text": "", "tool_calls": None}
        except Exception as exc:
            print(f"[LLMRouter] Sarvam failed ({type(exc).__name__}): {exc}")
            with self._lock:
                self.sarvam_healthy  = False
                self._sarvam_client  = None
            return None

    # ── Ollama (local fallback) ──────────────────────────────────────  #

    def _stream_ollama(self, messages, on_sentence) -> dict:
        model    = self.config.get("ollama_model", "llama3.2")
        base_url = self.config.get("ollama_url",
                   self.config.get("ollama_host", "http://localhost:11434"))
        resp = requests.post(
            f"{base_url}/api/chat",
            json={"model": model, "messages": messages, "stream": True},
            stream=True, timeout=25,
        )
        resp.raise_for_status()
        full_text      = ""
        sentence_buffer = ""
        for raw_line in resp.iter_lines():
            if not raw_line:
                continue
            try:
                chunk   = json.loads(raw_line)
                content = chunk.get("message", {}).get("content", "")
                if content:
                    full_text      += content
                    sentence_buffer += content
                    if on_sentence:
                        sentence_buffer = _flush_sentences(sentence_buffer, on_sentence)
                if chunk.get("done"):
                    break
            except json.JSONDecodeError:
                pass
        if sentence_buffer.strip() and on_sentence:
            on_sentence(sentence_buffer.strip())
        return {"text": full_text, "tool_calls": None}

    # ── Shared OpenAI-compatible stream parser ───────────────────────  #

    def _parse_openai_stream(self, resp, on_sentence) -> dict:
        full_text      = ""
        sentence_buffer = ""
        tool_calls: list = []

        for raw_line in resp.iter_lines():
            if not raw_line:
                continue
            line = raw_line.decode("utf-8") if isinstance(raw_line, bytes) else raw_line
            if not line.startswith("data: "):
                continue
            data_str = line[6:]
            if data_str == "[DONE]":
                break
            try:
                chunk  = json.loads(data_str)
                choice = chunk.get("choices", [{}])[0]
                delta  = choice.get("delta", {})

                for tc in delta.get("tool_calls", []):
                    idx = tc.get("index", 0)
                    while len(tool_calls) <= idx:
                        tool_calls.append(
                            {"id": "", "type": "function",
                             "function": {"name": "", "arguments": ""}}
                        )
                    if "id" in tc:
                        tool_calls[idx]["id"] = tc["id"]
                    fn = tc.get("function", {})
                    if "name" in fn:
                        tool_calls[idx]["function"]["name"] += fn["name"]
                    if "arguments" in fn:
                        tool_calls[idx]["function"]["arguments"] += fn["arguments"]

                content = delta.get("content") or ""
                if content:
                    full_text       += content
                    sentence_buffer += content
                    if on_sentence:
                        sentence_buffer = _flush_sentences(sentence_buffer, on_sentence)
            except (json.JSONDecodeError, KeyError, IndexError):
                pass

        if sentence_buffer.strip() and on_sentence:
            on_sentence(sentence_buffer.strip())

        return {
            "text":       full_text,
            "tool_calls": tool_calls if tool_calls else None,
        }

    # ── Non-streaming sync call ──────────────────────────────────────  #

    def chat_sync(self, messages: list, max_tokens: int = 200) -> str:
        for key_field, url, model_field, default_model in [
            ("groq_api_key",       "https://api.groq.com/openai/v1/chat/completions",
             "groq_model",         "llama-3.3-70b-versatile"),
            ("gemini_api_key",     "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions",
             "gemini_model",       "gemini-1.5-flash"),
            ("nvidia_api_key",     "https://integrate.api.nvidia.com/v1/chat/completions",
             "nvidia_model",       "meta/llama-3.1-70b-instruct"),
            ("openrouter_api_key", "https://openrouter.ai/api/v1/chat/completions",
             "openrouter_model",   "mistralai/mistral-7b-instruct:free"),
        ]:
            api_key = self.config.get(key_field, "")
            if not api_key or not self.internet_available():
                continue
            try:
                headers = {"Authorization": f"Bearer {api_key}",
                           "Content-Type": "application/json"}
                if key_field == "openrouter_api_key":
                    headers["HTTP-Referer"] = "https://jarvis.local"
                    headers["X-Title"]      = "JARVIS"
                resp = requests.post(url, headers=headers, json={
                    "model":       self.config.get(model_field, default_model),
                    "messages":    messages,
                    "max_tokens":  max_tokens,
                    "temperature": 0.7,
                    "stream":      False,
                }, timeout=8)
                resp.raise_for_status()
                return resp.json()["choices"][0]["message"]["content"].strip()
            except Exception:
                continue

        # Local fallback
        try:
            base = self.config.get("ollama_url",
                   self.config.get("ollama_host", "http://localhost:11434"))
            resp = requests.post(f"{base}/api/chat", json={
                "model":    self.config.get("ollama_model", "llama3.2"),
                "messages": messages,
                "stream":   False,
            }, timeout=30)
            resp.raise_for_status()
            return resp.json()["message"]["content"].strip()
        except Exception:
            return ""

    # ── Retry scheduler ─────────────────────────────────────────────── #

    def _schedule_retry(self, provider: str, delay: int = 60) -> None:
        def _retry():
            self._last_internet_check = 0.0
            if self.internet_available():
                with self._lock:
                    setattr(self, f"{provider}_healthy", True)
                    self._announced_fallback = False
                print(f"[LLMRouter] {provider.title()} back online.")
            else:
                self._schedule_retry(provider, delay)
        t = threading.Timer(delay, _retry)
        t.daemon = True
        t.start()


# ── Sentence boundary helper ─────────────────────────────────────────  #

def _flush_sentences(buffer: str, callback: Callable[[str], None]) -> str:
    for sep in (". ", "! ", "? ", ".\n", "!\n", "?\n"):
        while sep in buffer:
            idx      = buffer.index(sep)
            sentence = buffer[: idx + len(sep)].strip()
            buffer   = buffer[idx + len(sep):]
            if sentence:
                callback(sentence)
    return buffer
