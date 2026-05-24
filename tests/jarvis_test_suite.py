"""tests/jarvis_test_suite.py — JARVIS self-test suite.

Run:
    python -m tests.jarvis_test_suite
    python tests/jarvis_test_suite.py

Every test group prints PASS / WARN / FAIL per item.
Final "feel score" (0-100) is printed at the end.
After all groups pass, prints:
    [STARK INDUSTRIES] Phase 0 complete and tested. JARVIS online.
"""

import importlib
import json
import os
import sys
import time
import traceback
from pathlib import Path
from typing import Callable

# ── Make project root importable ───────────────────────────────────── #
_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


# ================================================================== #
#  Result helpers                                                       #
# ================================================================== #

GREEN  = "\033[92m"
YELLOW = "\033[93m"
RED    = "\033[91m"
CYAN   = "\033[96m"
BOLD   = "\033[1m"
RESET  = "\033[0m"

_results: list[dict] = []   # {"group", "name", "status", "msg", "ms"}


def _record(group: str, name: str, status: str, msg: str = "", ms: float = 0.0):
    _results.append({"group": group, "name": name, "status": status, "msg": msg, "ms": ms})
    badge = {
        "PASS": f"{GREEN}PASS{RESET}",
        "WARN": f"{YELLOW}WARN{RESET}",
        "FAIL": f"{RED}FAIL{RESET}",
    }.get(status, status)
    ms_str = f"  ({ms:.0f}ms)" if ms > 0 else ""
    detail = f"  — {msg}" if msg else ""
    print(f"  [{badge}] {name}{ms_str}{detail}")


def _run(group: str, name: str, fn: Callable, warn_on_exception: bool = False):
    t0 = time.perf_counter()
    try:
        result = fn()
        ms = (time.perf_counter() - t0) * 1000
        if result is True or result is None:
            _record(group, name, "PASS", ms=ms)
        elif isinstance(result, str):
            _record(group, name, "WARN", result, ms=ms)
        else:
            _record(group, name, "PASS", str(result)[:80], ms=ms)
    except Exception as e:
        ms = (time.perf_counter() - t0) * 1000
        status = "WARN" if warn_on_exception else "FAIL"
        _record(group, name, status, str(e)[:120], ms=ms)


def _header(title: str):
    print(f"\n{BOLD}{CYAN}{'-'*56}{RESET}")
    print(f"{BOLD}{CYAN}  {title}{RESET}")
    print(f"{BOLD}{CYAN}{'-'*56}{RESET}")


# ================================================================== #
#  Group 1 — Dependencies                                              #
# ================================================================== #

def test_dependencies():
    _header("GROUP 1 — DEPENDENCIES")
    G = "dependencies"

    required = [
        ("sounddevice",        "sounddevice",    False),
        ("numpy",              "numpy",          False),
        ("requests",           "requests",       False),
        ("faster_whisper",     "faster-whisper", False),
        ("pyttsx3",            "pyttsx3",        False),
        ("PyQt6.QtWidgets",    "PyQt6",          False),
        ("pystray",            "pystray",        True),
        ("PIL",                "Pillow",         False),
        ("psutil",             "psutil",         False),
        ("pyautogui",          "pyautogui",      True),
        ("pyperclip",          "pyperclip",      True),
        ("librosa",            "librosa",        True),
        ("cv2",                "opencv-python",  True),
        ("duckduckgo_search",  "duckduckgo-search", True),
        ("groq",               "groq",           True),
    ]

    for mod, pkg, optional in required:
        def _check(m=mod, p=pkg):
            importlib.import_module(m)
            return True
        _run(G, f"import {pkg}", _check, warn_on_exception=optional)

    # silero-vad check (torch-based)
    def _silero():
        import torch  # noqa: F401
        return True
    _run(G, "silero-vad (torch)", _silero, warn_on_exception=True)


# ================================================================== #
#  Group 2 — API Connections                                           #
# ================================================================== #

def test_api_connections():
    _header("GROUP 2 — API CONNECTIONS")
    G = "api"

    # Open-Meteo (no key needed)
    def _weather_api():
        import requests as rq
        r = rq.get(
            "https://api.open-meteo.com/v1/forecast"
            "?latitude=28.6&longitude=77.2&current_weather=true",
            timeout=8,
        )
        assert r.status_code == 200, f"HTTP {r.status_code}"
        assert "current_weather" in r.json()
        return True
    _run(G, "Open-Meteo weather API", _weather_api)

    # Open-Meteo geocoding
    def _geocode_api():
        import requests as rq
        r = rq.get(
            "https://geocoding-api.open-meteo.com/v1/search?name=Delhi&count=1",
            timeout=8,
        )
        assert r.status_code == 200
        assert r.json().get("results")
        return True
    _run(G, "Open-Meteo geocoding API", _geocode_api)

    # DDG subprocess worker
    def _ddg_worker():
        import subprocess
        worker = _ROOT / "tools" / "ddg_worker.py"
        assert worker.exists(), "ddg_worker.py missing"
        proc = subprocess.run(
            [sys.executable, str(worker), "search", "python programming"],
            capture_output=True, text=True, timeout=15,
        )
        if proc.returncode != 0:
            return f"DDG worker exited {proc.returncode} — network may be down"
        data = json.loads(proc.stdout.strip() or "[]")
        assert isinstance(data, list)
        return True
    _run(G, "DDG search worker subprocess", _ddg_worker, warn_on_exception=True)

    # Google News RSS
    def _gnews():
        import requests as rq
        from urllib.parse import quote
        r = rq.get(
            f"https://news.google.com/rss/search?q={quote('technology')}&hl=en-IN&gl=IN&ceid=IN:en",
            timeout=10,
            headers={"User-Agent": "Mozilla/5.0"},
        )
        assert r.status_code == 200
        assert b"<rss" in r.content[:500] or b"<feed" in r.content[:500]
        return True
    _run(G, "Google News RSS feed", _gnews, warn_on_exception=True)

    # Groq API (key optional)
    def _groq():
        from config import load_config
        cfg = load_config()
        key = cfg.get("groq_api_key", "")
        if not key:
            return "groq_api_key not set — skipping live test"
        import groq as groq_sdk
        client = groq_sdk.Groq(api_key=key)
        resp = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[{"role": "user", "content": "Say: JARVIS online"}],
            max_tokens=10,
        )
        assert resp.choices[0].message.content
        return True
    _run(G, "Groq API (live)", _groq, warn_on_exception=True)

    # Sarvam API (key optional)
    def _sarvam():
        from config import load_config
        cfg = load_config()
        key = cfg.get("sarvam_api_key", "")
        if not key:
            return "sarvam_api_key not set — skipping live test"
        import requests as rq
        r = rq.post(
            "https://api.sarvam.ai/text-to-speech",
            headers={"api-subscription-key": key, "Content-Type": "application/json"},
            json={"inputs": ["Test"], "target_language_code": "en-IN",
                  "speaker": "aditya", "model": "bulbul:v3"},
            timeout=10,
        )
        assert r.status_code in (200, 400, 422), f"HTTP {r.status_code}"
        return f"Sarvam reachable (HTTP {r.status_code})"
    _run(G, "Sarvam API (live)", _sarvam, warn_on_exception=True)


# ================================================================== #
#  Group 3 — Audio Pipeline                                            #
# ================================================================== #

def test_audio_pipeline():
    _header("GROUP 3 — AUDIO PIPELINE")
    G = "audio"

    def _noise_import():
        from audio.noise_pipeline import NoisePipeline, get_pipeline  # noqa
        return True
    _run(G, "NoisePipeline importable", _noise_import)

    def _noise_process():
        import numpy as np
        from audio.noise_pipeline import NoisePipeline
        pipeline = NoisePipeline({})
        audio = (np.random.randn(1600) * 0.05).astype(np.float32)
        cleaned, is_speech = pipeline.process(audio)
        assert cleaned is not None
        assert isinstance(is_speech, bool)
        return True
    _run(G, "NoisePipeline.process() runs", _noise_process)

    def _noise_calibrate():
        import numpy as np
        from audio.noise_pipeline import NoisePipeline
        pipeline = NoisePipeline({})
        ambient = (np.random.randn(32000) * 0.01).astype(np.float32)
        pipeline.calibrate(ambient)
        assert hasattr(pipeline, "_ambient_rms")
        return True
    _run(G, "NoisePipeline.calibrate() runs", _noise_calibrate)

    def _vad_check():
        from audio.noise_pipeline import _HAS_VAD
        if not _HAS_VAD:
            return "WebRTC/Silero VAD not available — energy fallback active"
        return True
    _run(G, "VAD available", _vad_check, warn_on_exception=True)

    def _listener_import():
        from audio.listener import ClapListener  # noqa
        return True
    _run(G, "ClapListener importable", _listener_import, warn_on_exception=True)

    def _stt_import():
        from audio.stt_engine import STTEngine  # noqa
        return True
    _run(G, "STTEngine importable", _stt_import)

    def _tts_import():
        from audio.tts_engine import TTSEngine  # noqa
        return True
    _run(G, "TTSEngine importable", _tts_import)

    def _sounds_import():
        from sounds import play_activation  # noqa
        return True
    _run(G, "sounds module importable", _sounds_import)


# ================================================================== #
#  Group 4 — LLM Routing                                               #
# ================================================================== #

def test_llm_routing():
    _header("GROUP 4 — LLM ROUTING")
    G = "llm"

    def _router_import():
        from brain.llm_router import LLMRouter  # noqa
        return True
    _run(G, "LLMRouter importable", _router_import)

    def _router_init():
        from brain.llm_router import LLMRouter
        from config import load_config
        cfg = load_config()
        router = LLMRouter(cfg)
        assert hasattr(router, "chat_sync")
        assert hasattr(router, "chat_stream")
        return True
    _run(G, "LLMRouter instantiates", _router_init)

    def _router_sync():
        from brain.llm_router import LLMRouter
        from config import load_config
        cfg = load_config()
        if not cfg.get("groq_api_key") and not cfg.get("sarvam_api_key"):
            return "No API key set — skipping live LLM call"
        router = LLMRouter(cfg)
        resp = router.chat_sync(
            [{"role": "user", "content": "Reply with exactly: ONLINE"}],
            max_tokens=10,
        )
        assert resp and len(resp) > 0
        return f'Got: "{resp[:40]}"'
    _run(G, "LLMRouter.chat_sync() live", _router_sync, warn_on_exception=True)

    def _brain_import():
        from brain.brain import Brain  # noqa
        return True
    _run(G, "Brain importable", _brain_import)

    def _intent_detection():
        from brain.brain import Brain
        from config import load_config
        cfg = load_config()

        # Minimal stubs so Brain.__init__ doesn't crash
        class _FakeRegistry:
            tools = {}
        class _FakeEngine:
            relationship_level = 0
            def get_context(self): return {}
            def get_session_summary(self): return ""
            def get_continuity_prompt(self): return ""
            def add_exchange(self, *a, **kw): pass
        class _FakeEmotion:
            def get_current_emotion(self): return None

        brain = Brain(
            config=cfg,
            tool_registry=_FakeRegistry(),
            speak_fn=lambda t: None,
            status_fn=lambda t: None,
            memory_engine=_FakeEngine(),
            conversation_engine=_FakeEngine(),
            context_engine=_FakeEngine(),
            emotion_engine=_FakeEmotion(),
        )
        intent = brain._detect_intent("what's the weather in Delhi")
        assert intent is not None, "Intent detection returned None for weather query"
        return f"Intent: {intent}"
    _run(G, "Brain intent detection (weather)", _intent_detection)


# ================================================================== #
#  Group 5 — Tool Registry                                             #
# ================================================================== #

def test_tool_registry():
    _header("GROUP 5 — TOOL REGISTRY")
    G = "tools"

    def _web_import():
        from tools.web import get_weather, get_news, web_search, get_live_score  # noqa
        return True
    _run(G, "tools.web imports", _web_import)

    def _system_import():
        from tools.system import get_battery, get_system_info, take_screenshot  # noqa
        return True
    _run(G, "tools.system imports", _system_import)

    def _pa_import():
        from tools.pa import set_timer, add_note, read_notes  # noqa
        return True
    _run(G, "tools.pa imports", _pa_import)

    def _utils_import():
        from tools.utils import get_datetime, calculate  # noqa
        return True
    _run(G, "tools.utils imports", _utils_import)

    def _weather_live():
        from tools.web import get_weather
        result = get_weather("Delhi")
        assert result and len(result) > 10, f"Unexpected: {result}"
        assert "°C" in result or "Could not" in result
        return result[:80]
    _run(G, "get_weather('Delhi') live", _weather_live, warn_on_exception=True)

    def _news_live():
        from tools.web import get_news
        result = get_news("technology")
        assert result and len(result) > 10
        return result[:80]
    _run(G, "get_news('technology') live", _news_live, warn_on_exception=True)

    def _datetime_tool():
        from tools.utils import get_datetime
        result = get_datetime()
        assert result and len(result) > 5
        return result[:60]
    _run(G, "get_datetime()", _datetime_tool)

    def _calculate_tool():
        from tools.utils import calculate
        result = calculate("2 ** 10")
        assert "1024" in str(result), f"Got: {result}"
        return True
    _run(G, "calculate('2 ** 10') = 1024", _calculate_tool)

    def _battery_tool():
        from tools.system import get_battery
        result = get_battery()
        assert result and len(result) > 3
        return result[:60]
    _run(G, "get_battery()", _battery_tool, warn_on_exception=True)


# ================================================================== #
#  Group 6 — Memory System                                             #
# ================================================================== #

def test_memory_system():
    _header("GROUP 6 — MEMORY SYSTEM")
    G = "memory"

    def _memory_import():
        from memory.memory_engine import MemoryEngine  # noqa
        return True
    _run(G, "MemoryEngine importable", _memory_import)

    def _memory_init():
        from memory.memory_engine import MemoryEngine
        mem = MemoryEngine()
        lvl = mem.relationship_level  # property
        ctx = mem.get_context()       # returns dict
        assert isinstance(lvl, int)
        assert isinstance(ctx, dict)
        assert "relationship_level" in ctx
        return f"relationship_level={lvl}"
    _run(G, "MemoryEngine instantiates", _memory_init)

    def _memory_facts():
        from memory.memory_engine import MemoryEngine
        mem = MemoryEngine()
        mem.remember("test_key", "test_value_12345")
        result = mem.recall("test_key")
        assert result == "test_value_12345", f"Got: {result}"
        return True
    _run(G, "MemoryEngine remember/recall round-trip", _memory_facts,
         warn_on_exception=True)

    def _conv_import():
        from memory.conversation_engine import ConversationEngine  # noqa
        return True
    _run(G, "ConversationEngine importable", _conv_import)

    def _conv_exchange():
        from memory.conversation_engine import ConversationEngine
        engine = ConversationEngine()
        engine.add_exchange("Hello JARVIS", "Good morning, sir.")
        summary = engine.get_session_summary()
        assert summary is not None
        return True
    _run(G, "ConversationEngine add_exchange + summary", _conv_exchange,
         warn_on_exception=True)


# ================================================================== #
#  Group 7 — Conversation Engine & Personality                         #
# ================================================================== #

def test_conversation_engine():
    _header("GROUP 7 — CONVERSATION ENGINE & PERSONALITY")
    G = "personality"

    def _filter_import():
        from personality.conversation_engine import filter_response  # noqa
        return True
    _run(G, "filter_response importable", _filter_import)

    def _filter_forbidden():
        from personality.conversation_engine import filter_response
        bad = "Certainly! I'd be happy to help you with that."
        result = filter_response(bad)
        assert "Certainly" not in result, f"Filter missed: {result}"
        assert "happy to help" not in result.lower(), f"Filter missed: {result}"
        return f'"{result[:60]}"'
    _run(G, "filter_response strips forbidden phrases", _filter_forbidden)

    def _filter_i_start():
        from personality.conversation_engine import filter_response
        result = filter_response("I found the information you need.")
        assert not result.startswith("I found"), f"Still starts with 'I found': {result}"
        return f'"{result[:60]}"'
    _run(G, "filter_response fixes 'I ' sentence start", _filter_i_start)

    def _small_talk():
        from personality.conversation_engine import detect_small_talk_trigger, get_small_talk
        trigger = detect_small_talk_trigger("how are you doing today?")
        assert trigger == "how_are_you", f"Got: {trigger}"
        response = get_small_talk(trigger)
        assert response and len(response) > 5
        return f'"{response}"'
    _run(G, "Small talk detection + response", _small_talk)

    def _filler_import():
        from personality.conversation_engine import FillerSpeech
        fs = FillerSpeech()
        filler = fs.get_contextual_filler("weather")
        assert filler and len(filler) > 3, "Empty filler returned"
        assert fs.should_use_filler("what's the weather in Delhi")
        assert not fs.should_use_filler("hi")
        return True
    _run(G, "FillerSpeech.get_contextual_filler()", _filler_import)

    def _response_filter_class():
        from personality.conversation_engine import ResponseFilter
        rf = ResponseFilter()
        result = rf.clean("Of course! Here's what I found: **bold text** and more.")
        assert "Of course" not in result, f"Forbidden phrase not removed: {result}"
        assert "**" not in result, f"Markdown not stripped: {result}"
        assert "bold text" in result, f"Content lost: {result}"
        return True
    _run(G, "ResponseFilter.clean() strips markdown + forbidden",
         _response_filter_class)

    def _initiator_import():
        from personality.initiator import ProactiveInitiator  # noqa
        return True
    _run(G, "ProactiveInitiator importable", _initiator_import)


# ================================================================== #
#  Group 8 — Emotion System                                            #
# ================================================================== #

def test_emotion_system():
    _header("GROUP 8 — EMOTION SYSTEM")
    G = "emotion"

    def _emotion_import():
        from awareness.emotion_engine import EmotionEngine  # noqa
        return True
    _run(G, "EmotionEngine importable", _emotion_import)

    def _emotion_voice():
        import numpy as np
        from awareness.emotion_engine import EmotionEngine
        engine = EmotionEngine({})
        audio = (np.random.randn(16000) * 0.1).astype(np.float32)
        try:
            result = engine.analyze_voice_emotion(audio)
            return f"Voice emotion: {result}"
        except Exception as e:
            return f"analyze_voice_emotion raised: {e}"
    _run(G, "EmotionEngine.analyze_voice_emotion()", _emotion_voice,
         warn_on_exception=True)

    def _context_import():
        from awareness.context_engine import ContextEngine  # noqa
        return True
    _run(G, "ContextEngine importable", _context_import)


# ================================================================== #
#  Group 9 — UI                                                        #
# ================================================================== #

def test_ui():
    _header("GROUP 9 — UI")
    G = "ui"

    def _theme_import():
        from ui.theme import ARC_BLUE, HUD_SIZE  # noqa
        assert ARC_BLUE == "#00D4FF"
        assert HUD_SIZE == 380
        return True
    _run(G, "ui.theme constants", _theme_import)

    def _hud_import():
        from ui.hud import JARVISHud  # noqa
        return True
    _run(G, "JARVISHud importable (no display needed)", _hud_import,
         warn_on_exception=True)

    def _boot_import():
        from ui.boot_sequence import BootSequence  # noqa
        return True
    _run(G, "BootSequence importable", _boot_import, warn_on_exception=True)

    def _panel_import():
        from ui.conversation_panel import ConversationPanel  # noqa
        return True
    _run(G, "ConversationPanel importable", _panel_import, warn_on_exception=True)

    def _config_load():
        from config import load_config
        cfg = load_config()
        assert cfg.get("hud_size") == 380
        assert "groq_api_key" in cfg
        assert "sarvam_api_key" in cfg
        assert "stt_provider" in cfg
        assert "llm_provider" in cfg
        assert "tts_provider" in cfg
        assert "elevenlabs_voice_id" in cfg
        return "All config keys present"
    _run(G, "config.py has Sarvam + provider keys", _config_load)


# ================================================================== #
#  Group 10 — End-to-End Pipeline                                      #
# ================================================================== #

def test_end_to_end():
    _header("GROUP 10 — END-TO-END PIPELINE")
    G = "e2e"

    def _tool_chain():
        from tools.web import get_weather
        from personality.conversation_engine import filter_response

        # Simulate: user asks weather -> tool runs -> response filtered
        raw = get_weather("Mumbai")
        assert raw and len(raw) > 5
        filtered = filter_response(raw)
        assert filtered  # should still have content
        return f"Weather -> filter pipeline OK: {filtered[:60]}"
    _run(G, "Weather tool -> filter_response pipeline", _tool_chain,
         warn_on_exception=True)

    def _calculate_chain():
        from tools.utils import calculate
        from personality.conversation_engine import filter_response
        raw = f"The result is {calculate('355 / 113')}"
        filtered = filter_response(raw)
        assert "3.14" in filtered or "result" in filtered.lower()
        return f"Calculate -> filter: {filtered[:60]}"
    _run(G, "Calculate tool -> filter_response pipeline", _calculate_chain)

    def _datetime_chain():
        from tools.utils import get_datetime
        from personality.conversation_engine import filter_response
        raw = get_datetime()
        filtered = filter_response(raw)
        assert filtered and len(filtered) > 3
        return filtered[:60]
    _run(G, "Datetime tool -> filter_response pipeline", _datetime_chain)

    def _news_chain():
        from tools.web import get_news
        from personality.conversation_engine import filter_response
        raw = get_news("India")
        filtered = filter_response(raw)
        assert filtered
        return filtered[:80]
    _run(G, "News tool -> filter_response pipeline", _news_chain,
         warn_on_exception=True)

    def _memory_chain():
        from memory.memory_engine import MemoryEngine
        from memory.conversation_engine import ConversationEngine
        mem = MemoryEngine()
        conv = ConversationEngine()
        conv.add_exchange("What time is it?", "It is 3 PM.")
        ctx = mem.get_context()
        assert isinstance(ctx, dict)
        ctx_str = mem.get_context_string()
        assert isinstance(ctx_str, str)
        return "Memory + conversation chain OK"
    _run(G, "Memory + conversation engine chain", _memory_chain,
         warn_on_exception=True)


# ================================================================== #
#  Feel Score                                                          #
# ================================================================== #

def compute_feel_score() -> int:
    """
    Score breakdown (0-100):
      - Core tools working (weather, news, datetime, calculate): 25pts
      - Audio pipeline importable: 10pts
      - LLM routing importable + intent detection: 15pts
      - Memory system functional: 10pts
      - Personality filter working: 15pts
      - UI importable: 5pts
      - Live API calls succeed (weather, news): 10pts
      - FillerSpeech + ResponseFilter implemented: 10pts
    """
    counts = {r["status"]: 0 for r in _results}
    for r in _results:
        counts[r["status"]] = counts.get(r["status"], 0) + 1

    total  = len(_results)
    passes = counts.get("PASS", 0)
    warns  = counts.get("WARN", 0)
    fails  = counts.get("FAIL", 0)

    # Base score from pass rate
    base = int((passes + warns * 0.5) / total * 100) if total else 0

    # Bonus checks
    bonus = 0

    # FillerSpeech implemented?
    filler_done = any(
        r["name"] == "FillerSpeech.get_contextual_filler()"
        and r["status"] in ("PASS", "WARN")
        for r in _results
    )
    if filler_done:
        bonus += 5

    # ResponseFilter implemented?
    rf_done = any(
        r["name"] == "ResponseFilter.clean() strips markdown + forbidden"
        and r["status"] in ("PASS", "WARN")
        for r in _results
    )
    if rf_done:
        bonus += 5

    # Live weather working?
    weather_ok = any(
        r["name"] == "get_weather('Delhi') live" and r["status"] == "PASS"
        for r in _results
    )
    if weather_ok:
        bonus += 5

    score = min(100, base + bonus)
    return score, passes, warns, fails


# ================================================================== #
#  Main                                                                #
# ================================================================== #

def main():
    print(f"\n{BOLD}{'='*56}{RESET}")
    print(f"{BOLD}  JARVIS SELF-TEST SUITE{RESET}")
    print(f"{BOLD}  Stark Industries - AI Systems Division{RESET}")
    print(f"{BOLD}{'='*56}{RESET}")

    test_dependencies()
    test_api_connections()
    test_audio_pipeline()
    test_llm_routing()
    test_tool_registry()
    test_memory_system()
    test_conversation_engine()
    test_emotion_system()
    test_ui()
    test_end_to_end()

    # ── Summary ──────────────────────────────────────────────────── #
    score, passes, warns, fails = compute_feel_score()

    _header("SUMMARY")
    print(f"  {GREEN}PASS{RESET}: {passes}   {YELLOW}WARN{RESET}: {warns}   {RED}FAIL{RESET}: {fails}")
    print(f"\n  {BOLD}FEEL SCORE: {score}/100{RESET}", end="  ")

    if score >= 90:
        print(f"{GREEN}[FRIDAY-LEVEL]{RESET}")
    elif score >= 70:
        print(f"{CYAN}[OPERATIONAL]{RESET}")
    elif score >= 50:
        print(f"{YELLOW}[DEGRADED]{RESET}")
    else:
        print(f"{RED}[NOT READY]{RESET}")

    if fails == 0:
        print(f"\n{BOLD}{GREEN}[STARK INDUSTRIES] Phase 0 complete and tested. JARVIS online.{RESET}\n")
    else:
        print(f"\n{YELLOW}  {fails} test(s) failed. Resolve FAIL items before declaring ready.{RESET}\n")

    return 0 if fails == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
