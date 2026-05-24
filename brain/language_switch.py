"""brain/language_switch.py — Runtime EN↔HI language switching.

Detects language-switch commands from STT text and coordinates switching
both TTS and STT engines atomically so every subsequent interaction is
in the chosen language.

Usage:
    from brain.language_switch import check_language_switch, handle_language_switch

    lang = check_language_switch(user_text)   # "en", "hi", or None
    if lang:
        handle_language_switch(lang, tts_engine, stt_engine)
"""

import logging
import re
from typing import Optional

logger = logging.getLogger(__name__)

# --- English trigger patterns -----------------------------------------
_EN_PATTERNS = re.compile(
    r"\b(switch(?: to)? english|english mode|speak(?: in)? english|back to english"
    r"|english please|change language to english|angrezi|in english"
    r"|can you speak in english|start speaking english)\b",
    re.IGNORECASE,
)

# --- Hindi trigger patterns -------------------------------------------
# Covers Romanized Hindi ("hindi mein bolo") and Devanagari fragments
_HI_PATTERNS = re.compile(
    r"\b(switch(?: to)? hindi|hindi mode|speak(?: in)? hindi|hindi please"
    r"|change language to hindi|hindi mein|mein hindi|bol hindi"
    r"|hindi bolo|bolo hindi|hinglish|can you speak in hindi"
    r"|please speak(?: in)? hindi|start speaking hindi|ab hindi(?: mein)?"
    r"|hindi mein bolna|hindi boliye|hindi bol)\b"
    r"|[ऀ-ॿ]{3,}",   # 3+ consecutive Devanagari chars = Hindi intent
    re.IGNORECASE,
)

# Confirmations spoken when switching
_CONFIRMATIONS = {
    "en": "JARVIS back online, sir.",
    "hi": "Friday here. हिंदी में बात करते हैं।",
}


def check_language_switch(text: str) -> Optional[str]:
    """Return "en", "hi", or None.  < 1ms, regex-only."""
    if not text:
        return None
    if _HI_PATTERNS.search(text):
        return "hi"
    if _EN_PATTERNS.search(text):
        return "en"
    return None


def handle_language_switch(
    lang: str,
    tts_engine,
    stt_engine,
    turn_detector=None,
    brain=None,
) -> None:
    """Switch TTS + STT + Brain persona to lang and speak confirmation.

    Args:
        lang:          "en" or "hi"
        tts_engine:    audio.tts_engine.TTSEngine instance
        stt_engine:    audio.stt_engine.STTEngine instance
        turn_detector: audio.turn_detector.TurnDetector (optional)
        brain:         brain.brain.Brain instance (optional) — switches persona
    """
    if lang not in ("en", "hi"):
        logger.warning("[LangSwitch] Unknown language %r — ignored", lang)
        return

    persona = "F.R.I.D.A.Y" if lang == "hi" else "J.A.R.V.I.S"
    logger.info("[LangSwitch] Switching → %s  persona=%s", lang, persona)

    tts_engine.switch_language(lang)
    stt_engine.set_language(lang)
    if brain is not None:
        brain.set_language(lang)

    confirmation = _CONFIRMATIONS[lang]
    try:
        if turn_detector is not None:
            turn_detector.set_state("SPEAKING")
        tts_engine.speak(confirmation)
    except Exception as e:
        logger.warning("[LangSwitch] Confirmation speak error: %s", e)
    finally:
        if turn_detector is not None:
            turn_detector.mark_tts_done()
