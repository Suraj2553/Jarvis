"""meeting/meeting_assistant.py — Real-time meeting detection and transcription.

Detects: Teams, Zoom, Chrome (meet.google.com, zoom.us), Discord.
On detection: silently starts recording system audio loopback.
Builds rolling transcript with action items and flagged notes.
On meeting end: offers summary, saves to Desktop.
"""

import json
import os
import pathlib
import threading
import time
from datetime import datetime
from typing import Callable, Optional

try:
    import psutil as _psutil
    _HAS_PSUTIL = True
except Exception:
    _psutil = None
    _HAS_PSUTIL = False

_MEETING_PROCESSES = {
    "teams.exe": "Microsoft Teams",
    "zoom.exe": "Zoom",
    "discord.exe": "Discord",
}

_MEETING_URLS = ("meet.google.com", "zoom.us", "teams.microsoft.com")


class MeetingAssistant:
    """Detects meetings, transcribes audio, tracks action items."""

    def __init__(
        self,
        speak_fn: Callable,
        stt=None,
        router=None,
        config: Optional[dict] = None,
    ):
        self._speak = speak_fn
        self._stt = stt
        self._router = router
        self._config = config or {}

        self._in_meeting = False
        self._meeting_start: Optional[float] = None
        self._transcript: list[str] = []
        self._action_items: list[str] = []
        self._flagged_moments: list[str] = []

        self._running = False
        self._detect_thread: Optional[threading.Thread] = None
        self._record_thread: Optional[threading.Thread] = None
        self._stop_record = threading.Event()

        # HUD callback for "LIVE" indicator
        self._hud_meeting_fn: Optional[Callable] = None

    def set_hud_callback(self, fn: Callable) -> None:
        self._hud_meeting_fn = fn

    def start(self) -> None:
        self._running = True
        self._detect_thread = threading.Thread(
            target=self._detect_loop, daemon=True, name="MeetingDetect"
        )
        self._detect_thread.start()

    def stop(self) -> None:
        self._running = False
        self._stop_record.set()

    # ------------------------------------------------------------------ #
    #  Detection                                                           #
    # ------------------------------------------------------------------ #

    def _detect_loop(self) -> None:
        while self._running:
            try:
                in_meeting = self._check_meeting_active()
                if in_meeting and not self._in_meeting:
                    self._on_meeting_start()
                elif not in_meeting and self._in_meeting:
                    self._on_meeting_end()
            except Exception as e:
                print(f"[MeetingAssistant] Detect error: {e}")
            time.sleep(5)

    def _check_meeting_active(self) -> bool:
        if not _HAS_PSUTIL:
            return False
        try:
            for proc in _psutil.process_iter(["name", "cmdline"]):
                try:
                    pname = proc.info["name"].lower()
                    if pname in _MEETING_PROCESSES:
                        return True
                    # Check Chrome/Edge for meeting URLs
                    if pname in ("chrome.exe", "msedge.exe"):
                        cmdline = " ".join(proc.info.get("cmdline") or []).lower()
                        if any(url in cmdline for url in _MEETING_URLS):
                            return True
                except Exception:
                    pass
        except Exception:
            pass
        return False

    # ------------------------------------------------------------------ #
    #  Meeting lifecycle                                                   #
    # ------------------------------------------------------------------ #

    def _on_meeting_start(self) -> None:
        self._in_meeting = True
        self._meeting_start = time.time()
        self._transcript.clear()
        self._action_items.clear()
        self._flagged_moments.clear()
        self._stop_record.clear()
        print("[MeetingAssistant] Meeting detected — silent entry.")

        if self._hud_meeting_fn:
            self._hud_meeting_fn(True)

        # Start loopback recording in background
        self._record_thread = threading.Thread(
            target=self._record_loop, daemon=True, name="MeetingRecord"
        )
        self._record_thread.start()

    def _on_meeting_end(self) -> None:
        self._in_meeting = False
        self._stop_record.set()

        if self._hud_meeting_fn:
            self._hud_meeting_fn(False)

        if not self._meeting_start:
            return

        duration_min = (time.time() - self._meeting_start) / 60
        duration_str = f"{duration_min:.0f} minutes"

        self._speak(f"Meeting ended. {duration_str}. Want the summary?")
        self._save_notes()
        print(f"[MeetingAssistant] Meeting ended ({duration_str}).")

    # ------------------------------------------------------------------ #
    #  Recording (loopback)                                                #
    # ------------------------------------------------------------------ #

    def _record_loop(self) -> None:
        """Capture system audio and transcribe in chunks."""
        if not self._stt:
            return
        try:
            import sounddevice as sd
            import numpy as np

            CHUNK_SECONDS = 10
            SR = 16000

            while not self._stop_record.is_set():
                try:
                    audio = sd.rec(
                        int(CHUNK_SECONDS * SR),
                        samplerate=SR,
                        channels=1,
                        dtype="float32",
                    )
                    # Wait or stop early
                    for _ in range(CHUNK_SECONDS * 10):
                        if self._stop_record.is_set():
                            sd.stop()
                            break
                        time.sleep(0.1)
                    else:
                        sd.wait()

                    if audio is not None and len(audio) > SR:
                        text = self._stt.transcribe(audio.flatten()).strip()
                        if text and len(text) > 10:
                            self._transcript.append(text)
                except Exception as e:
                    print(f"[MeetingAssistant] Record chunk error: {e}")
                    time.sleep(2)
        except Exception as e:
            print(f"[MeetingAssistant] Record loop error: {e}")

    # ------------------------------------------------------------------ #
    #  Voice commands (called from main pipeline during meeting)          #
    # ------------------------------------------------------------------ #

    def handle_command(self, text: str) -> Optional[str]:
        """Handle meeting-specific voice commands. Returns response or None."""
        tl = text.lower().strip()

        if "note that" in tl:
            # Flag last 30 seconds of transcript
            excerpt = " ".join(self._transcript[-3:]) if self._transcript else ""
            self._flagged_moments.append(f"[IMPORTANT] {excerpt[:200]}")
            return "Noted."

        if "action item" in tl:
            excerpt = text.replace("action item", "").strip()
            if excerpt:
                self._action_items.append(excerpt)
                return f"Action item recorded: {excerpt[:60]}"
            return "What's the action item?"

        if "summarize" in tl or "summarize the meeting" in tl:
            summary = self._generate_summary()
            return summary or "Still gathering data from the meeting."

        return None

    def _generate_summary(self) -> str:
        if not self._transcript:
            return "No transcript available yet."
        if not self._router:
            return f"Meeting in progress. {len(self._transcript)} segments captured."

        full_text = " ".join(self._transcript)[:3000]
        messages = [
            {"role": "system",
             "content": "Summarize this meeting transcript in 3 sentences. "
                        "Note any decisions made and action items."},
            {"role": "user", "content": full_text},
        ]
        try:
            return self._router.chat_sync(messages, max_tokens=150)
        except Exception:
            return f"Meeting captured {len(self._transcript)} segments."

    # ------------------------------------------------------------------ #
    #  Save notes                                                          #
    # ------------------------------------------------------------------ #

    def _save_notes(self) -> None:
        try:
            desktop = pathlib.Path(os.path.expanduser("~/Desktop"))
            timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M")
            notes_file = desktop / f"JARVIS_Meeting_{timestamp}.txt"

            lines = [
                f"JARVIS Meeting Notes — {datetime.now().strftime('%B %d, %Y %H:%M')}",
                "=" * 60,
                "",
            ]

            if self._action_items:
                lines += ["ACTION ITEMS:", ""]
                for i, item in enumerate(self._action_items, 1):
                    lines.append(f"{i}. {item}")
                lines.append("")

            if self._flagged_moments:
                lines += ["FLAGGED MOMENTS:", ""]
                for moment in self._flagged_moments:
                    lines.append(f"• {moment}")
                lines.append("")

            if self._transcript:
                lines += ["TRANSCRIPT SUMMARY:", ""]
                summary = self._generate_summary()
                lines.append(summary)
                lines.append("")

            with open(notes_file, "w", encoding="utf-8") as f:
                f.write("\n".join(lines))

            print(f"[MeetingAssistant] Notes saved: {notes_file}")
        except Exception as e:
            print(f"[MeetingAssistant] Save error: {e}")
