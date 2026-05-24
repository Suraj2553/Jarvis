"""Personal-assistant tools — the things an alive PA actually does.

Notes     : persistent timestamped notepad
Memory    : permanent key-fact storage (recall anytime)
Timers    : speaks when done (distinct from reminder)
Media     : play/pause/skip via OS media keys
Volume    : relative up/down + mute toggle
Network   : current WiFi SSID + public IP
"""

import json
import os
import re
import subprocess
import threading
import time
from datetime import datetime

_DATA_DIR = os.path.join(os.environ.get("APPDATA", ""), "JARVIS")
os.makedirs(_DATA_DIR, exist_ok=True)
_NOTES_FILE  = os.path.join(_DATA_DIR, "notes.txt")
_MEMORY_FILE = os.path.join(_DATA_DIR, "memory.json")

# Injected by main._build_tools so the timer can speak when done
_speak_fn = None

def _set_speak(fn) -> None:
    global _speak_fn
    _speak_fn = fn


# ------------------------------------------------------------------ #
#  Timer — speaks aloud when time is up                               #
# ------------------------------------------------------------------ #

def set_timer(minutes: float = 5.0, label: str = "timer") -> str:
    minutes = float(minutes)

    def _fire():
        try:
            import winsound
            for freq, ms in ((880, 350), (1047, 350), (1319, 500)):
                winsound.Beep(freq, ms)
                time.sleep(0.08)
        except Exception:
            pass
        if _speak_fn:
            _speak_fn(f"Sir, your {label} is done.")

    t = threading.Timer(minutes * 60, _fire)
    t.daemon = True
    t.start()

    if minutes < 1:
        return f"Timer set for {int(minutes*60)} seconds: {label}."
    mins_str = f"{minutes:.0f}" if minutes == int(minutes) else f"{minutes}"
    plural = "s" if minutes != 1 else ""
    return f"Timer set for {mins_str} minute{plural}: {label}."


# ------------------------------------------------------------------ #
#  Notes — timestamped notepad                                        #
# ------------------------------------------------------------------ #

def add_note(content: str) -> str:
    ts = datetime.now().strftime("%d %b %H:%M")
    with open(_NOTES_FILE, "a", encoding="utf-8") as f:
        f.write(f"[{ts}] {content}\n")
    return f"Noted, sir: {content}"


def read_notes() -> str:
    if not os.path.exists(_NOTES_FILE):
        return "No notes on file, sir."
    lines = [l.strip() for l in open(_NOTES_FILE, encoding="utf-8") if l.strip()]
    if not lines:
        return "Notes are empty, sir."
    recent = lines[-8:]
    return "Your notes: " + ". ".join(recent) + "."


def clear_notes() -> str:
    open(_NOTES_FILE, "w", encoding="utf-8").close()
    return "Notes cleared, sir."


# ------------------------------------------------------------------ #
#  Memory — persistent key-value facts                                #
# ------------------------------------------------------------------ #

def _load_mem() -> list[dict]:
    if not os.path.exists(_MEMORY_FILE):
        return []
    try:
        data = json.load(open(_MEMORY_FILE, encoding="utf-8"))
        if isinstance(data, list):
            return data
        return []  # corrupt / old dict format — start fresh
    except Exception:
        return []


def _save_mem(data: list[dict]) -> None:
    json.dump(data, open(_MEMORY_FILE, "w", encoding="utf-8"),
              indent=2, ensure_ascii=False)


def remember(fact: str) -> str:
    mem = _load_mem()
    ts  = datetime.now().strftime("%Y-%m-%d %H:%M")
    mem.append({"fact": fact, "saved": ts})
    _save_mem(mem)
    return f"Committed to memory, sir: {fact}"


def recall(query: str = "") -> str:
    mem = _load_mem()
    if not mem:
        return "Nothing in memory yet, sir."
    facts = [e["fact"] for e in mem if "fact" in e]
    if query:
        q = query.lower()
        matches = [f for f in facts if q in f.lower()]
        if matches:
            return "I recall: " + ". ".join(matches[-5:]) + "."
        return f"Nothing matching '{query}' in my memory, sir."
    return "I remember: " + ". ".join(facts[-6:]) + "."


def forget_all() -> str:
    _save_mem([])
    return "Memory wiped, sir."


# ------------------------------------------------------------------ #
#  Media controls — OS media keys                                     #
# ------------------------------------------------------------------ #

def play_pause_media() -> str:
    import pyautogui
    pyautogui.press("playpause")
    return "Play/pause toggled."


def next_track() -> str:
    import pyautogui
    pyautogui.press("nexttrack")
    return "Skipped to next track."


def prev_track() -> str:
    import pyautogui
    pyautogui.press("prevtrack")
    return "Back to previous track."


def stop_media() -> str:
    import pyautogui
    pyautogui.press("stop")
    return "Media stopped."


# ------------------------------------------------------------------ #
#  Volume — relative + mute                                           #
# ------------------------------------------------------------------ #

try:
    from ctypes import cast, POINTER
    from comtypes import CLSCTX_ALL
    from pycaw.pycaw import AudioUtilities, IAudioEndpointVolume
    _PYCAW = True
except Exception:
    _PYCAW = False


def _vol_iface():
    dev = AudioUtilities.GetSpeakers()
    iface = dev.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None)
    return cast(iface, POINTER(IAudioEndpointVolume))


def volume_up(step: int = 10) -> str:
    if not _PYCAW:
        return "Volume control unavailable."
    try:
        v   = _vol_iface()
        cur = round(v.GetMasterVolumeLevelScalar() * 100)
        new = min(100, cur + int(step))
        v.SetMasterVolumeLevelScalar(new / 100.0, None)
        return f"Volume raised to {new}%."
    except Exception as e:
        return f"Volume error: {e}"


def volume_down(step: int = 10) -> str:
    if not _PYCAW:
        return "Volume control unavailable."
    try:
        v   = _vol_iface()
        cur = round(v.GetMasterVolumeLevelScalar() * 100)
        new = max(0, cur - int(step))
        v.SetMasterVolumeLevelScalar(new / 100.0, None)
        return f"Volume reduced to {new}%."
    except Exception as e:
        return f"Volume error: {e}"


def mute_toggle() -> str:
    if not _PYCAW:
        return "Volume control unavailable."
    try:
        v     = _vol_iface()
        muted = v.GetMute()
        v.SetMute(not muted, None)
        return "Muted." if not muted else "Unmuted."
    except Exception as e:
        return f"Mute error: {e}"


# ------------------------------------------------------------------ #
#  Network info                                                        #
# ------------------------------------------------------------------ #

def get_connected_wifi() -> str:
    r = subprocess.run(
        ["netsh", "wlan", "show", "interfaces"],
        capture_output=True, text=True,
    )
    for line in r.stdout.splitlines():
        if "SSID" in line and "BSSID" not in line:
            parts = line.split(":", 1)
            if len(parts) == 2 and parts[1].strip():
                return f"Connected to '{parts[1].strip()}'."
    return "Not connected to any WiFi network."


def get_public_ip() -> str:
    import requests as _r
    try:
        ip = _r.get("https://api.ipify.org", timeout=6).text.strip()
        return f"Your public IP address is {ip}."
    except Exception:
        return "Could not retrieve public IP right now."


# ------------------------------------------------------------------ #
#  Conversation export                                                 #
# ------------------------------------------------------------------ #

def export_conversation(brain=None) -> str:
    """Save the current conversation history to Desktop as a txt file."""
    desktop = os.path.join(os.path.expanduser("~"), "Desktop")
    date_str = datetime.now().strftime("%Y-%m-%d_%H-%M")
    path = os.path.join(desktop, f"JARVIS_chat_{date_str}.txt")
    lines = []
    if brain and hasattr(brain, "_history"):
        for msg in brain._history:
            role = msg.get("role", "")
            content = msg.get("content", "")
            if role == "user":
                lines.append(f"[You] {content}")
            elif role == "assistant":
                lines.append(f"[JARVIS] {content}")
    if not lines:
        return "No conversation to export yet."
    try:
        with open(path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))
        return f"Conversation saved to Desktop as JARVIS_chat_{date_str}.txt."
    except Exception as e:
        return f"Export failed: {e}"


# ------------------------------------------------------------------ #
#  Contact management                                                  #
# ------------------------------------------------------------------ #

_CONTACTS_FILE = os.path.join(_DATA_DIR, "contacts.json")


def _load_contacts() -> dict:
    try:
        with open(_CONTACTS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _save_contacts(data: dict) -> None:
    with open(_CONTACTS_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def remember_contact(name: str, phone_number: str) -> str:
    contacts = _load_contacts()
    contacts[name.lower()] = {"name": name, "phone": phone_number}
    _save_contacts(contacts)
    return f"Contact saved: {name} — {phone_number}."


def list_contacts() -> str:
    contacts = _load_contacts()
    if not contacts:
        return "No contacts saved yet."
    entries = [f"{v['name']}: {v['phone']}" for v in contacts.values()]
    return "Contacts: " + ", ".join(entries) + "."


def forget_contact(name: str) -> str:
    contacts = _load_contacts()
    key = name.lower()
    if key in contacts:
        del contacts[key]
        _save_contacts(contacts)
        return f"Contact '{name}' removed."
    return f"No contact named '{name}' found."
