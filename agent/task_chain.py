"""agent/task_chain.py — Built-in multi-step task chains.

JARVIS executes compound workflows as single commands:
  "Morning briefing", "End of day", "Work mode", "Presentation mode"
"""

import threading
import time
from datetime import datetime
from typing import Callable, Optional


class TaskChain:
    """Executes predefined multi-step JARVIS routines."""

    def __init__(
        self,
        speak_fn: Callable,
        execute_tool: Callable,
        memory=None,
        monitor=None,
    ):
        self._speak = speak_fn
        self._execute = execute_tool
        self._memory = memory
        self._monitor = monitor

    def run(self, chain_name: str) -> str:
        """Execute a named chain. Returns spoken summary."""
        chains = {
            "morning briefing": self._morning_briefing,
            "briefing": self._morning_briefing,
            "status report": self._morning_briefing,
            "end of day": self._end_of_day,
            "goodnight": self._end_of_day,
            "good night": self._end_of_day,
            "work mode": self._work_mode,
            "presentation mode": self._presentation_mode,
            "focus mode": self._work_mode,
        }
        fn = chains.get(chain_name.lower())
        if fn:
            threading.Thread(target=fn, daemon=True).start()
            return f"Starting {chain_name}."
        return f"Unknown chain: {chain_name}"

    def is_chain_command(self, text: str) -> bool:
        tl = text.lower().strip()
        chain_triggers = (
            "morning briefing", "briefing", "status report",
            "end of day", "goodnight", "good night",
            "work mode", "presentation mode", "focus mode",
        )
        return any(trigger in tl for trigger in chain_triggers)

    def get_chain_name(self, text: str) -> Optional[str]:
        tl = text.lower().strip()
        chain_triggers = [
            "morning briefing", "briefing", "status report",
            "end of day", "goodnight", "good night",
            "work mode", "presentation mode", "focus mode",
        ]
        for trigger in chain_triggers:
            if trigger in tl:
                return trigger
        return None

    # ------------------------------------------------------------------ #
    #  Morning briefing                                                    #
    # ------------------------------------------------------------------ #

    def _morning_briefing(self) -> None:
        now = datetime.now()
        h = now.hour
        tod = "morning" if h < 12 else "afternoon" if h < 17 else "evening"

        name = ""
        if self._memory:
            try:
                name = self._memory.get("user", {}).get("name") or ""
            except Exception:
                pass

        name_str = f", {name}" if name else ""
        parts = [f"Good {tod}{name_str}."]

        # Weather
        try:
            from tools.web import get_weather
            weather = get_weather("Delhi")
            if weather and "Could not" not in weather:
                parts.append(weather)
        except Exception:
            pass

        # News
        try:
            from tools.web import get_news
            news = get_news("world")
            if news:
                parts.append(f"In the news: {news[:200]}")
        except Exception:
            pass

        # Battery
        if self._monitor:
            try:
                _, _, bat_pct, plugged = self._monitor.get_stats()
                bat_str = f"Battery at {bat_pct:.0f}%"
                if not plugged:
                    bat_str += ", unplugged"
                parts.append(bat_str)
            except Exception:
                pass

        # System health
        try:
            from tools.system import get_system_info
            info = get_system_info()
            if info:
                parts.append(info[:100])
        except Exception:
            pass

        spoken = " ".join(parts)
        self._speak(spoken)

    # ------------------------------------------------------------------ #
    #  End of day                                                          #
    # ------------------------------------------------------------------ #

    def _end_of_day(self) -> None:
        parts = ["Wrapping up for the day."]

        # Summary from memory/context
        if self._memory:
            try:
                last_session = self._memory.get("last_session_summary", "")
                if last_session:
                    parts.append(f"You were working on {last_session[:60]}.")
            except Exception:
                pass

        parts.append("Shall I shut down or stand by?")
        self._speak(" ".join(parts))

    # ------------------------------------------------------------------ #
    #  Work mode                                                           #
    # ------------------------------------------------------------------ #

    def _work_mode(self) -> None:
        self._speak("Entering work mode.")

        # Open preferred apps from memory
        if self._memory:
            try:
                prefs = self._memory.get("preferences", {})
                frequent_apps = prefs.get("frequent_apps", {})
                if frequent_apps:
                    top_apps = sorted(
                        frequent_apps.items(), key=lambda x: x[1], reverse=True
                    )[:3]
                    for app, _ in top_apps:
                        try:
                            self._execute("open_app", {"name": app})
                            time.sleep(0.5)
                        except Exception:
                            pass
            except Exception:
                pass

        self._speak("Work mode active. Focus timer started.")

    # ------------------------------------------------------------------ #
    #  Presentation mode                                                   #
    # ------------------------------------------------------------------ #

    def _presentation_mode(self) -> None:
        # Lower volume to 30%
        try:
            self._execute("set_volume", {"level": 30})
        except Exception:
            pass
        self._speak("Presentation mode active. Good luck.")
