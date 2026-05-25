"""Configuration for the cinematic JARVIS recording automation."""

from __future__ import annotations

import os
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
AUTOMATION_DIR = ROOT_DIR / "automation"
LOG_DIR = AUTOMATION_DIR / "logs"
RECORDING_DIR = ROOT_DIR / "recordings"

PYTHON_EXE = ROOT_DIR / ".venv" / "Scripts" / "python.exe"
if not PYTHON_EXE.exists():
    PYTHON_EXE = Path(os.environ.get("PYTHON", "python"))

JARVIS_ENTRY = ROOT_DIR / "main.py"
START_JARVIS_BAT = ROOT_DIR / "Start JARVIS.bat"
USE_START_JARVIS_BAT = False
JARVIS_ARGS = []

# OBS
OBS_ENABLED = True
OBS_EXE_CANDIDATES = [
    Path(r"C:\Program Files\obs-studio\bin\64bit\obs64.exe"),
    Path(r"C:\Program Files (x86)\obs-studio\bin\64bit\obs64.exe"),
]
OBS_SCENE_START = "JARVIS Demo"
OBS_SCENE_PRESENTATION = "Presentation"
OBS_SCENE_END = "JARVIS Demo"
OBS_WEBSOCKET_ENABLED = True
OBS_WEBSOCKET_HOST = "localhost"
OBS_WEBSOCKET_PORT = 4455
OBS_WEBSOCKET_PASSWORD = ""
OBS_STARTUP_WAIT = 8.0

# Desktop preparation
MINIMIZE_WINDOWS = True
HIDE_TASKBAR = True
DISABLE_NOTIFICATIONS = True
SET_CONSOLE_TITLE = True

# Timing
BOOT_TIMEOUT = 95.0
BOOT_READY_IDLE = 5.0
BOOT_GREETING_IDLE_SECONDS = 7.0
BOOT_GREETING_MAX_EXTRA_WAIT = 45.0
MIN_GAP_BETWEEN_PROMPTS = 2.2
MAX_EXTRA_NATURAL_PAUSE = 1.8
AFTER_RESPONSE_BREATH = (1.4, 3.2)
PROMPT_TTS_BEFORE_SEND = True
ACTIVATE_BEFORE_EACH_PROMPT = True
ACTIVATION_TRIGGERS = ["hey_jarvis"]
CLAP_GAP_SECONDS = 1.0
ACTIVATION_TO_PROMPT_PAUSE = (3.0, 4.8)
SEND_STDIN_BACKUP_AFTER_TTS = True
STDIN_BACKUP_DELAY = 2.2

# Monitoring
OUTPUT_IDLE_SECONDS = 3.2
SPEAKING_IDLE_SECONDS = 2.2
MAX_WAIT_EXTENSION = 75.0
STATUS_POLL_SECONDS = 0.25

# Idle recovery
IDLE_RECOVERY_ENABLED = True
IDLE_RECOVERY_AFTER = 24.0
IDLE_RECOVERY_COOLDOWN = 45.0
IDLE_RECOVERY_TRIGGERS = ["Hey Jarvis", "clap"]

# Voice prompts. "auto" tries edge-tts, then pyttsx3. Use "pyttsx3" if edge audio
# is not audible on your Windows build.
TTS_ENABLED = True
TTS_PROVIDER = "pyttsx3"
PYTTSX3_RATE = 138
PYTTSX3_VOLUME = 1.0
EDGE_TTS_VOICE_EN = "en-US-GuyNeural"
EDGE_TTS_VOICE_HI = "hi-IN-MadhurNeural"
TTS_RATE = "-4%"
TTS_VOLUME = "+0%"

# Presentation mode
PRESENTATION_TRIGGER = "present jarvis test"
PRESENTATION_FOLLOWUP_STOP = "stop"
PRESENTATION_MIN_WAIT = 12.0
PRESENTATION_MAX_WAIT = 90.0

# Optional extras
GENERATE_SUBTITLES = True
TERMINAL_FULLSCREEN_HOTKEY = False
