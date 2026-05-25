"""Prompt script for the cinematic recording."""

from __future__ import annotations

SEQUENCE = [
    # ── Act 1: JARVIS (English) ───────────────────────────────────── #
    ("What's the weather right now?",                             30),
    ("What time is it and what's today's date?",                  18),
    ("How's my system running - CPU and battery?",                18),
    ("Give me one interesting fact about Iron Man's arc reactor",  35),

    # ── Switch to Friday ─────────────────────────────────────────── #
    ("/friday",                                                    14),

    # ── Act 2: Friday (English) ──────────────────────────────────── #
    ("Friday, what's the stock price of NVIDIA right now?",       28),
    ("Tell me a witty one-liner in your Friday style",            30),
    ("Calculate compound interest on 50000 at 8 percent annually for 3 years", 18),
    ("Give me one sharp piece of advice for a developer",         32),

    # ── Switch Friday to Hindi ───────────────────────────────────── #
    ("/hindi",                                                     14),

    # ── Act 3: Friday (Hindi) ────────────────────────────────────── #
    ("Aaj ka mausam kaisa hai?",                                  30),
    ("Mujhe ek motivational quote do",                            32),
    ("Abhi time kya hai?",                                        16),
    ("Tony Stark ke baare mein ek interesting baat batao",        35),

    ("/quit",                                                       5),
]

BOOT_LINES = [
    "Initializing Stark Industries assistant systems.",
    "Voice interface online.",
    "Cinematic capture protocol armed.",
]

