# J.A.R.V.I.S — Personal AI for Windows

> *Just A Rather Very Intelligent System* — A fully voice-activated personal AI assistant that lives on your desktop. Groq-powered with local Ollama fallback, cinematic Iron Man HUD, emotion awareness, autonomous agents, and more.

![Python](https://img.shields.io/badge/Python-3.11%2B-blue?logo=python)
![PyQt6](https://img.shields.io/badge/UI-PyQt6-41CD52?logo=qt)
![Groq](https://img.shields.io/badge/LLM-Groq%20%7C%20Ollama-orange)
![Platform](https://img.shields.io/badge/Platform-Windows%2011-0078D4?logo=windows)
![License](https://img.shields.io/badge/License-MIT-green)

---

## What is this?

JARVIS is a **local-first** Windows AI assistant modelled after the one from the Iron Man films. It listens for your voice, understands context, controls your PC, and talks back — all from a glowing HUD in the corner of your screen.

- **Primary brain**: Groq API (llama-3.3-70b-versatile) — fast cloud inference
- **Fallback brain**: Ollama (llama3.2) — fully offline, auto-switches silently
- **Voice in / Voice out**: faster-whisper STT + Coqui TTS (VCTK voice cloning)
- **Always aware**: screen context every 60s, emotion detection, proactive observations
- **Autonomous agents**: multi-step research, web browsing via Playwright, task chains

---

## Architecture

```
jarvis/
│
├── main.py                  # Entry point — wires all subsystems
├── config.py                # Loads / saves %APPDATA%/JARVIS/config.json
├── monitor.py               # System stats (CPU, RAM, battery, network)
├── stt.py                   # faster-whisper speech-to-text
├── tts.py                   # pyttsx3 SAPI5 TTS (fallback)
├── sounds.py                # Pure-tone audio feedback (no audio files needed)
├── tray.py                  # pystray system tray icon
├── wake_word.py             # Always-on wake word detection ("Hey JARVIS")
│
├── brain/                   # LLM core
│   ├── llm_router.py        # Groq primary → Ollama fallback, streaming, retry
│   └── brain.py             # Tool calling, ReAct loop, response filtering
│
├── audio/                   # Audio pipeline
│   ├── listener.py          # Double-clap detection (biometric signature + PTT)
│   ├── noise_pipeline.py    # rnnoise → WebRTC VAD → STT gate
│   └── voice_engine.py      # Coqui TTS with sentence-streaming + voice cloning
│
├── memory/                  # Persistent state
│   ├── memory_engine.py     # Cross-session facts, relationship level (0-5)
│   └── conversation_engine.py  # Session log, unresolved threads, yesterday context
│
├── personality/             # Character
│   ├── conversation_engine.py  # Forbidden phrases, small talk, dry observations
│   └── initiator.py         # Proactive speech (battery, morning, contextual)
│
├── awareness/               # Situational intelligence
│   ├── context_engine.py    # Screen OCR every 60s → LLM activity summary
│   └── emotion_engine.py    # Voice pitch + face FER → tone adaptation
│
├── agent/                   # Autonomous capabilities
│   ├── autonomous_agent.py  # Multi-step research, up to 8 actions
│   ├── task_chain.py        # Morning briefing, work mode, end-of-day chains
│   └── predictor.py         # SQLite workflow predictor (learns your habits)
│
├── meeting/                 # Meeting intelligence
│   └── meeting_assistant.py # Detects Teams/Zoom, transcribes, action items
│
├── tools/                   # Tool implementations called by the LLM
│   ├── system.py            # App control, keyboard, volume, screenshot
│   ├── files.py             # File CRUD (delete → Recycle Bin)
│   ├── web.py               # DuckDuckGo search, weather, news, live scores
│   ├── pa.py                # Timers, notes, reminders, media control
│   ├── utils.py             # Calculator, datetime, clipboard
│   └── browser_control.py  # Playwright voice-driven browser
│
└── ui/                      # Visual layer
    ├── hud.py               # 380×380 five-layer animated HUD (60 fps QPainter)
    ├── conversation_panel.py # Typewriter conversation panel below HUD
    ├── boot_sequence.py     # Cinematic 7-second boot (arc reactor + typewriter)
    ├── war_room.py          # Full-screen second-monitor dashboard
    └── theme.py             # All colours, fonts, layout constants
```

---

## Features

### Voice Activation
| Method | How |
|--------|-----|
| Wake word | Say **"Hey JARVIS"** or **"JARVIS"** |
| Double clap | Two claps — biometric signature validated |
| Push to talk | Hold **Ctrl+Space** |

### Voice Commands (examples)
| Category | Examples |
|----------|---------|
| Apps | "Open Notepad", "Close Spotify", "What's running?" |
| Files | "Find report.pdf", "Read todo.txt", "Create notes.txt" |
| Web | "Search for quantum computing", "Weather in Tokyo", "Tech news" |
| System | "Set volume to 40", "Take a screenshot", "Lock the screen" |
| Media | "Play/pause", "Next track", "Volume up" |
| Reminders | "Remind me to call John in 20 minutes" |
| Research | "Research the best Python async frameworks and compare them" |
| Browser | "Open YouTube and search for lo-fi beats" |
| HUD | "Show war room", "Hide war room", "Move to second screen" |
| Settings | "Open settings", "Sleep", "Stand by" |

### AI Pipeline
```
Voice → faster-whisper (local STT)
      → Groq llama-3.3-70b (primary, 4s timeout)
        ↳ Ollama llama3.2 (silent fallback if Groq unreachable)
      → Tool calling (40+ tools, native Groq format)
      → Sentence-streaming TTS (speaks first sentence while generating rest)
      → Coqui VCTK voice (falls back to pyttsx3 SAPI5)
```

---

## Prerequisites

### Required

**1. Python 3.11+**
```
https://python.org — check "Add Python to PATH" during install
```

**2. Ollama** (local LLM fallback)
```
https://ollama.com/download/windows
ollama pull llama3.2
```

**3. Groq API key** *(free, optional but strongly recommended)*
```
https://console.groq.com — create a free account, copy your key
```

### Optional

**Tesseract OCR** — for screen reading / context awareness
```
https://github.com/UB-Mannheim/tesseract/wiki
Install to: C:\Program Files\Tesseract-OCR\
```

**Playwright** — for voice browser control
```
python -m playwright install chromium
```

---

## Installation

```bat
git clone https://github.com/your-username/jarvis.git
cd jarvis
pip install -r requirements.txt
```

Double-click **`Start JARVIS.bat`** — or run:
```bat
python main.py
```

On first run, JARVIS will ask for your name and Groq API key via a guided voice wizard.

---

## Configuration

Settings live in `%APPDATA%\JARVIS\config.json` and are also editable via the Settings dialog (say *"open settings"* or right-click the tray icon).

Key settings:
```json
{
  "groq_api_key":        "gsk_...",
  "groq_model":          "llama-3.3-70b-versatile",
  "ollama_model":        "llama3.2",
  "wake_word_enabled":   true,
  "noise_cancellation":  true,
  "emotion_detection":   true,
  "proactive_mode":      true,
  "boot_animation":      true,
  "war_room_auto":       false,
  "tts_mode":            "standard",
  "hud_corner":          "bottom-right"
}
```

---

## HUD States

The 380×380 HUD renders at 60 fps with five composited layers:

| State | Visual |
|-------|--------|
| **Idle** | Slow breathing bloom, rotating orbit ring, concentric arcs |
| **Listening** | 24 radial bars animate with mic RMS, radar sweep |
| **Thinking** | 4-segment spinning arcs (purple), orbit ring pulses |
| **Speaking** | Expanding rings + oscillating waveform (green glow) |

---

## Privacy

| Component | Where it runs |
|-----------|--------------|
| Speech recognition | Local CPU (faster-whisper) |
| LLM inference | Groq cloud **or** local Ollama — your choice |
| TTS | Local (Coqui / pyttsx3) |
| Screen context | Local OCR (Tesseract) |
| Face detection | Local webcam (OpenCV + FER) |
| Weather / News | Open-Meteo, Google News RSS — no account needed |

No conversation data is stored remotely. Groq processes only the text of your query — no audio is ever sent anywhere.

---

## Troubleshooting

| Problem | Fix |
|---------|-----|
| `"Ollama not found"` | Install Ollama, ensure `ollama` is in PATH |
| Mic not detected | Windows Settings → Privacy → Microphone → allow Desktop apps |
| No Groq response | Check your API key in Settings; JARVIS auto-falls back to Ollama |
| `pytesseract` error | Set `tesseract_path` in config or install Tesseract to default path |
| `pywin32` DLL error | Run: `python Scripts\pywin32_postinstall.py -install` |
| PyQt6 display glitches | Update GPU drivers |
| `rnnoise-wrapper` fails to install | Install [Microsoft C++ Build Tools](https://visualstudio.microsoft.com/visual-cpp-build-tools/) first |
| Coqui TTS slow first run | Normal — downloading VCTK model (~1 GB) once |

---

## License

MIT — do whatever you want, just don't make it evil.
