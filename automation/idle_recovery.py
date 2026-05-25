"""Idle recovery gestures for stalled demo turns."""

from __future__ import annotations

import logging
import random
import time

import config

LOG = logging.getLogger(__name__)


class IdleRecovery:
    def __init__(self) -> None:
        self._last_recovery = 0.0

    def maybe_recover(self, send_text, play_prompt_tts) -> bool:
        now = time.monotonic()
        if not config.IDLE_RECOVERY_ENABLED:
            return False
        if now - self._last_recovery < config.IDLE_RECOVERY_COOLDOWN:
            return False
        self._last_recovery = now
        trigger = random.choice(config.IDLE_RECOVERY_TRIGGERS)
        LOG.warning("Idle recovery triggered via %s", trigger)
        if trigger == "clap":
            self.two_claps()
            return True
        play_prompt_tts("Hey Jarvis")
        send_text("Hey Jarvis")
        return True

    def two_claps(self) -> None:
        gap = getattr(config, "CLAP_GAP_SECONDS", 1.0)
        try:
            import numpy as np
            import sounddevice as sd

            sample_rate = 44100
            duration = 0.09
            n = int(sample_rate * duration)
            noise = np.random.uniform(-1.0, 1.0, n).astype("float32")
            envelope = np.linspace(1.0, 0.02, n, dtype="float32")
            clap = (noise * envelope * 0.95).reshape(-1, 1)
            sd.play(clap, sample_rate)
            sd.wait()
            time.sleep(gap)
            sd.play(clap, sample_rate)
            sd.wait()
            return
        except Exception as exc:
            LOG.debug("Synthetic clap playback failed, using beep fallback: %s", exc)

        try:
            import winsound
            winsound.Beep(1900, 90)
            time.sleep(gap)
            winsound.Beep(1900, 90)
        except Exception:
            print("*clap*")
            time.sleep(gap)
            print("*clap*")
