"""terminal_input.py — Keyboard fallback for noisy environments.

Runs a readline loop in a daemon thread.  Typed text is passed to the
provided handle_text_fn, which can be a coroutine submitted to an event
loop or a plain synchronous callable.

Built-in slash commands:
  /hindi   — switch TTS + STT to Hindi
  /english — switch TTS + STT to English
  /status  — print engine status
  /quit    — shut down JARVIS

Usage:
    t = TerminalInputThread(
        handle_text_fn=my_async_fn,
        loop=asyncio_event_loop,   # optional
        tts_engine=tts,
        stt_engine=stt,
    )
    t.start()
    t.stop()
"""

import asyncio
import logging
import threading
from typing import Callable, Optional

logger = logging.getLogger(__name__)

_BANNER = (
    "\n[JARVIS] Terminal input active — type your command and press Enter.\n"
    "  /hindi   → switch to Hindi\n"
    "  /english → switch to English\n"
    "  /status  → show engine status\n"
    "  /quit    → exit\n"
)


class TerminalInputThread:
    """Background thread that reads from stdin and dispatches text."""

    def __init__(
        self,
        handle_text_fn: Callable[[str], object],
        loop: Optional[asyncio.AbstractEventLoop] = None,
        tts_engine=None,
        stt_engine=None,
        quit_fn: Optional[Callable[[], None]] = None,
        get_persona_fn: Optional[Callable[[], str]] = None,
        set_persona_fn: Optional[Callable[[str], None]] = None,
    ):
        self._handle_text  = handle_text_fn
        self._loop         = loop
        self._tts          = tts_engine
        self._stt          = stt_engine
        self._quit_fn      = quit_fn
        self._get_persona  = get_persona_fn   # returns "jarvis" | "friday"
        self._set_persona  = set_persona_fn   # callable(persona_str)
        self._stop_event   = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._run, daemon=True, name="TerminalInput"
        )
        self._thread.start()
        logger.info("[Terminal] Input thread started")

    def stop(self) -> None:
        self._stop_event.set()

    # ------------------------------------------------------------------ #
    #  Main loop                                                           #
    # ------------------------------------------------------------------ #

    def _run(self) -> None:
        print(_BANNER, flush=True)
        while not self._stop_event.is_set():
            try:
                persona = self._get_persona() if self._get_persona else "jarvis"
                prompt = "[FRIDAY] > " if persona == "friday" else "[JARVIS] > "
                text = input(prompt).strip()
            except (EOFError, KeyboardInterrupt):
                break
            if not text:
                continue

            if text.startswith("/"):
                handled = self._handle_command(text)
                if handled:
                    continue

            self._dispatch(text)

        logger.info("[Terminal] Input thread exiting")

    def _handle_command(self, cmd: str) -> bool:
        """Process slash commands. Returns True if handled."""
        lower = cmd.lower()

        if lower in ("/friday", "/fr"):
            self._switch_persona("friday")
            return True

        if lower in ("/jarvis", "/jv"):
            self._switch_persona("jarvis")
            return True

        if lower in ("/hindi", "/hi"):
            self._switch_language("hi")
            return True

        if lower in ("/english", "/en"):
            self._switch_language("en")
            return True

        if lower in ("/status", "/s"):
            self._print_status()
            return True

        if lower in ("/quit", "/exit", "/q"):
            print("[JARVIS] Shutting down...", flush=True)
            if self._quit_fn:
                try:
                    self._quit_fn()
                except Exception as e:
                    logger.error("[Terminal] Quit callback error: %s", e)
            self._stop_event.set()
            return True

        print(f"[JARVIS] Unknown command: {cmd}", flush=True)
        return True

    def _switch_persona(self, persona: str) -> None:
        if self._set_persona is None:
            print(f"[JARVIS] Persona switch not connected", flush=True)
            return
        try:
            self._set_persona(persona)
            label = "Friday" if persona == "friday" else "JARVIS"
            print(f"[JARVIS] Persona switched to {label}", flush=True)
        except Exception as e:
            logger.error("[Terminal] Persona switch error: %s", e)

    def _switch_language(self, lang: str) -> None:
        if self._tts is None and self._stt is None:
            print(f"[JARVIS] Language engines not connected", flush=True)
            return
        try:
            from brain.language_switch import handle_language_switch
            handle_language_switch(lang, self._tts, self._stt)
            print(f"[JARVIS] Language switched to {lang.upper()}", flush=True)
        except Exception as e:
            logger.error("[Terminal] Language switch error: %s", e)

    def _print_status(self) -> None:
        lines = ["[JARVIS] Status:"]
        if self._tts:
            lang = getattr(self._tts, "_language", "?")
            speaking = self._tts.is_speaking()
            lines.append(f"  TTS  lang={lang}  speaking={speaking}")
        if self._stt:
            mode = getattr(self._stt, "_mode", "?")
            lang = getattr(self._stt, "_language", "?")
            lines.append(f"  STT  mode={mode}  lang={lang}")
        print("\n".join(lines), flush=True)

    def _dispatch(self, text: str) -> None:
        """Send text to the handler (async or sync)."""
        if self._tts and self._tts.is_speaking():
            self._tts.stop_immediately()

        if self._loop is not None and asyncio.iscoroutinefunction(self._handle_text):
            try:
                asyncio.run_coroutine_threadsafe(
                    self._handle_text(text), self._loop
                )
            except Exception as e:
                logger.error("[Terminal] Async dispatch error: %s", e)
        else:
            try:
                result = self._handle_text(text)
                # If it returned a coroutine but no loop, run it synchronously
                if asyncio.iscoroutine(result):
                    asyncio.run(result)
            except Exception as e:
                logger.error("[Terminal] Dispatch error: %s", e)
