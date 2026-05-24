"""memory/memory_engine.py — Personality memory engine.

Stores user facts, preferences, relationship level, and routines.
Learns passively from every conversation. Persists to %APPDATA%/JARVIS/memory.json
"""

import json
import os
import pathlib
import threading
import time
from datetime import datetime
from typing import Any, Optional

import requests


_APPDATA = pathlib.Path(os.path.expandvars("%APPDATA%")) / "JARVIS"
_MEMORY_FILE = _APPDATA / "memory.json"

_DEFAULT_MEMORY = {
    "user": {
        "name": None,
        "preferred_name": None,
        "wake_times": [],
        "sleep_times": [],
        "mood_history": [],
        "detected_city": None,
    },
    "preferences": {
        "frequent_apps": {},
        "frequent_commands": {},
        "music_habit": False,
        "work_start_hour": None,
        "work_end_hour": None,
        "preferred_language": "en",
        "response_brevity": "normal",
    },
    "projects": [],
    "facts": [],
    "contacts": {},
    "reminders_pending": [],
    "last_session": None,
    "total_sessions": 0,
    "total_interactions": 0,
    "relationship_level": 0,
    "routines": {},
}

# Sessions needed to advance each relationship level
_LEVEL_THRESHOLDS = {0: 0, 1: 5, 2: 20, 3: 50, 4: 100, 5: 200}

MAX_FACTS = 100

_NAME_PATTERNS = [
    r"(?:i'm|i am|my name is|call me)\s+([A-Z][a-z]{1,20})",
    r"(?:i'm|i am)\s+([A-Z][a-z]{1,20})\b",
]


class MemoryEngine:
    """Long-term personality memory for JARVIS."""

    def __init__(self, router=None):
        self._router = router  # LLMRouter for passive fact extraction
        self._data: dict = {}
        self._lock = threading.Lock()
        self._dirty = False
        self._last_save = 0.0
        self._load()

    # ------------------------------------------------------------------ #
    #  Public getters                                                      #
    # ------------------------------------------------------------------ #

    def get(self, key: str, default: Any = None) -> Any:
        with self._lock:
            return self._data.get(key, default)

    def get_context_string(self) -> str:
        """Return a compact string of key facts for the LLM system prompt."""
        with self._lock:
            lines = []
            user = self._data.get("user", {})
            name = user.get("name")
            if name:
                lines.append(f"User's name: {name}")
            city = user.get("detected_city")
            if city:
                lines.append(f"User's city: {city}")
            facts = self._data.get("facts", [])[-10:]  # last 10 facts
            for fact in facts:
                if isinstance(fact, dict):
                    lines.append(f"- {fact.get('content', '')}")
                elif isinstance(fact, str):
                    lines.append(f"- {fact}")
            sessions = self._data.get("total_sessions", 0)
            lines.append(f"Total sessions: {sessions}")
            return "\n".join(lines)

    def get_yesterday_tail(self) -> str:
        """Return last user question from yesterday — delegates to log helper."""
        try:
            from memory.conversation_engine import get_yesterday_tail_from_logs
            return get_yesterday_tail_from_logs()
        except Exception:
            return self._data.get("last_session_summary", "")

    # ------------------------------------------------------------------ #
    #  Session tracking                                                    #
    # ------------------------------------------------------------------ #

    def start_session(self) -> None:
        with self._lock:
            self._data["total_sessions"] = self._data.get("total_sessions", 0) + 1
            self._data["last_session"] = datetime.now().isoformat()
            self._update_relationship_level()
            self._dirty = True
        self._save_debounced()

    def end_session(self, summary: str = "") -> None:
        with self._lock:
            if summary:
                self._data["last_session_summary"] = summary
            self._dirty = True
        self._save_now()

    def record_interaction(self) -> None:
        with self._lock:
            self._data["total_interactions"] = (
                self._data.get("total_interactions", 0) + 1
            )
            self._dirty = True

    # ------------------------------------------------------------------ #
    #  Name learning                                                       #
    # ------------------------------------------------------------------ #

    def learn_name(self, text: str) -> Optional[str]:
        """Extract user name from text if present. Returns name or None."""
        import re
        for pattern in _NAME_PATTERNS:
            m = re.search(pattern, text, re.IGNORECASE)
            if m:
                name = m.group(1).strip().capitalize()
                with self._lock:
                    self._data.setdefault("user", {})["name"] = name
                    self._dirty = True
                self._save_debounced()
                return name
        return None

    def set_name(self, name: str) -> None:
        with self._lock:
            self._data.setdefault("user", {})["name"] = name
            self._dirty = True
        self._save_debounced()

    # ------------------------------------------------------------------ #
    #  Fact learning                                                       #
    # ------------------------------------------------------------------ #

    def add_fact(self, content: str) -> None:
        with self._lock:
            facts = self._data.setdefault("facts", [])
            # Avoid duplicates
            existing = [
                f.get("content", f) if isinstance(f, dict) else f
                for f in facts
            ]
            if content in existing:
                return
            facts.append({
                "content": content,
                "added": datetime.now().isoformat(),
                "references": 1,
            })
            # Prune to MAX_FACTS (remove oldest least-referenced)
            if len(facts) > MAX_FACTS:
                facts.sort(key=lambda f: (
                    f.get("references", 1) if isinstance(f, dict) else 1
                ))
                self._data["facts"] = facts[-MAX_FACTS:]
            self._dirty = True
        self._save_debounced()

    def learn_from_exchange(self, user_text: str, assistant_reply: str) -> None:
        """Passively extract learnable facts from a conversation exchange."""
        # Try name learning first (no LLM needed)
        self.learn_name(user_text)
        self.record_interaction()

        # Use LLM for deeper fact extraction only if router available
        if not self._router:
            return

        # Simple rule-based fact detection (no LLM needed for common cases)
        self._simple_fact_detection(user_text)

    def _simple_fact_detection(self, text: str) -> None:
        tl = text.lower()
        fact = None

        if "i prefer dark mode" in tl or "i like dark mode" in tl:
            fact = "User prefers dark mode"
        elif "i prefer light mode" in tl:
            fact = "User prefers light mode"
        elif "i usually work" in tl:
            fact = f"Work habit: {text[:60]}"
        elif "i'm working on" in tl or "i am working on" in tl:
            import re
            m = re.search(r"working on\s+(.{5,50})", tl)
            if m:
                fact = f"Working on: {m.group(1)}"
        elif "i hate" in tl and len(text) < 80:
            fact = f"User dislikes: {text[tl.find('i hate') + 7:][:40]}"
        elif "i love" in tl and len(text) < 80:
            fact = f"User likes: {text[tl.find('i love') + 7:][:40]}"

        if fact:
            self.add_fact(fact)

    # ------------------------------------------------------------------ #
    #  Routines                                                            #
    # ------------------------------------------------------------------ #

    def add_routine(self, name: str, steps: list) -> None:
        with self._lock:
            self._data.setdefault("routines", {})[name] = {
                "steps": steps,
                "created": datetime.now().isoformat(),
            }
            self._dirty = True
        self._save_debounced()

    def get_routine(self, name: str) -> Optional[list]:
        with self._lock:
            routine = self._data.get("routines", {}).get(name)
            return routine.get("steps") if routine else None

    # ------------------------------------------------------------------ #
    #  Relationship level                                                  #
    # ------------------------------------------------------------------ #

    def _update_relationship_level(self) -> None:
        sessions = self._data.get("total_sessions", 0)
        level = 0
        for lvl, threshold in sorted(_LEVEL_THRESHOLDS.items()):
            if sessions >= threshold:
                level = lvl
        self._data["relationship_level"] = level

    # ------------------------------------------------------------------ #
    #  Persistence                                                         #
    # ------------------------------------------------------------------ #

    def _load(self) -> None:
        try:
            _APPDATA.mkdir(parents=True, exist_ok=True)
            if _MEMORY_FILE.exists():
                with open(_MEMORY_FILE, "r", encoding="utf-8") as f:
                    loaded = json.load(f)
                # Deep merge with defaults
                self._data = {**_DEFAULT_MEMORY, **loaded}
                # Ensure nested dicts are merged too
                for key in ("user", "preferences"):
                    if key in _DEFAULT_MEMORY:
                        self._data[key] = {
                            **_DEFAULT_MEMORY[key],
                            **loaded.get(key, {}),
                        }
            else:
                self._data = {k: v.copy() if isinstance(v, (dict, list)) else v
                              for k, v in _DEFAULT_MEMORY.items()}
                self._save_now()
        except Exception as e:
            print(f"[MemoryEngine] Load error: {e}")
            self._data = {k: v.copy() if isinstance(v, (dict, list)) else v
                          for k, v in _DEFAULT_MEMORY.items()}

    def _save_debounced(self) -> None:
        now = time.monotonic()
        if now - self._last_save < 30:
            return
        self._save_now()

    def _save_now(self) -> None:
        def _worker():
            try:
                _APPDATA.mkdir(parents=True, exist_ok=True)
                with self._lock:
                    data_copy = json.loads(json.dumps(self._data))
                with open(_MEMORY_FILE, "w", encoding="utf-8") as f:
                    json.dump(data_copy, f, indent=2, ensure_ascii=False)
                self._last_save = time.monotonic()
                self._dirty = False
            except Exception as e:
                print(f"[MemoryEngine] Save error: {e}")

        threading.Thread(target=_worker, daemon=True).start()

    # ------------------------------------------------------------------ #
    #  Convenience properties / compat API used by Brain                 #
    # ------------------------------------------------------------------ #

    @property
    def relationship_level(self) -> int:
        return self._data.get("relationship_level", 0)

    def get_context(self) -> dict:
        """Return a dict snapshot of key memory facts for the system prompt."""
        with self._lock:
            user = self._data.get("user", {})
            return {
                "name":               user.get("name"),
                "city":               user.get("detected_city"),
                "relationship_level": self._data.get("relationship_level", 0),
                "total_sessions":     self._data.get("total_sessions", 0),
                "facts":              self._data.get("facts", [])[-10:],
                "preferences":        self._data.get("preferences", {}),
            }

    def remember(self, key: str, value: str) -> None:
        """Store a labelled fact (key: value) in the fact list."""
        self.add_fact(f"{key}: {value}")

    def recall(self, key: str) -> Optional[str]:
        """Return the most recent fact whose content starts with key:, or None."""
        prefix = f"{key}:"
        with self._lock:
            for fact in reversed(self._data.get("facts", [])):
                content = fact.get("content", fact) if isinstance(fact, dict) else fact
                if content.startswith(prefix):
                    return content[len(prefix):].strip()
        return None

    def wipe(self) -> None:
        """Full memory wipe — requires confirmation before calling."""
        with self._lock:
            self._data = {k: v.copy() if isinstance(v, (dict, list)) else v
                          for k, v in _DEFAULT_MEMORY.items()}
            self._dirty = True
        self._save_now()
        print("[MemoryEngine] Memory wiped.")
