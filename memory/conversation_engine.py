"""memory/conversation_engine.py — Within-session and cross-session conversation flow.

Tracks every exchange, detects unresolved threads, enables reference resolution.
Persists session logs to %APPDATA%/JARVIS/logs/YYYY-MM-DD.json
"""

import json
import os
import pathlib
import threading
import time
from datetime import datetime
from typing import Optional


_APPDATA  = pathlib.Path(os.path.expandvars("%APPDATA%")) / "JARVIS"
_LOGS_DIR = _APPDATA / "logs"


def get_yesterday_tail_from_logs() -> str:
    """Module-level helper: last meaningful user question from yesterday's log."""
    from datetime import timedelta
    yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
    path = _LOGS_DIR / f"{yesterday}.json"
    if not path.exists():
        return ""
    _CONTAMINATION = [
        "appears nominal", "most active", "percent", "running at",
        "cpu usage", "ram usage", "battery", "systems online",
        "see you", "next video", "next week", "subscribe", "crazy crazy",
        "i'll see", "we'll see", "hold that", "pick it up",
    ]
    _PROFANITY = ["fuck", "shit", "damn", "ass", "bitch", "crap", "hell"]
    _SKIP_EXACT = {"stop", "sleep", "bye", "exit", "quit", "thanks", "ok", "okay",
                   ".", "..", "jarvis"}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        # Prefer explicit session_topic written at startup
        topic = data.get("session_topic", "").strip()
        if topic:
            return topic
        for ex in reversed(data.get("exchanges", [])[-10:]):
            msg = ex.get("user", "").strip()
            ml  = msg.lower()
            if not (8 <= len(msg) <= 70):
                continue
            if ml.strip(".,!?") in _SKIP_EXACT:
                continue
            if any(kw in ml for kw in _CONTAMINATION):
                continue
            if any(kw in ml for kw in _PROFANITY):
                continue
            return msg
    except Exception:
        pass
    return ""


class Exchange:
    __slots__ = ("timestamp", "user", "assistant", "topics")

    def __init__(self, user: str, assistant: str):
        self.timestamp = time.time()
        self.user = user
        self.assistant = assistant
        self.topics: list[str] = []


class UnresolvedThread:
    __slots__ = ("type", "subject", "created_at", "reminded")

    def __init__(self, type_: str, subject: str):
        self.type = type_
        self.subject = subject
        self.created_at = time.time()
        self.reminded = False


class ConversationEngine:
    """Tracks the full conversation within a session and across sessions."""

    # Patterns that indicate a pending intention
    _PENDING_PATTERNS = [
        ("i need to call", "call"),
        ("i should email", "email"),
        ("remind me to", "reminder"),
        ("i'll do", "action"),
        ("i have to", "action"),
        ("i need to", "action"),
        ("don't let me forget", "reminder"),
    ]

    def __init__(self):
        self._exchanges: list[Exchange] = []
        self._unresolved: list[UnresolvedThread] = []
        self._lock = threading.Lock()
        self._today_file = _LOGS_DIR / f"{datetime.now().strftime('%Y-%m-%d')}.json"
        self._load_today()

    # ------------------------------------------------------------------ #
    #  Session management                                                  #
    # ------------------------------------------------------------------ #

    def add_exchange(self, user: str, assistant: str) -> None:
        with self._lock:
            ex = Exchange(user, assistant)
            self._exchanges.append(ex)
            self._check_for_pending(user)
            self._save_async()

    def _check_for_pending(self, user_text: str) -> None:
        tl = user_text.lower()
        for pattern, type_ in self._PENDING_PATTERNS:
            if pattern in tl:
                # Extract subject after the pattern
                idx = tl.find(pattern)
                subject = user_text[idx + len(pattern):].strip().split(".")[0][:60]
                if subject:
                    self._unresolved.append(UnresolvedThread(type_, subject))
                break

    def get_pending_reminder(self, max_age_hours: float = 2.0) -> Optional[str]:
        """Return a proactive reminder about an unresolved thread, or None."""
        now = time.time()
        for thread in self._unresolved:
            if not thread.reminded:
                age_hours = (now - thread.created_at) / 3600
                if age_hours >= max_age_hours:
                    thread.reminded = True
                    return f"You mentioned {thread.subject} earlier. Still on your list?"
        return None

    def get_session_summary(self, max_exchanges: int = 5) -> str:
        """Return a brief summary of recent exchanges for the LLM context."""
        with self._lock:
            recent = self._exchanges[-max_exchanges:]
            if not recent:
                return ""
            lines = []
            for ex in recent:
                t = datetime.fromtimestamp(ex.timestamp).strftime("%H:%M")
                lines.append(f"[{t}] You: {ex.user[:80]}")
                lines.append(f"[{t}] JARVIS: {ex.assistant[:80]}")
            return "\n".join(lines)

    def get_today_summary(self) -> str:
        return self.get_session_summary(max_exchanges=8)

    # ------------------------------------------------------------------ #
    #  Reference resolution                                                #
    # ------------------------------------------------------------------ #

    def resolve_reference(self, text: str) -> str:
        """Attempt to resolve vague references like 'that file', 'what you said'.

        Replaces pronouns with context from recent exchanges.
        """
        tl = text.lower()
        if not any(w in tl for w in ("that", "it", "the file", "what you said",
                                      "the thing", "that thing")):
            return text

        with self._lock:
            if not self._exchanges:
                return text
            last = self._exchanges[-1]
            # Very simple resolution: if user says "that" look at last assistant reply topic
            if "that file" in tl or "the file" in tl:
                import re
                file_m = re.search(r'[\w\-_]+\.\w{2,5}', last.assistant)
                if file_m:
                    return text.replace("that file", file_m.group()).replace("the file", file_m.group())
            if "what you said" in tl or "that thing" in tl:
                excerpt = last.assistant[:40]
                return text + f" (referring to: {excerpt})"

        return text

    # ------------------------------------------------------------------ #
    #  Cross-session continuity                                            #
    # ------------------------------------------------------------------ #

    def get_yesterday_tail(self) -> str:
        """Return the last meaningful user question from yesterday's session."""
        return get_yesterday_tail_from_logs()

    def get_last_topic(self) -> str:
        """Return a brief description of the last topic discussed."""
        with self._lock:
            if not self._exchanges:
                return ""
            last = self._exchanges[-1]
            # Return first ~40 chars of user query
            return last.user[:40]

    # ------------------------------------------------------------------ #
    #  Persistence                                                         #
    # ------------------------------------------------------------------ #

    def _load_today(self) -> None:
        try:
            _LOGS_DIR.mkdir(parents=True, exist_ok=True)
            if self._today_file.exists():
                with open(self._today_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                for ex_data in data.get("exchanges", []):
                    ex = Exchange(
                        ex_data.get("user", ""),
                        ex_data.get("assistant", ""),
                    )
                    ex.timestamp = ex_data.get("timestamp", time.time())
                    self._exchanges.append(ex)
        except Exception:
            pass

    def _save_async(self) -> None:
        threading.Thread(target=self._save, daemon=True).start()

    def _save(self) -> None:
        try:
            _LOGS_DIR.mkdir(parents=True, exist_ok=True)
            with self._lock:
                data = {
                    "date": datetime.now().strftime("%Y-%m-%d"),
                    "exchanges": [
                        {
                            "timestamp": ex.timestamp,
                            "user": ex.user,
                            "assistant": ex.assistant,
                        }
                        for ex in self._exchanges
                    ],
                }
            with open(self._today_file, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
        except Exception as e:
            print(f"[ConversationEngine] Save error: {e}")
