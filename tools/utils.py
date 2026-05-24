"""Smart utilities — date/time, reminders, safe calculator."""

import ast
import math
import os
import json
import re
import threading
import time
import datetime

_DATA_DIR = os.path.join(os.environ.get("APPDATA", ""), "JARVIS")
os.makedirs(_DATA_DIR, exist_ok=True)
_REMINDERS_FILE = os.path.join(_DATA_DIR, "reminders.json")

# ── Response micro-cache ────────────────────────────────────────────── #

_CACHE: dict = {}


def _cached(key: str, fn, ttl_sec: float):
    """Call fn() and cache result for ttl_sec seconds."""
    now = time.monotonic()
    entry = _CACHE.get(key)
    if entry and now - entry[0] < ttl_sec:
        return entry[1]
    result = fn()
    _CACHE[key] = (now, result)
    return result

try:
    from tzlocal import get_localzone
    _TZLOCAL = True
except ImportError:
    _TZLOCAL = False

try:
    from plyer import notification as _notifier
    _PLYER = True
except ImportError:
    _PLYER = False


# ------------------------------------------------------------------ #
#  Date / time                                                         #
# ------------------------------------------------------------------ #

def get_datetime() -> str:
    def _fetch():
        now = datetime.datetime.now()
        tz = "local time"
        if _TZLOCAL:
            try:
                tz = str(get_localzone())
            except Exception:
                pass
        return (
            f"It is {now.strftime('%A, %B %d %Y')} "
            f"at {now.strftime('%I:%M %p')} ({tz})."
        )
    return _cached("datetime", _fetch, 5.0)


# ------------------------------------------------------------------ #
#  Reminders                                                           #
# ------------------------------------------------------------------ #

_active_timers: list[threading.Timer] = []


def _load_reminders() -> list:
    try:
        with open(_REMINDERS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []


def _save_reminders(data: list) -> None:
    try:
        with open(_REMINDERS_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
    except Exception:
        pass


def _schedule_reminder(entry: dict, speak_fn=None, notify_fn=None) -> None:
    """Schedule a threading.Timer for one reminder entry."""
    import winsound
    due_iso = entry.get("due_iso", "")
    message = entry.get("message", "")
    try:
        due = datetime.datetime.fromisoformat(due_iso)
        secs = max(0, (due - datetime.datetime.now()).total_seconds())
    except Exception:
        return

    def _fire():
        entry["fired"] = True
        reminders = _load_reminders()
        for r in reminders:
            if r.get("id") == entry.get("id"):
                r["fired"] = True
        _save_reminders(reminders)
        try:
            winsound.Beep(880, 600)
        except Exception:
            pass
        if _PLYER:
            try:
                _notifier.notify(title="JARVIS Reminder", message=message,
                                 app_name="JARVIS", timeout=10)
            except Exception:
                pass
        if speak_fn:
            speak_fn(f"Reminder: {message}")
        if notify_fn:
            notify_fn(f"Reminder: {message}")

    t = threading.Timer(secs, _fire)
    t.daemon = True
    t.start()
    _active_timers.append(t)


def set_reminder(
    message: str,
    minutes: float,
    speak_fn=None,
    notify_fn=None,
) -> str:
    secs = float(minutes) * 60
    due = datetime.datetime.now() + datetime.timedelta(seconds=secs)
    entry = {
        "id":      f"{time.time():.0f}",
        "message": message,
        "due_iso": due.isoformat(),
        "fired":   False,
    }
    # Persist so it survives restarts
    reminders = _load_reminders()
    reminders.append(entry)
    _save_reminders(reminders)
    # Schedule in-process timer
    _schedule_reminder(entry, speak_fn=speak_fn, notify_fn=notify_fn)

    plural = "s" if minutes != 1 else ""
    return f"Reminder set for {minutes} minute{plural}: {message}"


def check_pending_reminders(speak_fn=None, notify_fn=None) -> list[str]:
    """Reschedule unfired future reminders; speak any that fired while offline.

    Returns list of overdue reminder messages spoken.
    """
    reminders = _load_reminders()
    now = datetime.datetime.now()
    spoke: list[str] = []
    changed = False
    for entry in reminders:
        if entry.get("fired"):
            continue
        try:
            due = datetime.datetime.fromisoformat(entry["due_iso"])
        except Exception:
            continue
        if due <= now:
            # Overdue — fire immediately
            entry["fired"] = True
            changed = True
            msg = entry.get("message", "")
            spoke.append(msg)
            if speak_fn:
                speak_fn(f"Reminder from earlier: {msg}")
        else:
            # Future — reschedule
            _schedule_reminder(entry, speak_fn=speak_fn, notify_fn=notify_fn)
    if changed:
        _save_reminders(reminders)
    return spoke


# ------------------------------------------------------------------ #
#  Calculator (safe AST eval + unit conversions)                      #
# ------------------------------------------------------------------ #

_SAFE_NAMES = {
    "abs": abs, "round": round, "min": min, "max": max,
    "sqrt": math.sqrt, "sin": math.sin, "cos": math.cos, "tan": math.tan,
    "log": math.log, "log10": math.log10,
    "floor": math.floor, "ceil": math.ceil,
    "pi": math.pi, "e": math.e,
}

_ALLOWED_TYPES = (
    ast.Expression, ast.BinOp, ast.UnaryOp, ast.Constant,
    ast.Add, ast.Sub, ast.Mult, ast.Div, ast.FloorDiv, ast.Mod, ast.Pow,
    ast.USub, ast.UAdd, ast.Call, ast.Name,
)

_UNIT_CONVERSIONS = [
    (r"([\d.]+)\s*km\s+(?:to|in)\s+miles?", lambda v: f"{v} km = {v * 0.621371:.4f} miles"),
    (r"([\d.]+)\s*miles?\s+(?:to|in)\s+km", lambda v: f"{v} miles = {v * 1.60934:.4f} km"),
    (r"([\d.]+)\s*kg\s+(?:to|in)\s+(?:lbs?|pounds?)", lambda v: f"{v} kg = {v * 2.20462:.4f} lbs"),
    (r"([\d.]+)\s*(?:lbs?|pounds?)\s+(?:to|in)\s+kg", lambda v: f"{v} lbs = {v * 0.453592:.4f} kg"),
    (r"([\d.]+)\s*(?:°?c|celsius)\s+(?:to|in)\s+(?:°?f|fahrenheit)", lambda v: f"{v}°C = {v * 9/5 + 32:.2f}°F"),
    (r"([\d.]+)\s*(?:°?f|fahrenheit)\s+(?:to|in)\s+(?:°?c|celsius)", lambda v: f"{v}°F = {(v-32)*5/9:.2f}°C"),
    (r"([\d.]+)\s*m(?:eters?)?\s+(?:to|in)\s+(?:ft|feet)", lambda v: f"{v} m = {v * 3.28084:.4f} ft"),
    (r"([\d.]+)\s*(?:ft|feet)\s+(?:to|in)\s+m(?:eters?)?", lambda v: f"{v} ft = {v * 0.3048:.4f} m"),
    (r"([\d.]+)\s*(?:l|liters?)\s+(?:to|in)\s+(?:gal|gallons?)", lambda v: f"{v} L = {v * 0.264172:.4f} gal"),
    (r"([\d.]+)\s*(?:gal|gallons?)\s+(?:to|in)\s+(?:l|liters?)", lambda v: f"{v} gal = {v * 3.78541:.4f} L"),
]


def calculate(expression: str) -> str:
    expr_lower = expression.lower().strip()

    # Check unit conversions first
    for pattern, formatter in _UNIT_CONVERSIONS:
        m = re.search(pattern, expr_lower)
        if m:
            try:
                val = float(m.group(1))
                return formatter(val)
            except Exception:
                pass

    # Safe AST math evaluation
    try:
        tree = ast.parse(expression, mode="eval")
        for node in ast.walk(tree):
            if not isinstance(node, _ALLOWED_TYPES):
                return f"Expression not allowed (unsafe node: {type(node).__name__})."
        result = eval(
            compile(tree, "<string>", "eval"),
            {"__builtins__": {}},
            _SAFE_NAMES,
        )
        return f"{expression} = {result}"
    except ZeroDivisionError:
        return "Division by zero."
    except Exception as e:
        return f"Could not calculate: {e}"
