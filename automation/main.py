"""Fully automated cinematic JARVIS demo recorder."""

from __future__ import annotations

import logging
import random
import subprocess
import sys
import time

import config
from idle_recovery import IdleRecovery
from obs_controller import OBSController
from prompts import BOOT_LINES, SEQUENCE
from terminal_monitor import TerminalMonitor
from tts_engine import PromptTTS
from utils import (
    disable_focus_assist_best_effort,
    hide_taskbar,
    minimize_all_windows,
    natural_pause,
    set_console_title,
    setup_logging,
    show_taskbar,
    typewriter,
)

LOG = logging.getLogger(__name__)


class CinematicRecorder:
    def __init__(self) -> None:
        self.session_log = setup_logging()
        self.jarvis_log = config.LOG_DIR / f"jarvis_stdout_{self.session_log.stem.removeprefix('session_')}.log"
        self.obs = OBSController()
        self.tts = PromptTTS()
        self.recovery = IdleRecovery()
        self.proc: subprocess.Popen[str] | None = None
        self.monitor = TerminalMonitor(self.jarvis_log, on_line=self._echo_important_line)
        self.subtitles: list[tuple[float, float, str]] = []
        self.started_at = time.monotonic()

    def run(self) -> int:
        LOG.info("Session log: %s", self.session_log)
        try:
            self._prepare_desktop()
            self.obs.start()
            self._boot_sequence()
            self._launch_jarvis()
            self._run_sequence()
            return 0
        finally:
            self._shutdown()

    def _prepare_desktop(self) -> None:
        if config.SET_CONSOLE_TITLE:
            set_console_title("STARK INDUSTRIES // JARVIS CINEMATIC CAPTURE")
        if config.MINIMIZE_WINDOWS:
            minimize_all_windows()
        if config.HIDE_TASKBAR:
            hide_taskbar()
        if config.DISABLE_NOTIFICATIONS:
            disable_focus_assist_best_effort()

    def _boot_sequence(self) -> None:
        print("\n" + "=" * 72)
        print("        STARK INDUSTRIES // JARVIS CINEMATIC CAPTURE")
        print("=" * 72 + "\n")
        typewriter(BOOT_LINES, delay=0.018)
        natural_pause(1.0, (0.4, 1.2))

    def _launch_jarvis(self) -> None:
        if config.USE_START_JARVIS_BAT and config.START_JARVIS_BAT.exists():
            cmd = ["cmd.exe", "/c", str(config.START_JARVIS_BAT), *config.JARVIS_ARGS]
        else:
            cmd = [str(config.PYTHON_EXE), str(config.JARVIS_ENTRY), *config.JARVIS_ARGS]
        LOG.info("Launching JARVIS: %s", " ".join(cmd))
        self.proc = subprocess.Popen(
            cmd,
            cwd=str(config.ROOT_DIR),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
        )
        self.monitor.attach(self.proc)
        ready = self.monitor.wait_for_boot_ready(config.BOOT_TIMEOUT)
        LOG.info("Boot readiness: %s", ready)
        self._wait_for_boot_greeting()
        natural_pause(1.2, (0.8, 2.2))

    def _wait_for_boot_greeting(self) -> None:
        LOG.info("Waiting for JARVIS boot greeting to settle.")
        deadline = time.monotonic() + config.BOOT_GREETING_MAX_EXTRA_WAIT
        while time.monotonic() < deadline:
            self.monitor.drain()
            idle_for = time.monotonic() - self.monitor.last_line_at
            if idle_for >= config.BOOT_GREETING_IDLE_SECONDS:
                LOG.info("Boot greeting settled after %.1fs idle.", idle_for)
                return
            time.sleep(config.STATUS_POLL_SECONDS)
        LOG.info("Boot greeting wait reached max extra wait.")

    def _run_sequence(self) -> None:
        for index, (prompt, fallback_wait) in enumerate(SEQUENCE, start=1):
            lower = prompt.lower().strip()
            LOG.info("Turn %02d/%02d: %s", index, len(SEQUENCE), prompt)

            if config.PRESENTATION_TRIGGER in lower:
                self.obs.switch_scene(config.OBS_SCENE_PRESENTATION)

            self._send_prompt(prompt)

            state = self.monitor.observe_turn(
                prompt,
                fallback_wait,
                recovery_callback=lambda p=prompt: self._recover_turn(p),
            )
            LOG.info(
                "Turn complete: started=%s processing=%s speaking=%s completion=%s lines=%d",
                state.saw_user_echo,
                state.saw_processing,
                state.saw_speaking,
                state.saw_completion,
                len(state.lines),
            )

            if config.PRESENTATION_FOLLOWUP_STOP == lower:
                self.obs.switch_scene(config.OBS_SCENE_END)

            breath = random.uniform(*config.AFTER_RESPONSE_BREATH)
            natural_pause(breath, (0.0, config.MAX_EXTRA_NATURAL_PAUSE))

    def _send_prompt(self, prompt: str) -> None:
        if self._is_slash_command(prompt):
            natural_pause(0.6, (0.2, 0.8))
            self._write_stdin(prompt)
            return

        language = self._language_for(prompt)
        if config.ACTIVATE_BEFORE_EACH_PROMPT:
            self._activate_jarvis(language)

        spoken_start = time.monotonic()
        if config.PROMPT_TTS_BEFORE_SEND:
            LOG.info("Speaking prompt aloud: %s", prompt)
            self.tts.speak_prompt(prompt, language)
        spoken_end = time.monotonic()
        self.subtitles.append((spoken_start - self.started_at, spoken_end - self.started_at, prompt))
        if config.SEND_STDIN_BACKUP_AFTER_TTS:
            natural_pause(config.STDIN_BACKUP_DELAY, (0.0, 0.6))
            self._write_stdin(prompt)

    def _activate_jarvis(self, language: str = "en") -> None:
        trigger = random.choice(config.ACTIVATION_TRIGGERS)
        LOG.info("Activating JARVIS via %s", trigger)
        if trigger == "clap":
            self.recovery.two_claps()
        else:
            self.tts.speak_prompt("Hey Jarvis", language)
        natural_pause(0.0, config.ACTIVATION_TO_PROMPT_PAUSE)

    def _recover_turn(self, prompt: str) -> bool:
        if self._is_slash_command(prompt):
            return self.recovery.maybe_recover(self._write_stdin, self.tts.speak_prompt)
        LOG.warning("Turn appears idle; re-activating and replaying prompt naturally.")
        self._activate_jarvis(self._language_for(prompt))
        self.tts.speak_prompt(prompt, self._language_for(prompt))
        if config.SEND_STDIN_BACKUP_AFTER_TTS:
            natural_pause(config.STDIN_BACKUP_DELAY, (0.0, 0.5))
            self._write_stdin(prompt)
        return True

    def _write_stdin(self, text: str) -> None:
        if self.proc is None or self.proc.stdin is None:
            raise RuntimeError("JARVIS process is not running")
        self.proc.stdin.write(text + "\n")
        self.proc.stdin.flush()

    def _is_slash_command(self, prompt: str) -> bool:
        return prompt.strip().startswith("/")

    def _language_for(self, prompt: str) -> str:
        p = prompt.lower()
        if p in {"/hindi", "/hi"}:
            return "hi"
        if any(token in p for token in ("aaj", "mujhe", "abhi", "kaisa", "batao")):
            return "hi"
        return "en"

    def _echo_important_line(self, line: str) -> None:
        print(line, flush=True)

    def _shutdown(self) -> None:
        LOG.info("Shutting down automation.")
        if config.GENERATE_SUBTITLES:
            self._write_subtitles()
        try:
            if self.proc and self.proc.poll() is None:
                try:
                    self._write_stdin("/quit")
                    self.proc.wait(timeout=12)
                except Exception:
                    self.proc.terminate()
        finally:
            self.monitor.stop()
            self.obs.stop()
            if config.HIDE_TASKBAR:
                show_taskbar()

    def _write_subtitles(self) -> None:
        path = config.LOG_DIR / f"prompts_{self.session_log.stem.removeprefix('session_')}.srt"
        with path.open("w", encoding="utf-8") as fh:
            for idx, (start, end, text) in enumerate(self.subtitles, start=1):
                if end <= start:
                    end = start + 2.0
                fh.write(f"{idx}\n{_srt_time(start)} --> {_srt_time(end)}\n{text}\n\n")
        LOG.info("Prompt subtitles written: %s", path)


def _srt_time(seconds: float) -> str:
    ms_total = int(max(0.0, seconds) * 1000)
    ms = ms_total % 1000
    total = ms_total // 1000
    s = total % 60
    total //= 60
    m = total % 60
    h = total // 60
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def main() -> int:
    return CinematicRecorder().run()


if __name__ == "__main__":
    sys.exit(main())
