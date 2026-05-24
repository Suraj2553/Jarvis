"""audio/filler.py — Instant filler phrases spoken while LLM thinks.

The moment the user stops talking, JARVIS speaks a filler phrase IMMEDIATELY
while the LLM generates. User never hears silence.

Usage:
    from audio.filler import get_filler, classify_intent

    intent = classify_intent(user_text)           # < 1ms, regex-based
    phrase = get_filler(intent, language="en")    # random, no repeats
"""

import random
import re
import logging

logger = logging.getLogger(__name__)

FILLERS: dict[str, list[str]] = {
    "default": [
        "On it.", "One moment.", "Give me a second.",
        "Let me check that.", "Right.", "Scanning.",
        "Working on it.", "Just a second.", "Looking into that.",
    ],
    "search": [
        "Searching now.", "Let me pull that up.",
        "Scanning sources.", "Checking the web.",
        "Finding that for you.",
    ],
    "calculation": [
        "Running the numbers.", "Calculating.",
        "Let me crunch that.", "One moment with the math.",
    ],
    "personal": [
        "Of course.", "Noted.", "On it, sir.",
        "Already on it.", "Understood.",
    ],
    "sarcastic": [
        "Oh, that. Sure.",
        "Not the strangest request today. Give me a second.",
        "Fascinating. Processing.",
        "On it. As always.",
        "Scanning. Try not to be impressed.",
    ],
    "hindi": [
        "ठीक है।", "एक पल।", "देखती हूं।", "अभी करती हूं।", "समझ गई।",
        "जी, बताइए।", "हाँ, एक सेकंड।", "ज़रूर, रुकिए।",
        "जानकारी ला रही हूं।", "बिल्कुल, अभी।", "हाँ सर।", "देख रही हूं।",
    ],
    "weather": [
        "Checking conditions now.", "Pulling up the forecast.",
        "One moment — fetching that.",
    ],
    "news": [
        "Scanning the headlines.", "Pulling the latest.",
        "Give me a second on that.",
    ],
    "timer": [
        "Setting that up.", "Got it.", "Timer coming right up.",
    ],
    "score": [
        "Checking the score.", "Pulling live data.",
    ],
}

_last_fillers: dict[str, str] = {}  # prevent repeats per category

INTENT_PATTERNS: dict[str, str] = {
    # Specific intents checked BEFORE the generic "search" catch-all
    "weather":     r"\b(weather|forecast|temperature|rain|humidity|climate|mausam)\b",
    "news":        r"\b(news|headlines|what'?s happening|khabar|samachar)\b",
    "timer":       r"\b(timer|alarm|remind me in|set a timer|countdown)\b",
    "score":       r"\b(score|cricket|ipl|match|live)\b",
    "calculation": r"\b(calculat\w*|comput\w*|how much|percentage|math|convert|equals|formula)\b",
    "personal":    r"\b(i am|i feel|my |remind me|remember|help me|i need|i want)\b",
    "search":      r"\b(search|find|look up|google|what is|who is|where is|latest|current)\b",
}

_compiled: dict[str, re.Pattern] = {
    k: re.compile(v, re.IGNORECASE) for k, v in INTENT_PATTERNS.items()
}


def classify_intent(text: str) -> str:
    """Fast regex intent detection — no LLM, < 1ms."""
    for category, pattern in _compiled.items():
        if pattern.search(text):
            return category
    return "default"


def get_filler(
    category: str = "default",
    language: str = "en",
    sarcasm_chance: float = 0.15,
) -> str:
    """Pick a filler phrase that was not used last time for this category.

    language:       "en" or "hi"
    sarcasm_chance: probability of sarcastic filler (0.0–1.0)
    """
    if language == "hi":
        pool = FILLERS["hindi"]
    elif random.random() < sarcasm_chance:
        pool = FILLERS["sarcastic"]
    else:
        pool = FILLERS.get(category, FILLERS["default"])

    last = _last_fillers.get(category, "")
    candidates = [f for f in pool if f != last]
    if not candidates:
        candidates = pool

    chosen = random.choice(candidates)
    _last_fillers[category] = chosen
    return chosen
