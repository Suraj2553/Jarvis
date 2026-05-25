"""Real-time stdout monitor and turn-completion detector for JARVIS."""

from __future__ import annotations

import logging
import queue
import re
import subprocess
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

import config

LOG = logging.getLogger(__name__)


@dataclass
class TurnState:
    prompt: str
    started_at: float = field(default_factory=time.monotonic)
    last_output_at: float = field(default_factory=time.monotonic)
    last_speech_at: float = 0.0
    saw_user_echo: bool = False
    saw_processing: bool = False
    saw_speaking: bool = False
    saw_completion: bool = False
    lines: list[str] = field(default_factory=list)


class TerminalMonitor:
    PROCESSING_PATTERNS = [
        re.compile(r"\[Brain\]|\[LLMRouter\]|\[Tools\]|\[JARVIS\] Activated", re.I),
        re.compile(r"thinking|processing|tool call|intent|result", re.I),
    ]
    SPEAKING_PATTERNS = [
        re.compile(r"\[JARVIS\] Speaking:", re.I),
        re.compile(r"TTS|queue_sentence|speak\(\)", re.I),
        re.compile(r"\[ConvState\].*IDLE.*response complete", re.I),
    ]
    COMPLETION_PATTERNS = [
        re.compile(r"\[ConvState\].*response complete", re.I),
        re.compile(r"\[(?:JARVIS|FRIDAY)\] >\s*$", re.I),
        re.compile(r"\[JARVIS\] Persona switched|\[JARVIS\] Language switched", re.I),
    ]
    READY_PATTERNS = [
        re.compile(r"Terminal input active", re.I),
        re.compile(r"All systems nominal", re.I),
        re.compile(r"I'm listening|listening", re.I),
    ]

    def __init__(self, log_path: Path, on_line: Optional[Callable[[str], None]] = None) -> None:
        self.log_path = log_path
        self.on_line = on_line
        self._q: queue.Queue[str] = queue.Queue()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._proc: subprocess.Popen[str] | None = None
        self.last_line_at = time.monotonic()
        self.ready = False

    def attach(self, proc: subprocess.Popen[str]) -> None:
        self._proc = proc
        self._thread = threading.Thread(target=self._reader, name="JarvisStdout", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _reader(self) -> None:
        assert self._proc is not None and self._proc.stdout is not None
        with self.log_path.open("a", encoding="utf-8", errors="replace") as fh:
            for raw in self._proc.stdout:
                line = raw.rstrip("\r\n")
                now = time.monotonic()
                self.last_line_at = now
                fh.write(line + "\n")
                fh.flush()
                self._q.put(line)
                if any(p.search(line) for p in self.READY_PATTERNS):
                    self.ready = True
                if self.on_line:
                    self.on_line(line)
                if self._stop.is_set():
                    break

    def drain(self) -> list[str]:
        lines: list[str] = []
        while True:
            try:
                lines.append(self._q.get_nowait())
            except queue.Empty:
                return lines

    def wait_for_boot_ready(self, timeout: float) -> bool:
        start = time.monotonic()
        stable_since = None
        while time.monotonic() - start < timeout:
            self.drain()
            if self.ready:
                if stable_since is None:
                    stable_since = time.monotonic()
                if time.monotonic() - stable_since >= config.BOOT_READY_IDLE:
                    return True
            time.sleep(config.STATUS_POLL_SECONDS)
        return self.ready

    def observe_turn(
        self,
        prompt: str,
        fallback_wait: float,
        recovery_callback: Optional[Callable[[], bool]] = None,
    ) -> TurnState:
        state = TurnState(prompt=prompt)
        deadline = time.monotonic() + max(fallback_wait, 5.0) + config.MAX_WAIT_EXTENSION
        min_wait_until = time.monotonic() + min(max(fallback_wait * 0.22, 3.0), 10.0)
        recovered = False

        while time.monotonic() < deadline:
            for line in self.drain():
                self._classify_line(line, state)

            now = time.monotonic()
            output_idle = now - state.last_output_at
            speech_idle = now - state.last_speech_at if state.last_speech_at else output_idle
            has_started = (
                state.saw_user_echo
                or state.saw_processing
                or state.saw_speaking
                or state.saw_completion
            )
            can_finish = now >= min_wait_until and has_started

            if can_finish and state.saw_completion and output_idle >= 1.0:
                return state
            if can_finish and state.saw_speaking and speech_idle >= config.SPEAKING_IDLE_SECONDS:
                return state
            if can_finish and output_idle >= config.OUTPUT_IDLE_SECONDS:
                return state

            if (
                recovery_callback
                and not recovered
                and not has_started
                and now - state.started_at >= config.IDLE_RECOVERY_AFTER
            ):
                recovered = recovery_callback()
                state.started_at = time.monotonic()

            time.sleep(config.STATUS_POLL_SECONDS)

        LOG.warning("Turn timed out after %.1fs: %s", time.monotonic() - state.started_at, prompt)
        return state

    def _classify_line(self, line: str, state: TurnState) -> None:
        now = time.monotonic()
        state.lines.append(line)
        state.last_output_at = now
        if line.startswith("[You]"):
            state.saw_user_echo = True
        if any(p.search(line) for p in self.PROCESSING_PATTERNS):
            state.saw_processing = True
        if any(p.search(line) for p in self.SPEAKING_PATTERNS):
            state.saw_speaking = True
            state.last_speech_at = now
        if any(p.search(line) for p in self.COMPLETION_PATTERNS):
            state.saw_completion = True
