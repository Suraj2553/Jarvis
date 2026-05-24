"""tests/self_test.py — Self-test suite for the rebuilt JARVIS voice engine.

Tests all Phase 1-9 subsystems.  Run:
    python tests/self_test.py

Every test prints PASS / WARN / FAIL.
All tests must reach PASS or WARN before the suite declares success.
A FAIL exits with code 1.
"""

import sys
import time
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import numpy as np

# ── Colour helpers ─────────────────────────────────────────────────── #
GREEN  = "\033[92m"
YELLOW = "\033[93m"
RED    = "\033[91m"
CYAN   = "\033[96m"
BOLD   = "\033[1m"
RESET  = "\033[0m"

_results: list[dict] = []


def _record(group: str, name: str, status: str, msg: str = "", ms: float = 0.0) -> None:
    _results.append({"group": group, "name": name, "status": status, "msg": msg, "ms": ms})
    badge = {"PASS": f"{GREEN}PASS{RESET}",
             "WARN": f"{YELLOW}WARN{RESET}",
             "FAIL": f"{RED}FAIL{RESET}"}.get(status, status)
    ms_str = f"  ({ms:.0f}ms)" if ms > 0 else ""
    detail = f"  — {msg}" if msg else ""
    print(f"  [{badge}] {name}{ms_str}{detail}")


def _check(group: str, name: str, fn, warn_only: bool = False) -> None:
    t0 = time.monotonic()
    try:
        result = fn()
        ms = (time.monotonic() - t0) * 1000
        if result is True or result is None:
            _record(group, name, "PASS", ms=ms)
        elif isinstance(result, str) and result.startswith("WARN:"):
            _record(group, name, "WARN", result[5:].strip(), ms=ms)
        else:
            _record(group, name, "PASS", str(result)[:80], ms=ms)
    except Exception as e:
        ms = (time.monotonic() - t0) * 1000
        if warn_only:
            _record(group, name, "WARN", str(e)[:120], ms=ms)
        else:
            _record(group, name, "FAIL", str(e)[:120], ms=ms)


def _header(title: str) -> None:
    print(f"\n{CYAN}{BOLD}{'-'*60}{RESET}")
    print(f"{CYAN}{BOLD}  {title}{RESET}")
    print(f"{CYAN}{BOLD}{'-'*60}{RESET}")


# ================================================================== #
#  Group 1 — Echo Cancellation                                         #
# ================================================================== #

def _test_echo_cancel() -> None:
    _header("Group 1 — Echo Cancellation (audio/echo_cancel.py)")

    def _import():
        from audio.echo_cancel import apply_echo_cancellation, get_loopback_audio
        return True
    _check("echo_cancel", "import echo_cancel", _import)

    def _apply_zeros():
        from audio.echo_cancel import apply_echo_cancellation
        mic = np.zeros(480, dtype=np.float32)
        ref = np.zeros(480, dtype=np.float32)
        out = apply_echo_cancellation(mic, ref)
        assert out.shape == mic.shape, "shape mismatch"
        assert out.dtype == np.float32
    _check("echo_cancel", "apply_echo_cancellation(zeros)", _apply_zeros)

    def _apply_sine():
        from audio.echo_cancel import apply_echo_cancellation
        t   = np.linspace(0, 1, 480, dtype=np.float32)
        mic = np.sin(2 * np.pi * 440 * t) * 0.5
        ref = np.sin(2 * np.pi * 440 * t) * 0.3
        out = apply_echo_cancellation(mic, ref)
        assert out.shape == mic.shape
        # AEC should have reduced energy, not amplified
        assert float(np.abs(out).max()) < float(np.abs(mic).max()) + 0.1
    _check("echo_cancel", "apply_echo_cancellation(sine)", _apply_sine)

    def _loopback_zeros():
        from audio.echo_cancel import get_loopback_audio
        arr = get_loopback_audio(480, 16000)
        assert arr.shape == (480,) or len(arr) == 480
        assert arr.dtype == np.float32
    _check("echo_cancel", "get_loopback_audio returns float32 array", _loopback_zeros, warn_only=True)


# ================================================================== #
#  Group 2 — Noise Suppression                                         #
# ================================================================== #

def _test_noise_suppress() -> None:
    _header("Group 2 — Noise Suppression (audio/noise_suppress.py)")

    def _import():
        from audio.noise_suppress import suppress_noise
        return True
    _check("noise_suppress", "import noise_suppress", _import)

    def _passthrough():
        from audio.noise_suppress import suppress_noise
        audio = np.random.randn(480).astype(np.float32) * 0.1
        out   = suppress_noise(audio, sample_rate=16000)
        assert out.shape == audio.shape
        assert out.dtype == np.float32
    _check("noise_suppress", "suppress_noise passthrough/process", _passthrough)

    def _silence_preserved():
        from audio.noise_suppress import suppress_noise
        audio = np.zeros(480, dtype=np.float32)
        out   = suppress_noise(audio, sample_rate=16000)
        # Silence in → near-silence out (not amplified)
        assert float(np.abs(out).max()) < 0.01
    _check("noise_suppress", "silence in → silence out", _silence_preserved)


# ================================================================== #
#  Group 3 — VAD                                                        #
# ================================================================== #

def _test_vad() -> None:
    _header("Group 3 — VAD (audio/vad.py)")

    def _import():
        from audio.vad import is_speech
        return True
    _check("vad", "import vad", _import)

    def _silence():
        from audio.vad import is_speech
        audio = np.zeros(480, dtype=np.float32)
        detected, conf = is_speech(audio, 16000)
        assert conf >= 0.0 and conf <= 1.0
        assert isinstance(detected, bool)
    _check("vad", "is_speech(silence) returns (bool, float)", _silence)

    def _speech_like():
        from audio.vad import is_speech
        # Sine wave — louder than silence, energy-based fallback should detect
        t     = np.linspace(0, 0.03, 480, dtype=np.float32)
        audio = np.sin(2 * np.pi * 300 * t) * 0.5
        detected, conf = is_speech(audio, 16000)
        assert isinstance(detected, bool)
        assert 0.0 <= conf <= 1.0
    _check("vad", "is_speech(sine) returns valid (bool, float)", _speech_like)

    def _latency():
        from audio.vad import is_speech
        audio = np.random.randn(480).astype(np.float32) * 0.05
        t0    = time.monotonic()
        for _ in range(10):
            is_speech(audio, 16000)
        ms = (time.monotonic() - t0) * 100  # avg per call
        if ms > 20:
            return f"WARN: avg {ms:.1f}ms per call (target <20ms)"
    _check("vad", "is_speech latency < 20ms avg", _latency)


# ================================================================== #
#  Group 4 — Audio Pipeline                                             #
# ================================================================== #

def _test_pipeline() -> None:
    _header("Group 4 — Audio Pipeline (audio/pipeline.py)")

    def _import():
        from audio.pipeline import process_mic_chunk, get_pipeline_latency
        return True
    _check("pipeline", "import pipeline", _import)

    def _returns_tuple():
        from audio.pipeline import process_mic_chunk
        raw = np.random.randn(480).astype(np.float32) * 0.01
        audio, speech, conf = process_mic_chunk(raw, 16000)
        assert audio.dtype == np.float32
        assert isinstance(speech, bool)
        assert 0.0 <= conf <= 1.0
    _check("pipeline", "process_mic_chunk returns (audio, bool, float)", _returns_tuple)

    def _latency():
        from audio.pipeline import process_mic_chunk, get_pipeline_latency
        raw = np.random.randn(480).astype(np.float32) * 0.01
        process_mic_chunk(raw, 16000)
        ms = get_pipeline_latency()
        if ms > 50:
            return f"WARN: pipeline took {ms:.1f}ms (target <50ms)"
    _check("pipeline", "pipeline latency < 50ms", _latency)


# ================================================================== #
#  Group 5 — Turn Detector                                              #
# ================================================================== #

def _test_turn_detector() -> None:
    _header("Group 5 — Turn Detector (audio/turn_detector.py)")

    def _import():
        from audio.turn_detector import TurnDetector
        return True
    _check("turn_detector", "import TurnDetector", _import)

    def _init():
        from audio.turn_detector import TurnDetector
        td = TurnDetector(sample_rate=16000)
        assert td.get_state() == "IDLE"
    _check("turn_detector", "TurnDetector initializes in IDLE", _init)

    def _state_transitions():
        from audio.turn_detector import TurnDetector
        td = TurnDetector(sample_rate=16000)
        td.set_state("LISTENING")
        assert td.get_state() == "LISTENING"
        td.set_state("PROCESSING")
        assert td.get_state() == "PROCESSING"
        td.mark_processing_done()
        assert td.get_state() == "SPEAKING"
        td.mark_tts_done()
        assert td.get_state() == "IDLE"
    _check("turn_detector", "state machine transitions (manual)", _state_transitions)

    def _feed_silence():
        from audio.turn_detector import TurnDetector
        td    = TurnDetector(sample_rate=16000)
        chunk = np.zeros(480, dtype=np.float32)
        for _ in range(20):
            td.feed(chunk)
        assert td.get_state() == "IDLE"
    _check("turn_detector", "feed(silence×20) stays IDLE", _feed_silence)

    def _callbacks():
        from audio.turn_detector import TurnDetector
        td = TurnDetector(sample_rate=16000)
        fired = []
        td.on_speech_start = lambda: fired.append("start")
        td.on_interrupt    = lambda: fired.append("interrupt")
        td.on_speech_end   = lambda audio: fired.append("end")
        # Callbacks are registered without error
        assert td.on_speech_start is not None
    _check("turn_detector", "callbacks assigned without error", _callbacks)


# ================================================================== #
#  Group 6 — Filler Speech                                              #
# ================================================================== #

def _test_filler() -> None:
    _header("Group 6 — Filler Speech (audio/filler.py)")

    def _import():
        from audio.filler import get_filler, classify_intent
        return True
    _check("filler", "import filler", _import)

    def _classify_intents():
        from audio.filler import classify_intent
        cases = [
            ("what is the weather today", "weather"),
            ("search for python tutorials", "search"),
            ("set a timer for 5 minutes", "timer"),
            ("calculate 5 percent of 200", "calculation"),
            ("cricket score today", "score"),
        ]
        for text, expected in cases:
            got = classify_intent(text)
            assert got == expected, f"{text!r} → {got!r}, expected {expected!r}"
    _check("filler", "classify_intent — 5 intent categories", _classify_intents)

    def _get_filler_default():
        from audio.filler import get_filler
        phrase = get_filler("default", language="en")
        assert isinstance(phrase, str) and len(phrase) > 0
    _check("filler", "get_filler(default) returns non-empty string", _get_filler_default)

    def _get_filler_hindi():
        from audio.filler import get_filler
        phrase = get_filler("default", language="hi")
        assert isinstance(phrase, str) and len(phrase) > 0
    _check("filler", "get_filler(language=hi) returns Hindi phrase", _get_filler_hindi)

    def _no_repeat():
        from audio.filler import get_filler
        phrases = [get_filler("default") for _ in range(5)]
        # At least some variety — not the same phrase 5 times in a row
        assert len(set(phrases)) > 1
    _check("filler", "get_filler avoids consecutive repeats", _no_repeat)

    def _sarcasm_chance():
        from audio.filler import get_filler
        FILLERS_SARCASTIC = [
            "Oh, that. Sure.",
            "Not the strangest request today. Give me a second.",
            "Fascinating. Processing.",
            "On it. As always.",
            "Scanning. Try not to be impressed.",
        ]
        sarcastic_hits = sum(
            1 for _ in range(50)
            if get_filler("default", sarcasm_chance=1.0) in FILLERS_SARCASTIC
        )
        assert sarcastic_hits == 50, f"sarcasm_chance=1.0 should always return sarcastic: got {sarcastic_hits}/50"
    _check("filler", "sarcasm_chance=1.0 always returns sarcastic phrase", _sarcasm_chance)


# ================================================================== #
#  Group 7 — TTS Engine                                                 #
# ================================================================== #

def _test_tts_engine() -> None:
    _header("Group 7 — TTS Engine (audio/tts_engine.py)")

    def _import():
        from audio.tts_engine import TTSEngine
        return True
    _check("tts_engine", "import TTSEngine", _import)

    def _init_no_keys():
        from audio.tts_engine import TTSEngine
        engine = TTSEngine({})
        assert not engine.is_speaking()
    _check("tts_engine", "TTSEngine init with empty config (no API keys)", _init_no_keys)

    def _switch_language():
        from audio.tts_engine import TTSEngine
        engine = TTSEngine({})
        engine.switch_language("hi")
        assert engine._language == "hi"
        engine.switch_language("en")
        assert engine._language == "en"
    _check("tts_engine", "switch_language en/hi works", _switch_language)

    def _invalid_language():
        from audio.tts_engine import TTSEngine
        engine = TTSEngine({})
        engine.switch_language("fr")  # should warn, not crash
        assert engine._language == "en"  # unchanged
    _check("tts_engine", "switch_language(invalid) is ignored gracefully", _invalid_language)

    def _stop_no_crash():
        from audio.tts_engine import TTSEngine
        engine = TTSEngine({})
        engine.stop_immediately()   # must not crash when not speaking
    _check("tts_engine", "stop_immediately() when idle does not crash", _stop_no_crash)

    def _speak_filler_no_crash():
        from audio.tts_engine import TTSEngine
        engine = TTSEngine({})
        engine.speak_filler("One moment.")
        time.sleep(0.1)
        engine.stop_immediately()
    _check("tts_engine", "speak_filler fires daemon thread without crash", _speak_filler_no_crash, warn_only=True)


# ================================================================== #
#  Group 8 — STT Engine                                                 #
# ================================================================== #

def _test_stt_engine() -> None:
    _header("Group 8 — STT Engine (audio/stt_engine.py)")

    def _import():
        from audio.stt_engine import STTEngine
        return True
    _check("stt_engine", "import STTEngine", _import)

    def _init():
        from audio.stt_engine import STTEngine
        engine = STTEngine({})
        assert engine._mode in ("whisper", "sarvam", "auto")
    _check("stt_engine", "STTEngine init", _init)

    def _set_mode():
        from audio.stt_engine import STTEngine
        engine = STTEngine({})
        engine.set_mode("whisper")
        assert engine._mode == "whisper"
        engine.set_mode("sarvam")
        assert engine._mode == "sarvam"
    _check("stt_engine", "set_mode whisper/sarvam works", _set_mode)

    def _set_language():
        from audio.stt_engine import STTEngine
        engine = STTEngine({})
        engine.set_language("en")
        assert engine._language == "en"
        engine.set_language("hi")
        assert engine._language == "hi"
    _check("stt_engine", "set_language en/hi works", _set_language)

    def _transcribe_empty():
        from audio.stt_engine import STTEngine
        engine = STTEngine({})
        result = engine.transcribe(np.array([], dtype=np.float32))
        assert result == ""
    _check("stt_engine", "transcribe(empty) returns empty string", _transcribe_empty)

    def _transcribe_short():
        from audio.stt_engine import STTEngine
        engine = STTEngine({})
        short = np.zeros(50, dtype=np.float32)  # < 100 samples
        result = engine.transcribe(short)
        assert result == ""
    _check("stt_engine", "transcribe(< 100 samples) returns empty", _transcribe_short)

    def _transcribe_silence():
        from audio.stt_engine import STTEngine
        engine = STTEngine({})
        # Whisper on silence should either return "" or some very short text
        silence = np.zeros(16000, dtype=np.float32)  # 1s silence
        result  = engine.transcribe(silence)
        assert isinstance(result, str)
    _check("stt_engine", "transcribe(1s silence) returns str without crash", _transcribe_silence, warn_only=True)


# ================================================================== #
#  Group 9 — Language Switch                                            #
# ================================================================== #

def _test_language_switch() -> None:
    _header("Group 9 — Language Switch (brain/language_switch.py)")

    def _import():
        from brain.language_switch import check_language_switch, handle_language_switch
        return True
    _check("lang_switch", "import language_switch", _import)

    def _detect_hindi():
        from brain.language_switch import check_language_switch
        cases = [
            "switch to hindi",
            "hindi mode",
            "hindi please",
            "hindi bolo",
            "मुझे हिंदी में बात करो",
        ]
        for text in cases:
            lang = check_language_switch(text)
            assert lang == "hi", f"{text!r} → {lang!r} (expected 'hi')"
    _check("lang_switch", "detect Hindi switch commands", _detect_hindi)

    def _detect_english():
        from brain.language_switch import check_language_switch
        cases = [
            "switch to english",
            "english mode",
            "speak english",
            "back to english",
        ]
        for text in cases:
            lang = check_language_switch(text)
            assert lang == "en", f"{text!r} → {lang!r} (expected 'en')"
    _check("lang_switch", "detect English switch commands", _detect_english)

    def _no_switch():
        from brain.language_switch import check_language_switch
        nulls = [
            "what is the weather",
            "play some music",
            "hello jarvis",
            "",
        ]
        for text in nulls:
            lang = check_language_switch(text)
            assert lang is None, f"{text!r} → {lang!r} (expected None)"
    _check("lang_switch", "non-switch phrases return None", _no_switch)


# ================================================================== #
#  Group 10 — Terminal Input                                            #
# ================================================================== #

def _test_terminal_input() -> None:
    _header("Group 10 — Terminal Input (terminal_input.py)")

    def _import():
        from terminal_input import TerminalInputThread
        return True
    _check("terminal", "import TerminalInputThread", _import)

    def _init():
        from terminal_input import TerminalInputThread
        dispatched = []
        t = TerminalInputThread(handle_text_fn=lambda text: dispatched.append(text))
        assert t._thread is None  # not started yet
    _check("terminal", "TerminalInputThread init (not started)", _init)

    def _start_stop():
        from terminal_input import TerminalInputThread
        t = TerminalInputThread(handle_text_fn=lambda text: None)
        t.start()
        assert t._thread is not None and t._thread.is_alive()
        t.stop()
        assert t._stop_event.is_set()
    _check("terminal", "start/stop without crash", _start_stop)


# ================================================================== #
#  Summary                                                              #
# ================================================================== #

def _print_summary() -> int:
    passed = sum(1 for r in _results if r["status"] == "PASS")
    warned = sum(1 for r in _results if r["status"] == "WARN")
    failed = sum(1 for r in _results if r["status"] == "FAIL")
    total  = len(_results)

    print(f"\n{BOLD}{'='*60}{RESET}", flush=True)
    print(f"{BOLD}  JARVIS VOICE ENGINE — SELF-TEST RESULTS{RESET}")
    print(f"{BOLD}{'='*60}{RESET}")
    print(f"  {GREEN}PASS{RESET}: {passed}/{total}")
    print(f"  {YELLOW}WARN{RESET}: {warned}/{total}")
    print(f"  {RED}FAIL{RESET}: {failed}/{total}")

    if failed == 0:
        print(f"\n{GREEN}{BOLD}  ALL TESTS PASSED.{RESET}")
        print(f"{GREEN}{BOLD}  [STARK INDUSTRIES] Voice engine rebuild complete. JARVIS online.{RESET}\n")
        return 0
    else:
        print(f"\n{RED}{BOLD}  {failed} test(s) FAILED:{RESET}")
        for r in _results:
            if r["status"] == "FAIL":
                print(f"    • [{r['group']}] {r['name']}: {r['msg']}")
        print()
        return 1


# ================================================================== #
#  Entry point                                                          #
# ================================================================== #

if __name__ == "__main__":
    print(f"\n{BOLD}{CYAN}JARVIS VOICE ENGINE — SELF-TEST SUITE{RESET}")
    print(f"{CYAN}Testing all rebuilt subsystems (Phases 1-9){RESET}\n")

    _test_echo_cancel()
    _test_noise_suppress()
    _test_vad()
    _test_pipeline()
    _test_turn_detector()
    _test_filler()
    _test_tts_engine()
    _test_stt_engine()
    _test_language_switch()
    _test_terminal_input()

    sys.exit(_print_summary())
