"""tools/meeting.py — JARVIS leads meetings: agenda, timer, notes, action items.

Also handles:
  • Interview practice mode (JARVIS as interviewer)
  • Focus/Pomodoro timer with spoken check-ins
"""

import json
import os
import threading
import time
from datetime import datetime

_DATA_DIR = os.path.join(os.environ.get("APPDATA", ""), "JARVIS")
_MEETINGS_FILE = os.path.join(_DATA_DIR, "meetings.json")

_meeting:          dict = {}   # active meeting session
_focus:            dict = {}   # active focus session
_interview_active: dict = {}   # non-empty while interview mode is on
_speak_fn    = None
_pause_hook  = None   # callable() — pause background services during interview
_resume_hook = None   # callable() — resume background services after interview


def _set_speak(fn) -> None:
    global _speak_fn
    _speak_fn = fn


def _set_busy_hooks(pause_fn, resume_fn) -> None:
    global _pause_hook, _resume_hook
    _pause_hook  = pause_fn
    _resume_hook = resume_fn


def _say(text: str) -> None:
    if _speak_fn:
        _speak_fn(text)


def _elapsed(since_iso: str) -> str:
    try:
        delta = datetime.now() - datetime.fromisoformat(since_iso)
        mins = int(delta.total_seconds() / 60)
        return f"{mins} minute{'s' if mins != 1 else ''}"
    except Exception:
        return ""


# ================================================================== #
#  Meeting Leader                                                      #
# ================================================================== #

def start_meeting(title: str = "Meeting", agenda: str = "",
                  participants: str = "") -> str:
    """Begin a meeting session. JARVIS tracks agenda, time, notes, action items."""
    _meeting.clear()
    _meeting.update({
        "title":        title,
        "started":      datetime.now().isoformat(),
        "participants": [p.strip() for p in participants.split(",") if p.strip()],
        "agenda":       [a.strip() for a in agenda.split(",") if a.strip()],
        "current_item": 0,
        "action_items": [],
        "notes":        [],
        "item_times":   {},
    })
    if _meeting["agenda"]:
        _meeting["item_times"][0] = datetime.now().isoformat()

    lines = [f"Meeting '{title}' started at {datetime.now().strftime('%H:%M')}."]
    if _meeting["participants"]:
        lines.append(f"Attendees: {', '.join(_meeting['participants'])}.")
    if _meeting["agenda"]:
        items = ", ".join(f"{i+1}. {a}" for i, a in enumerate(_meeting["agenda"]))
        lines.append(f"Agenda: {items}.")
        lines.append(f"Starting with item 1: {_meeting['agenda'][0]}.")
    else:
        lines.append("No agenda set — free-form meeting.")
    return " ".join(lines)


def next_agenda_item() -> str:
    """Advance to the next agenda item."""
    if not _meeting:
        return "No active meeting. Start one with start_meeting."
    agenda = _meeting.get("agenda", [])
    if not agenda:
        return "No agenda items are set for this meeting."
    cur = _meeting["current_item"]
    if cur >= len(agenda) - 1:
        return f"Already on the last agenda item: '{agenda[cur]}'. Call end_meeting when done."
    cur += 1
    _meeting["current_item"] = cur
    _meeting["item_times"][cur] = datetime.now().isoformat()
    return f"Moving to item {cur + 1} of {len(agenda)}: '{agenda[cur]}'."


def add_meeting_note(note: str) -> str:
    """Record a note during the meeting."""
    if not _meeting:
        return "No active meeting."
    ts = datetime.now().strftime("%H:%M")
    entry = f"[{ts}] {note}"
    _meeting["notes"].append(entry)
    return f"Note recorded."


def record_action_item(action: str, owner: str = "") -> str:
    """Add an action item with optional owner."""
    if not _meeting:
        return "No active meeting."
    _meeting["action_items"].append({
        "action": action,
        "owner":  owner,
        "time":   datetime.now().strftime("%H:%M"),
    })
    suffix = f" — assigned to {owner}" if owner else ""
    return f"Action item logged: '{action}'{suffix}."


def meeting_status() -> str:
    """Report current meeting state."""
    if not _meeting:
        return "No active meeting."
    agenda   = _meeting.get("agenda", [])
    cur      = _meeting.get("current_item", 0)
    elapsed  = _elapsed(_meeting["started"])
    n_action = len(_meeting["action_items"])
    n_notes  = len(_meeting["notes"])
    cur_item = f"Current item: '{agenda[cur]}'. " if agenda else ""
    return (f"Meeting '{_meeting['title']}' — {elapsed} in. "
            f"{cur_item}{n_action} action items, {n_notes} notes.")


def end_meeting() -> str:
    """End the meeting and generate a summary saved to Desktop."""
    if not _meeting:
        return "No active meeting to end."

    elapsed = _elapsed(_meeting["started"])
    lines   = [
        f"MEETING SUMMARY",
        f"{'=' * 40}",
        f"Title:    {_meeting['title']}",
        f"Date:     {datetime.now().strftime('%Y-%m-%d')}",
        f"Time:     {datetime.fromisoformat(_meeting['started']).strftime('%H:%M')} – {datetime.now().strftime('%H:%M')} ({elapsed})",
    ]
    if _meeting["participants"]:
        lines.append(f"Attendees: {', '.join(_meeting['participants'])}")

    if _meeting["agenda"]:
        lines += ["", "AGENDA", *[f"  {i+1}. {a}" for i, a in enumerate(_meeting["agenda"])]]

    if _meeting["notes"]:
        lines += ["", "NOTES", *[f"  {n}" for n in _meeting["notes"]]]

    if _meeting["action_items"]:
        lines += ["", "ACTION ITEMS"]
        for ai in _meeting["action_items"]:
            owner = f" [{ai['owner']}]" if ai["owner"] else ""
            lines.append(f"  • [{ai['time']}] {ai['action']}{owner}")

    summary = "\n".join(lines)

    # Persist
    try:
        os.makedirs(_DATA_DIR, exist_ok=True)
        all_m = []
        if os.path.exists(_MEETINGS_FILE):
            with open(_MEETINGS_FILE, encoding="utf-8") as f:
                all_m = json.load(f)
        all_m.append({**_meeting, "ended": datetime.now().isoformat()})
        with open(_MEETINGS_FILE, "w", encoding="utf-8") as f:
            json.dump(all_m, f, indent=2, ensure_ascii=False)
    except Exception:
        pass

    # Save to Desktop
    desktop = os.path.join(os.path.expanduser("~"), "Desktop")
    fname   = f"Meeting_{datetime.now().strftime('%Y-%m-%d_%H%M')}.txt"
    try:
        with open(os.path.join(desktop, fname), "w", encoding="utf-8") as f:
            f.write(summary)
        saved = f"Notes saved to Desktop as {fname}."
    except Exception:
        saved = ""

    _meeting.clear()
    return f"Meeting ended ({elapsed}). {saved}\n\n{summary}"


# ================================================================== #
#  Interview Practice Mode                                            #
# ================================================================== #

def start_interview(role: str = "Software Engineer",
                    difficulty: str = "medium",
                    num_questions: int = 0) -> str:
    """Put JARVIS into interviewer mode for practice.

    num_questions: how many questions to ask total (0 = auto, 6-8).
    """
    _interview_active.clear()
    _interview_active.update({
        "role": role,
        "difficulty": difficulty,
        "q_count": 0,
        "num_questions": int(num_questions),
    })
    if _pause_hook:
        try:
            _pause_hook()
        except Exception:
            pass
    q_note = f" Ask exactly {num_questions} question(s) total, then wrap up." if num_questions else ""
    return (
        f"Interview mode activated for {role} ({difficulty} level).{q_note} "
        "Welcome the candidate warmly and ask your first interview question now."
    )


def end_interview() -> str:
    """End interview practice and clear the interviewer persona."""
    _interview_active.clear()
    if _resume_hook:
        try:
            _resume_hook()
        except Exception:
            pass
    return (
        "The interview session is now over. "
        "Give the candidate a comprehensive performance review: "
        "strengths, areas to improve, and a final score out of 10."
    )


# ================================================================== #
#  Focus / Pomodoro Mode                                              #
# ================================================================== #

def start_focus_session(minutes: int = 25, task: str = "") -> str:
    """Start a Pomodoro-style focus session with JARVIS check-ins."""
    _focus.clear()
    _focus.update({
        "task":    task or "focused work",
        "minutes": int(minutes),
        "started": datetime.now().isoformat(),
    })

    def _focus_done():
        time.sleep(int(minutes) * 60)
        if _focus:
            _say(f"Focus session complete! {int(minutes)} minutes of {task or 'work'} done. "
                 "Time for a short break.")

    def _halfway():
        time.sleep(int(minutes) * 30)
        if _focus:
            _say(f"Halfway through your focus session — {int(minutes) // 2} minutes down, "
                 f"{int(minutes) // 2} to go. Keep it up.")

    threading.Thread(target=_halfway,    daemon=True).start()
    threading.Thread(target=_focus_done, daemon=True).start()

    task_str = f" on {task}" if task else ""
    return (f"Focus session started — {minutes} minutes{task_str}. "
            "Notifications silenced. I'll check in at the halfway mark and when done.")


def end_focus_session() -> str:
    """Cancel the active focus session."""
    if not _focus:
        return "No active focus session."
    task    = _focus.get("task", "work")
    elapsed = _elapsed(_focus["started"])
    _focus.clear()
    return f"Focus session on '{task}' ended after {elapsed}."


def focus_status() -> str:
    """Report how long the current focus session has been running."""
    if not _focus:
        return "No active focus session."
    elapsed = _elapsed(_focus["started"])
    mins    = _focus.get("minutes", 25)
    return f"Focus session on '{_focus['task']}' — {elapsed} of {mins} minutes."


def is_interview_active() -> bool:
    """Returns True while interview mode is running."""
    return bool(_interview_active)
