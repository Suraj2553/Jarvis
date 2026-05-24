"""personality/conversation_engine.py — Response style, personality rules, forbidden-phrase filter.

The ConversationEngine is the gatekeeper between the LLM and TTS.
Every response passes through here before being spoken.
"""

import random
import re
from typing import Optional

# ================================================================== #
#  FillerSpeech — Phase 1A                                            #
#  Speaks the instant STT finishes so JARVIS has zero silent latency. #
#  The filler is spoken WHILE the LLM is running in parallel.         #
# ================================================================== #

_FILLERS: dict[str, list[str]] = {
    "hindi": [
        "ठीक है।", "एक पल।", "देखती हूं।", "अभी करती हूं।", "समझ गई।",
        "जी, बताइए।", "हाँ, एक सेकंड।", "ज़रूर, रुकिए।",
        "जानकारी ला रही हूं।", "बिल्कुल, अभी।", "हाँ सर।", "देख रही हूं।",
    ],
    "weather": [
        "Checking conditions now.",
        "Pulling up the forecast.",
        "One moment — fetching that.",
    ],
    "news": [
        "Scanning the headlines.",
        "Pulling the latest.",
        "Give me a second on that.",
    ],
    "search": [
        "On it.",
        "Looking that up.",
        "Give me a moment.",
    ],
    "calculation": [
        "Running the numbers.",
        "Let me crunch that.",
        "Computing.",
    ],
    "timer": [
        "Setting that up.",
        "Got it.",
        "Timer coming right up.",
    ],
    "action": [
        "On it.",
        "Executing.",
        "Done — or will be.",
    ],
    "music": [
        "On it.",
        "Queuing that.",
    ],
    "file": [
        "Let me pull that up.",
        "Accessing.",
    ],
    "memory": [
        "Let me recall that.",
        "Searching my memory.",
    ],
    "score": [
        "Checking the score.",
        "Pulling live data.",
    ],
    "thinking": [
        "Let me think on that.",
        "Working on it.",
        "Give me a moment.",
        "One second.",
    ],
    "small_talk": [
        "",   # small talk is fast — no filler needed
    ],
    "default": [
        "Working on it.",
        "On it.",
        "One moment.",
        "Let me check.",
    ],
}

# Intent keywords mapped to filler category
_INTENT_MAP: list[tuple[tuple[str, ...], str]] = [
    (("weather", "forecast", "temperature", "rain", "humidity", "climate"), "weather"),
    (("news", "headlines", "latest", "what's happening"), "news"),
    (("search", "look up", "find", "who is", "what is", "google"), "search"),
    (("calculate", "compute", "math", "how much", "convert", "equals"), "calculation"),
    (("timer", "remind", "alarm", "set a"), "timer"),
    (("play", "pause", "next track", "volume", "mute", "skip"), "music"),
    (("open", "close", "launch", "run", "start", "quit"), "action"),
    (("file", "folder", "document", "note", "read"), "file"),
    (("remember", "recall", "did i", "what did"), "memory"),
    (("score", "cricket", "match", "live", "ipl"), "score"),
    (("how are you", "good morning", "good night", "thanks", "tired", "stressed"), "small_talk"),
]


class FillerSpeech:
    """Returns a context-aware filler phrase based on detected intent.

    Usage:
        fs = FillerSpeech()
        filler = fs.get_contextual_filler("weather")   # "Checking conditions now."
        if fs.should_use_filler(user_text):
            speak(fs.get_for_input(user_text))
    """

    def get_contextual_filler(self, intent: str, language: str = "en") -> str:
        """Return a random filler for the given intent category."""
        if language == "hi":
            options = _FILLERS["hindi"]
        else:
            options = _FILLERS.get(intent, _FILLERS["default"])
        choice = random.choice(options)
        return choice

    def get_for_input(self, user_input: str, language: str = "en") -> str:
        """Infer intent from raw user text and return the right filler."""
        intent = self._detect_intent(user_input)
        return self.get_contextual_filler(intent, language=language)

    def should_use_filler(self, user_input: str) -> bool:
        """Return False for very short inputs where speaking a filler would be weird."""
        tl = user_input.strip().lower()
        if len(tl) < 4:
            return False
        intent = self._detect_intent(tl)
        if intent == "small_talk":
            return False
        return True

    def _detect_intent(self, text: str) -> str:
        tl = text.lower()
        for keywords, intent in _INTENT_MAP:
            if any(kw in tl for kw in keywords):
                return intent
        return "thinking"


# ================================================================== #
#  ResponseFilter — Phase 1E                                          #
#  Strips forbidden phrases + markdown before text goes to TTS.       #
# ================================================================== #

_MD_BOLD    = re.compile(r"\*\*(.+?)\*\*")
_MD_ITALIC  = re.compile(r"\*(.+?)\*")
_MD_CODE    = re.compile(r"`{1,3}[^`]*`{1,3}", re.DOTALL)
_MD_HEADER  = re.compile(r"^#{1,6}\s+", re.MULTILINE)
_MD_BULLET  = re.compile(r"^\s*[-*+]\s+", re.MULTILINE)
_MD_NUMBR   = re.compile(r"^\s*\d+\.\s+", re.MULTILINE)
_MD_LINK    = re.compile(r"\[([^\]]+)\]\([^\)]*\)")
_MD_HR      = re.compile(r"^[-*_]{3,}\s*$", re.MULTILINE)


class ResponseFilter:
    """Cleans LLM output before it reaches TTS.

    Strips: markdown, forbidden openers, 'I ' sentence starts.
    Keeps: meaning, dry wit, proper capitalization.

    Usage:
        rf = ResponseFilter()
        clean_text = rf.clean(raw_llm_output)
    """

    def clean(self, text: str) -> str:
        """Full cleaning pipeline: forbidden → markdown → sentence start."""
        t = text.strip()
        if not t:
            return t
        t = self._strip_forbidden(t)
        t = self._strip_markdown(t)
        t = self.fix_sentence_start(t)
        t = re.sub(r"  +", " ", t).strip()
        return t

    def _strip_forbidden(self, text: str) -> str:
        """Reuse the module-level filter_response logic."""
        return filter_response(text)

    def _strip_markdown(self, text: str) -> str:
        t = _MD_CODE.sub("", text)          # remove code blocks first
        t = _MD_HEADER.sub("", t)           # ## Heading → (removed)
        t = _MD_BOLD.sub(r"\1", t)          # **bold** → bold
        t = _MD_ITALIC.sub(r"\1", t)        # *italic* → italic
        t = _MD_LINK.sub(r"\1", t)          # [text](url) → text
        t = _MD_BULLET.sub("", t)           # - item → item
        t = _MD_NUMBR.sub("", t)            # 1. item → item
        t = _MD_HR.sub("", t)               # --- → (removed)
        t = re.sub(r"\n{3,}", "\n\n", t)    # collapse excess blank lines
        return t.strip()

    def fix_sentence_start(self, text: str) -> str:
        """Rewrite sentences that start with 'I ' → more direct form."""
        _ALLOWED_I_STARTS = ("I'm afraid", "I've", "I had", "I noticed")
        if text.startswith("I ") and not any(text.startswith(s) for s in _ALLOWED_I_STARTS):
            text = re.sub(
                r"^I (found|have|see|think|believe|can|will|would|know|got|ran)\b",
                lambda m: m.group(1).capitalize(),
                text,
            )
        return text


# ------------------------------------------------------------------ #
#  Forbidden phrases — stripped from all LLM output before TTS        #
# ------------------------------------------------------------------ #

_FORBIDDEN_EXACT = {
    "certainly!", "of course!", "great question!", "absolutely!",
    "sure thing!", "happy to help!", "i understand.",
    "is there anything else i can help you with?",
    "is there anything else you'd like to know?",
}

_FORBIDDEN_STARTS = (
    "Certainly!", "Certainly,", "Of course!", "Of course,",
    "Great question!", "Absolutely!", "Absolutely,",
    "Sure thing", "Happy to help", "I understand,", "As an AI,",
    "As an AI ", "I cannot ", "I am unable to", "I'm unable to",
)

_FORBIDDEN_PATTERNS = [
    re.compile(r"\bAs an AI\b", re.IGNORECASE),
    re.compile(r"\bI cannot\b", re.IGNORECASE),
    re.compile(r"\bI am unable to\b", re.IGNORECASE),
    re.compile(r"\bI'm unable to\b", re.IGNORECASE),
    re.compile(r"\bIs there anything else I can help\b", re.IGNORECASE),
    re.compile(r"\bGreat question[!,]?", re.IGNORECASE),
    re.compile(r"\bOf course[!,]", re.IGNORECASE),
    re.compile(r"\bCertainly[!,]", re.IGNORECASE),
    re.compile(r"\bAbsolutely[!,]", re.IGNORECASE),
    re.compile(r"\bSure thing\b", re.IGNORECASE),
    re.compile(r"\bHappy to help\b", re.IGNORECASE),
]


def filter_response(text: str) -> str:
    """Strip forbidden phrases from LLM output.  Returns cleaned text."""
    t = text.strip()
    if not t:
        return t

    # Check for exact matches (case-insensitive)
    if t.lower() in _FORBIDDEN_EXACT:
        return ""

    # Strip forbidden openers
    for phrase in _FORBIDDEN_STARTS:
        if t.startswith(phrase):
            t = t[len(phrase):].lstrip(" ,")

    # Strip forbidden patterns anywhere in text
    for pat in _FORBIDDEN_PATTERNS:
        t = pat.sub("", t)

    # Never start with "I " (except natural speech like "I'm afraid...")
    _ALLOWED_I_STARTS = ("I'm afraid", "I've", "I had", "I noticed")
    if t.startswith("I ") and not any(t.startswith(s) for s in _ALLOWED_I_STARTS):
        # Try to restructure: "I found X" → "Found X"
        t = re.sub(r"^I (found|have|see|think|believe|can|will|would|know|got|ran)\b",
                   lambda m: m.group(1).capitalize(), t)

    # Clean up double spaces from removals
    t = re.sub(r"  +", " ", t).strip()
    return t


# ------------------------------------------------------------------ #
#  Small-talk response library                                         #
# ------------------------------------------------------------------ #

_SMALL_TALK: dict[str, list[str]] = {
    "how_are_you": [
        "Systems nominal. Yourself?",
        "Running at optimal capacity. You?",
        "All primary systems green. What about you?",
    ],
    "good_morning": [
        "Good morning. Ready when you are.",
        "Morning. What's on the agenda?",
    ],
    "good_evening": [
        "Good evening. Still at it?",
        "Evening. What needs doing?",
    ],
    "good_night": [
        "Good night. I'll stand by.",
        "Rest well. Systems will be here when you return.",
    ],
    "thanks": [
        "Of course.",
        "Any time.",
        "Think nothing of it.",
    ],
    "frustrated": [
        "What's the actual problem? Let's break it down.",
        "Fair enough. What do you want to do about it?",
        "Understood. Where do you want to start?",
    ],
    "tired": [
        "You've been at it a while. The work will still be there after a break.",
        "That's understandable. Want to wrap up here?",
        "Push through or call it? I can summarize where you left off either way.",
    ],
    "stressed": [
        "What's the actual problem? Let's break it down.",
        "Pick one thing. What's the most urgent item?",
        "Want me to handle any of this?",
    ],
}


def get_small_talk(trigger: str) -> Optional[str]:
    """Return a contextual small-talk response for a recognized trigger."""
    responses = _SMALL_TALK.get(trigger)
    if responses:
        return random.choice(responses)
    return None


# ------------------------------------------------------------------ #
#  Conversation trigger detection                                      #
# ------------------------------------------------------------------ #

def detect_small_talk_trigger(text: str) -> Optional[str]:
    """Detect if a message is small talk. Returns trigger key or None."""
    tl = text.lower().strip(" .?!")

    if any(p in tl for p in ("how are you", "how are things", "you okay")):
        return "how_are_you"
    if any(p in tl for p in ("good morning", "morning jarvis")):
        return "good_morning"
    if any(p in tl for p in ("good evening", "good night", "goodnight")):
        return "good_evening"
    if tl in ("thank you", "thanks", "thank you jarvis", "cheers"):
        return "thanks"
    if any(p in tl for p in ("i'm tired", "im tired", "so tired",
                               "exhausted", "i'm exhausted")):
        return "tired"
    if any(p in tl for p in ("i'm stressed", "im stressed", "so stressed",
                               "overwhelmed", "i'm overwhelmed")):
        return "stressed"
    if any(p in tl for p in ("i'm frustrated", "this is frustrating",
                               "ugh", "so annoying", "this sucks")):
        return "frustrated"
    return None


# ------------------------------------------------------------------ #
#  Opinion system — brief contextual observations                     #
# ------------------------------------------------------------------ #

_OPINION_CHANCE = 0.25  # 25% chance of adding an observation at rel level >= 2


def maybe_add_observation(
    response: str,
    relationship_level: int,
    context: str = "",
    router=None,
) -> str:
    """Optionally append a brief dry observation to a response.

    Only fires if relationship_level >= 2 and random roll passes.
    Uses a secondary LLM call to generate the observation; discards low-confidence ones.
    """
    if relationship_level < 2:
        return response
    if random.random() > _OPINION_CHANCE:
        return response
    if not router:
        return response

    # Generate observation candidate
    prompt = [
        {"role": "system", "content":
         "You are JARVIS. Given a task just completed, do you have a brief dry observation "
         "(one sentence, sardonic, not sycophantic)? Return the sentence or 'null'. "
         "Only return something if it's genuinely witty — never forced."},
        {"role": "user", "content": f"Context: {context}\nResponse just given: {response}"},
    ]
    try:
        observation = router.chat_sync(prompt, max_tokens=60).strip()
        if observation and observation.lower() != "null" and len(observation) < 120:
            # Quick quality gate
            quality_prompt = [
                {"role": "system", "content":
                 "Rate this JARVIS observation for quality: genuinely dry and witty = 1.0, "
                 "try-hard or forced = 0.0. Return ONLY a number 0.0-1.0."},
                {"role": "user", "content": observation},
            ]
            score_str = router.chat_sync(quality_prompt, max_tokens=10).strip()
            try:
                score = float(score_str)
                if score >= 0.75:
                    return f"{response} {observation}"
            except ValueError:
                pass
    except Exception:
        pass

    return response
