"""demo_launcher.py — Clean demo recording launcher.

Sequence (slow-paced, rate-limit safe):
  Boot → JARVIS greeting → Act 1 JARVIS EN → Act 2 Friday EN → Act 3 Friday HI
"""

import subprocess
import time
import os
import ctypes

JARVIS_DIR = os.path.dirname(os.path.abspath(__file__))
PYTHON     = os.path.join(JARVIS_DIR, ".venv", "Scripts", "python.exe")

# Wait for full boot + greeting to finish speaking before first prompt
BOOT_WAIT = 55

# (prompt_or_command, seconds_to_wait_AFTER_sending)
# Wait includes response + speaking time + breathing room.
# Purely local tools (weather, battery, calculator) need less wait.
# LLM-only responses get 35s to avoid Groq rate-limit.
SEQUENCE = [
    # ── Act 1: JARVIS (English) ────────────────────────────────── #
    ("What's the weather right now?",                            30),  # Open-Meteo
    ("What time is it and what's today's date?",                 18),  # local clock
    ("How's my system running — CPU and battery?",               18),  # local stats
    ("Give me one interesting fact about Iron Man's arc reactor", 35),  # LLM

    # ── Switch to Friday ──────────────────────────────────────── #
    ("/friday",                                                   14),

    # ── Act 2: Friday (English) ───────────────────────────────── #
    ("Friday, what's the stock price of NVIDIA right now?",      28),  # web tool
    ("Tell me a witty one-liner in your Friday style",           30),  # LLM
    ("Calculate compound interest on 50000 at 8 percent "
     "annually for 3 years",                                     18),  # calculator
    ("Give me one sharp piece of advice for a developer",        32),  # LLM

    # ── Switch Friday to Hindi ────────────────────────────────── #
    ("/hindi",                                                    14),

    # ── Act 3: Friday (Hindi) ─────────────────────────────────── #
    ("Aaj ka mausam kaisa hai?",                                 30),  # Open-Meteo
    ("Mujhe ek motivational quote do",                           32),  # LLM
    ("Abhi time kya hai?",                                       16),  # local
    ("Tony Stark ke baare mein ek interesting baat batao",       35),  # LLM

    # ── End ───────────────────────────────────────────────────── #
    ("/quit",                                                      5),
]


def minimize_all_windows():
    try:
        user32 = ctypes.windll.user32
        SW_MINIMIZE = 6
        EnumWindowsProc = ctypes.WINFUNCTYPE(
            ctypes.c_bool, ctypes.POINTER(ctypes.c_int), ctypes.POINTER(ctypes.c_int)
        )
        def _cb(hwnd, _):
            if user32.IsWindowVisible(hwnd):
                user32.ShowWindow(hwnd, SW_MINIMIZE)
            return True
        user32.EnumWindows(EnumWindowsProc(_cb), 0)
        time.sleep(1)
    except Exception as e:
        print(f"[Demo] minimize error: {e}")


def main():
    print("[Demo] Minimizing all windows for clean recording...")
    minimize_all_windows()
    time.sleep(2)

    print("[Demo] Starting JARVIS...")
    proc = subprocess.Popen(
        [PYTHON, "main.py"],
        stdin=subprocess.PIPE,
        cwd=JARVIS_DIR,
        text=True,
        encoding="utf-8",
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    print(f"[Demo] PID {proc.pid} — waiting {BOOT_WAIT}s for boot + greeting...")
    time.sleep(BOOT_WAIT)

    for i, (cmd, wait) in enumerate(SEQUENCE, 1):
        tag = "CMD   " if cmd.startswith("/") else "PROMPT"
        print(f"[Demo] {tag} {i:02d}/{len(SEQUENCE)}: {cmd[:65]}  (wait {wait}s)")
        proc.stdin.write(cmd + "\n")
        proc.stdin.flush()
        time.sleep(wait)

    try:
        proc.wait(timeout=10)
    except Exception:
        proc.terminate()
    print("[Demo] Done.")


if __name__ == "__main__":
    main()
