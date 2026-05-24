"""audio/conversation_state.py — Turn-taking state machine for JARVIS  v3.0

Solves: JARVIS doesn't know when to wait vs when to move on.

States
──────
IDLE          → waiting for wake word / clap
LISTENING     → actively recording user speech (VAD active)
THINKING      → LLM processing
SPEAKING      → TTS playing
LISTENING_WAIT → JARVIS asked a question; holding the mic open
                 for a reply before returning to IDLE.
                 Times out after `wait_timeout` seconds of silence.

Integration
───────────
In main.py, replace direct calls to _activate() / _speak() with:
    state = ConversationState(speak_fn, activate_fn, config)

    # When JARVIS finishes speaking a question:
    state.jarvis_asked_question()

    # When user says something (wake word or VAD trigger):
    state.user_spoke()

    # When JARVIS is done speaking (not a question):
    state.jarvis_done_speaking()

    # In your activation handler:
    if not state.can_activate():
        return   # already in mid-conversation
"""

import enum
import threading
import time
from typing import Callable, Optional


class _State(enum.Enum):
    IDLE           = "idle"
    LISTENING      = "listening"
    THINKING       = "thinking"
    SPEAKING       = "speaking"
    LISTENING_WAIT = "listening_wait"   # holding mic after JARVIS asked a Q


class ConversationState:
    """Finite-state machine for JARVIS conversation turns.

    Thread-safe.  All transitions log to console in debug mode.
    """

    # How long to wait for a user reply after JARVIS asks a question
    # before giving up and returning to IDLE.
    DEFAULT_WAIT_TIMEOUT = 8.0   # seconds

    def __init__(
        self,
        activate_fn: Callable,          # re-activates STT listening
        config: Optional[dict] = None,
    ):
        self._activate  = activate_fn
        self._config    = config or {}
        self._state     = _State.IDLE
        self._lock      = threading.Lock()
        self._wait_timer: Optional[threading.Timer] = None
        self._debug     = config.get("debug_audio_timing", False) if config else False

    # ── State queries ───────────────────────────────────────────────── #

    @property
    def current(self) -> str:
        return self._state.value

    def is_idle(self) -> bool:
        return self._state == _State.IDLE

    def is_speaking(self) -> bool:
        return self._state == _State.SPEAKING

    def is_listening_for_reply(self) -> bool:
        return self._state == _State.LISTENING_WAIT

    def can_activate(self) -> bool:
        """Returns True if it's okay to start a new STT session."""
        with self._lock:
            # Don't re-activate while already listening or mid-thought
            return self._state in (_State.IDLE, _State.LISTENING_WAIT)

    # ── Transitions ─────────────────────────────────────────────────── #

    def user_spoke(self) -> None:
        """Call when the user starts speaking (VAD onset or wake word)."""
        with self._lock:
            self._cancel_wait_timer()
            self._set((_State.IDLE, _State.LISTENING_WAIT), _State.LISTENING)

    def user_done_speaking(self) -> None:
        """Call when VAD detects end of user speech."""
        with self._lock:
            self._set((_State.LISTENING,), _State.THINKING)

    def llm_started(self) -> None:
        """Call when the LLM request is dispatched."""
        with self._lock:
            self._set((_State.THINKING,), _State.THINKING)  # stays in THINKING

    def jarvis_started_speaking(self) -> None:
        """Call when TTS begins playing audio."""
        with self._lock:
            self._set((_State.THINKING, _State.LISTENING_WAIT), _State.SPEAKING)

    def jarvis_done_speaking(self) -> None:
        """Call when TTS finishes (no question was asked)."""
        with self._lock:
            self._cancel_wait_timer()
            self._state = _State.IDLE
            self._log("→ IDLE (response complete)")

    def jarvis_asked_question(self, timeout: Optional[float] = None) -> None:
        """Call when JARVIS finishes speaking a question.

        Keeps the mic conceptually 'open' for `timeout` seconds.
        If the user speaks within that window, normal activation fires.
        If not, JARVIS quietly returns to IDLE without speaking again.
        """
        with self._lock:
            self._cancel_wait_timer()
            self._state = _State.LISTENING_WAIT
            self._log("→ LISTENING_WAIT (waiting for reply)")

        wait = timeout or self._config.get("question_wait_timeout",
                                           self.DEFAULT_WAIT_TIMEOUT)
        # Activate STT immediately so the mic is live
        try:
            threading.Thread(
                target=self._activate,
                kwargs={"source": "question_followup"},
                daemon=True,
            ).start()
        except TypeError:
            # activate_fn may not accept kwargs — call with no args
            threading.Thread(target=self._activate, daemon=True).start()

        self._wait_timer = threading.Timer(wait, self._on_wait_timeout)
        self._wait_timer.daemon = True
        self._wait_timer.start()

    def interrupted(self) -> None:
        """Call when TTS is cut off mid-sentence."""
        with self._lock:
            self._cancel_wait_timer()
            self._state = _State.IDLE
            self._log("→ IDLE (interrupted)")

    # ── Internal ────────────────────────────────────────────────────── #

    def _set(self, allowed_from: tuple, new_state: _State) -> None:
        """Transition only if current state is in allowed_from."""
        if self._state in allowed_from:
            old = self._state.value
            self._state = new_state
            self._log(f"{old} → {new_state.value}")

    def _log(self, msg: str) -> None:
        if self._debug:
            print(f"[ConvState] {msg}")

    def _cancel_wait_timer(self) -> None:
        if self._wait_timer:
            self._wait_timer.cancel()
            self._wait_timer = None

    def _on_wait_timeout(self) -> None:
        """User didn't reply — silently return to IDLE."""
        with self._lock:
            if self._state == _State.LISTENING_WAIT:
                self._state = _State.IDLE
                self._log("→ IDLE (reply timeout — user didn't answer)")


# ── Helper: detect if JARVIS's reply ends with a question ─────────── #
# Call this after LLM response is complete to decide whether to call
# jarvis_asked_question() or jarvis_done_speaking().

import re as _re
_QUESTION_RE = _re.compile(r"\?\s*$", _re.MULTILINE)


def reply_ends_with_question(text: str) -> bool:
    """True if the reply's last meaningful sentence is a question."""
    stripped = text.strip()
    return bool(_QUESTION_RE.search(stripped))
