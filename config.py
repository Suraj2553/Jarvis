"""JARVIS configuration  v3.0 — loads/saves %APPDATA%/JARVIS/config.json.

New in v3.0
───────────
• gemini_api_key / gemini_model     — Google AI Studio (free, 2M context)
• nvidia_api_key / nvidia_model     — NVIDIA NIM free credits
• openrouter_api_key / openrouter_model — OpenRouter multi-model fallback
• llm_long_context_threshold        — chars above which Gemini is preferred
• question_wait_timeout             — seconds JARVIS waits for a reply
• proactive_min_interval            — raised to 1800 s (30 min)
"""

import json
import os
import pathlib

APPDATA_DIR = pathlib.Path(os.path.expandvars("%APPDATA%")) / "JARVIS"
CONFIG_PATH = APPDATA_DIR / "config.json"

# .env / shell env-var → config key mapping.
# load_dotenv() in main.py puts these into os.environ; we pull them here so
# every subsystem that reads config gets them without any extra plumbing.
_ENV_KEY_MAP: dict[str, str] = {
    "GROQ_API_KEY":              "groq_api_key",
    "GEMINI_API_KEY":            "gemini_api_key",
    "NVIDIA_API_KEY":            "nvidia_api_key",
    "OPENROUTER_API_KEY":        "openrouter_api_key",
    "SARVAM_API_KEY":            "sarvam_api_key",
    "ELEVENLABS_API_KEY":        "elevenlabs_api_key",
    "ELEVENLABS_HINDI_VOICE_ID": "elevenlabs_hindi_voice_id",
}

DEFAULTS: dict = {
    # ── AI Backend — primary ─────────────────────────────────────── #
    "model":               "llama-3.3-70b-versatile",
    "groq_api_key":        "",
    "groq_model":          "llama-3.3-70b-versatile",
    "groq_timeout":        4,

    # ── AI Backend — Gemini (Google AI Studio, free tier) ────────── #
    # Get free key: https://aistudio.google.com/app/apikey
    "gemini_api_key":      "",
    "gemini_model":        "gemini-2.0-flash",

    # ── AI Backend — NVIDIA NIM (free developer credits) ─────────── #
    # Get free key: https://build.nvidia.com/explore/discover
    "nvidia_api_key":      "",
    "nvidia_model":        "meta/llama-3.1-70b-instruct",

    # ── AI Backend — OpenRouter (free-tier models) ───────────────── #
    # Get free key: https://openrouter.ai/keys
    "openrouter_api_key":  "",
    "openrouter_model":    "mistralai/mistral-7b-instruct:free",

    # ── AI Backend — local fallback ──────────────────────────────── #
    "ollama_model":        "llama3.2",
    "ollama_host":         "http://localhost:11434",
    "ollama_url":          "http://localhost:11434",

    # ── Provider routing ─────────────────────────────────────────── #
    # "auto" | "groq" | "gemini" | "sarvam" | "nvidia" | "openrouter" | "ollama"
    "llm_provider":        "auto",
    "stt_provider":        "auto",
    "tts_provider":        "auto",

    # Above this many chars in conversation, prefer Gemini (long context)
    "llm_long_context_threshold": 3000,

    # ── Sarvam AI ────────────────────────────────────────────────── #
    "sarvam_api_key":      "",
    "sarvam_speaker":      "arjun",
    "sarvam_pace":         1.15,
    "sarvam_loudness":     1.3,

    # ElevenLabs TTS for English speech. API key may also come from ELEVENLABS_API_KEY.
    "elevenlabs_api_key":  "",
    "elevenlabs_voice_id": "vJmgICboVY6SshddGMAS",
    "elevenlabs_model":    "eleven_flash_v2_5",
    "elevenlabs_output_format": "pcm_24000",
    "elevenlabs_stability": 0.45,
    "elevenlabs_similarity": 0.85,
    "elevenlabs_style":    0.0,
    "elevenlabs_speaker_boost": True,

    # ElevenLabs Hindi voice — set voice_id after creating in Voice Lab.
    # Model must be eleven_multilingual_v2 for native Hindi support.
    "elevenlabs_hindi_voice_id": "",
    "elevenlabs_hindi_model":    "eleven_multilingual_v2",
    "elevenlabs_hindi_stability": 0.50,
    "elevenlabs_hindi_similarity": 0.80,

    # ── Audio ────────────────────────────────────────────────────── #
    "noise_cancellation":  True,
    "noise_strength":      0.8,
    "echo_cancellation":   True,
    "ptt_hotkey":          "ctrl+space",
    "mic_sensitivity":     3.5,

    # ── TTS ──────────────────────────────────────────────────────── #
    "tts_voice":           "",
    "tts_mode":            "standard",
    "tts_rate":            165,
    "voice_rate":          165,
    "tts_volume":          0.9,
    "voice_volume":        0.9,
    "preferred_voice":     ["Zira", "David"],

    # ── STT ──────────────────────────────────────────────────────── #
    "stt_model":           "base.en",

    # ── Wake word ────────────────────────────────────────────────── #
    "wake_word_enabled":   True,
    "wake_words":          ["hey jarvis", "jarvis", "hey travis", "ok jarvis"],

    # ── Conversation state machine ────────────────────────────────── #
    # Seconds JARVIS waits for user reply after asking a question
    "question_wait_timeout": 8.0,

    # ── Awareness ────────────────────────────────────────────────── #
    "screen_scan_interval": 60,
    "emotion_detection":   True,
    "face_detection":      True,
    "proactive_mode":      True,
    "proactive_min_interval": 1800,   # 30 min (was 25)

    # ── HUD ──────────────────────────────────────────────────────── #
    "hud_size":            380,
    "hud_corner":          "bottom-right",
    "hud_offset_x":        24,
    "hud_offset_y":        24,
    "boot_animation":      True,
    "scan_line_effect":    True,
    "war_room_auto":       False,
    "use_new_hud":         True,

    # ── Daily briefing ───────────────────────────────────────────── #
    "daily_briefing_enabled": False,
    "daily_briefing_time":    "08:00",

    # ── System ───────────────────────────────────────────────────── #
    "startup_with_windows": False,
    "tesseract_path":      r"C:/Program Files/Tesseract-OCR/tesseract.exe",

    # ── Debug ────────────────────────────────────────────────────── #
    "debug_audio_timing":  False,
    "debug_emotion":       False,
    "debug_llm_routing":   False,
}


def _apply_env_overrides(cfg: dict) -> None:
    """Overwrite API key slots from environment variables (set by load_dotenv)."""
    for env_var, cfg_key in _ENV_KEY_MAP.items():
        val = os.getenv(env_var, "").strip()
        if val:
            cfg[cfg_key] = val


def load_config() -> dict:
    APPDATA_DIR.mkdir(parents=True, exist_ok=True)
    if not CONFIG_PATH.exists():
        save_config(DEFAULTS.copy())
        d = DEFAULTS.copy()
        _apply_env_overrides(d)
        return d
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        # Merge new keys without overwriting user values
        added = False
        for k, v in DEFAULTS.items():
            if k not in data:
                data[k] = v
                added = True
        if data.get("elevenlabs_model") == "eleven_multilingual_v2":
            data["elevenlabs_model"] = DEFAULTS["elevenlabs_model"]
            added = True
        if added:
            save_config(data)   # persist new defaults so the file stays up-to-date
        _apply_env_overrides(data)
        return data
    except Exception:
        d = DEFAULTS.copy()
        _apply_env_overrides(d)
        return d


def save_config(cfg: dict) -> None:
    APPDATA_DIR.mkdir(parents=True, exist_ok=True)
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2)
