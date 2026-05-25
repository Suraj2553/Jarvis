"""JARVIS — Personal AI for Windows.  v2.0 — Stark Industries upgrade.

Activation
----------
- Say "Hey JARVIS" (or just "JARVIS") — always listening
- Double-clap — for hands-free activation
- Ctrl+Space (PTT) — push to talk in noisy environments

Startup sequence
----------------
1. Load / create config in %APPDATA%/JARVIS/config.json
2. First-run wizard if no config (Groq key, name, test wake word)
3. Verify Ollama is running for fallback; auto-start if not
4. Calibrate microphone
5. Launch PyQt6 HUD + system tray
6. Start all subsystems (memory, brain, audio pipeline, emotion, context)
7. Time-of-day greeting with cross-session context
8. Auto-activate once after greeting
"""

import faulthandler
import os
import warnings
warnings.filterwarnings("ignore", message="data discontinuity in recording")
import random
import subprocess
import sys
from typing import Optional

# Load .env before anything else so os.getenv() picks up API keys
try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))
except ImportError:
    pass  # python-dotenv not installed — keys must come from config.json

_TRACE_FILE = open(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "crash_trace.txt"),
    "w", buffering=1, encoding="utf-8",
)
faulthandler.enable(file=_TRACE_FILE, all_threads=True)

import threading

# ── Thread-aware stdout router ────────────────────────────────────── #
# When terminal input is active, background thread logs are redirected
# to jarvis_run.log so the console stays clean for typing.

_real_stdout = sys.stdout
_log_fh      = open(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "jarvis_run.log"),
    "a", encoding="utf-8", buffering=1,
)

class _SmartOut:
    """Route stdout: key threads → console; background threads → log file."""
    _CONSOLE_THREADS = {
        "MainThread", "TerminalInput",
        "Pipeline",    # JARVIS heard / activated messages
        "SpeechEnd",   # VAD on_speech_end (what mic captured)
        "TTS-Worker",  # what JARVIS is speaking
        "PresentAuto", # presentation auto-advance narration
    }

    def write(self, text: str) -> None:
        tname = threading.current_thread().name
        if tname in self._CONSOLE_THREADS:
            try:
                _real_stdout.write(text)
            except UnicodeEncodeError:
                enc = getattr(_real_stdout, "encoding", "utf-8") or "utf-8"
                _real_stdout.write(text.encode(enc, errors="replace").decode(enc))
        else:
            _log_fh.write(text)

    def flush(self) -> None:
        _real_stdout.flush()
        _log_fh.flush()

    def fileno(self):
        return _real_stdout.fileno()

sys.stdout = _SmartOut()
import time

import requests
from PyQt6.QtCore import QObject, Qt, QTimer, pyqtSignal
from PyQt6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QDialog,
    QHBoxLayout, QLabel, QLineEdit, QPushButton, QSlider, QVBoxLayout,
)

import config as cfg_mod

from brain import Brain
from audio.listener import ClapListener
from audio.conversation_state import ConversationState, reply_ends_with_question
from monitor import SystemMonitor
from sounds import play_activation, play_listening_start, play_response_start, play_error
from tray import SystemTray
from wake_word import WakeWordListener
import tools.files      as file_tools
import tools.pa         as pa_tools
import tools.system     as sys_tools
import tools.utils      as util_tools
import tools.web        as web_tools
import tools.presenter  as presenter_tools
import tools.email_tool as email_tools
import tools.meeting    as meeting_tools

# ── New subsystems (graceful fallback if not installed) ──────────── #
try:
    from memory.memory_engine import MemoryEngine
    _HAS_MEMORY = True
except Exception as e:
    print(f"[Main] MemoryEngine unavailable: {e}")
    _HAS_MEMORY = False
    MemoryEngine = None

try:
    from memory.conversation_engine import ConversationEngine
    _HAS_CONV = True
except Exception as e:
    print(f"[Main] ConversationEngine unavailable: {e}")
    _HAS_CONV = False
    ConversationEngine = None

try:
    from awareness.context_engine import ContextEngine
    _HAS_CONTEXT = True
except Exception as e:
    print(f"[Main] ContextEngine unavailable: {e}")
    _HAS_CONTEXT = False
    ContextEngine = None

try:
    from awareness.emotion_engine import EmotionEngine
    _HAS_EMOTION = True
except Exception as e:
    print(f"[Main] EmotionEngine unavailable: {e}")
    _HAS_EMOTION = False
    EmotionEngine = None

try:
    from personality.initiator import ProactiveInitiator
    _HAS_INITIATOR = True
except Exception as e:
    print(f"[Main] Initiator unavailable: {e}")
    _HAS_INITIATOR = False
    ProactiveInitiator = None

try:
    from agent.task_chain import TaskChain
    _HAS_TASKCHAIN = True
except Exception as e:
    print(f"[Main] TaskChain unavailable: {e}")
    _HAS_TASKCHAIN = False
    TaskChain = None

try:
    from agent.autonomous_agent import AutonomousAgent, is_agentic_request
    _HAS_AGENT = True
except Exception as e:
    print(f"[Main] AutonomousAgent unavailable: {e}")
    _HAS_AGENT = False
    AutonomousAgent = None
    def is_agentic_request(t): return False  # noqa: E731

try:
    from meeting.meeting_assistant import MeetingAssistant
    _HAS_MEETING = True
except Exception as e:
    print(f"[Main] MeetingAssistant unavailable: {e}")
    _HAS_MEETING = False
    MeetingAssistant = None

try:
    from ui.war_room import WarRoom
    _HAS_WAR_ROOM = True
except Exception as e:
    print(f"[Main] WarRoom unavailable: {e}")
    _HAS_WAR_ROOM = False
    WarRoom = None

try:
    from tools.browser_control import BrowserController, is_browser_command
    _HAS_BROWSER = True
except Exception as e:
    print(f"[Main] BrowserController unavailable: {e}")
    _HAS_BROWSER = False
    BrowserController = None
    def is_browser_command(t): return False  # noqa: E731

from ui.hud import JARVISHud
from ui.boot_sequence import BootSequence
from personality.conversation_engine import FillerSpeech

# ── New voice-engine subsystems (graceful fallback if unavailable) ── #
try:
    from audio.tts_engine import TTSEngine
    _HAS_TTS_ENGINE = True
except Exception as _e:
    print(f"[Main] TTSEngine unavailable: {_e}")
    _HAS_TTS_ENGINE = False
    TTSEngine = None

try:
    from audio.stt_engine import STTEngine
    _HAS_STT_ENGINE = True
except Exception as _e:
    print(f"[Main] STTEngine unavailable: {_e}")
    _HAS_STT_ENGINE = False
    STTEngine = None

try:
    from audio.turn_detector import TurnDetector
    from audio.pipeline import process_mic_chunk
    _HAS_TURN_DETECTOR = True
except Exception as _e:
    print(f"[Main] TurnDetector unavailable: {_e}")
    _HAS_TURN_DETECTOR = False
    TurnDetector = None
    def process_mic_chunk(a, **kw): return a, False, 0.0  # noqa: E731

try:
    from audio.filler import get_filler, classify_intent
    _HAS_FILLER = True
except Exception as _e:
    _HAS_FILLER = False
    def get_filler(*a, **kw): return "One moment."  # noqa: E731
    def classify_intent(t): return "default"  # noqa: E731

try:
    from brain.language_switch import check_language_switch, handle_language_switch
    _HAS_LANG_SWITCH = True
except Exception as _e:
    _HAS_LANG_SWITCH = False
    def check_language_switch(t): return None  # noqa: E731
    def handle_language_switch(*a, **kw): pass  # noqa: E731

try:
    from terminal_input import TerminalInputThread
    _HAS_TERMINAL_THREAD = True
except Exception as _e:
    _HAS_TERMINAL_THREAD = False
    TerminalInputThread = None

# Force GC on main thread now — flushes temporary COM objects created during
# module imports (av/PyAV, comtypes, soundcard) before any background threads
# start.  Without this, those objects can be GC'd from worker threads, causing
# a COM apartment violation (STATUS_ACCESS_VIOLATION, exit 0xC0000005).
import gc as _gc
_gc.collect()

# ── Settings signal ───────────────────────────────────────────────── #
class _SettingsSignaler(QObject):
    ready = pyqtSignal(list, list)


# ──────────────────────────────────────────────────────────────────── #
#  First-run setup wizard — GUI dialog                                 #
# ──────────────────────────────────────────────────────────────────── #

class _FirstRunDialog(QDialog):
    """Cinematic first-run setup dialog."""

    _STYLE = """
        QDialog {
            background: #02020A;
            border: 1px solid #00D4FF;
        }
        QLabel {
            color: #E8F4FD;
        }
        QLabel#title {
            color: #00D4FF;
            font-size: 22px;
            font-weight: bold;
            letter-spacing: 4px;
        }
        QLabel#subtitle {
            color: #4A6080;
            font-size: 10px;
            letter-spacing: 2px;
        }
        QLabel#section {
            color: #00D4FF;
            font-size: 10px;
            letter-spacing: 1px;
        }
        QLabel#status {
            color: #10B981;
            font-size: 10px;
        }
        QLineEdit {
            background: #05050F;
            color: #E8F4FD;
            border: 1px solid #00D4FF;
            border-radius: 4px;
            padding: 8px 12px;
            font-size: 12px;
            selection-background-color: #003D4F;
        }
        QLineEdit:focus {
            border: 1px solid #00D4FF;
            background: #07071A;
        }
        QPushButton#primary {
            background: #00D4FF;
            color: #000;
            border: none;
            border-radius: 4px;
            padding: 10px 28px;
            font-size: 12px;
            font-weight: bold;
            letter-spacing: 1px;
        }
        QPushButton#primary:hover { background: #00B8E0; }
        QPushButton#primary:pressed { background: #0090B0; }
        QPushButton#skip {
            background: transparent;
            color: #4A6080;
            border: 1px solid #1A2040;
            border-radius: 4px;
            padding: 10px 20px;
            font-size: 11px;
        }
        QPushButton#skip:hover { color: #E8F4FD; border-color: #4A6080; }
    """

    def __init__(self, tts, cfg: dict | None = None):
        super().__init__()
        self._tts = tts
        self._pre_cfg = cfg or {}
        self._cfg_result: dict = {}

        self.setWindowTitle("J.A.R.V.I.S — First Run Setup")
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
        )
        self.setFixedSize(480, 400)
        self.setStyleSheet(self._STYLE)
        self._build_ui()
        self._center_on_screen()

        # Speak welcome after dialog appears
        if self._tts:
            QTimer.singleShot(400, lambda: threading.Thread(
                target=lambda: self._tts.speak("Welcome. I am JARVIS. Let us get you configured."),
                daemon=True,
            ).start())

    def _center_on_screen(self) -> None:
        screen = QApplication.primaryScreen().geometry()
        self.move(
            (screen.width() - self.width()) // 2,
            (screen.height() - self.height()) // 2,
        )

    def _build_ui(self) -> None:
        from PyQt6.QtGui import QFont
        layout = QVBoxLayout(self)
        layout.setContentsMargins(40, 36, 40, 32)
        layout.setSpacing(0)

        # ── Header ───────────────────────────────────────────────── #
        title = QLabel("J.A.R.V.I.S")
        title.setObjectName("title")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(title)

        subtitle = QLabel("JUST A RATHER VERY INTELLIGENT SYSTEM  ·  v2.0")
        subtitle.setObjectName("subtitle")
        subtitle.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(subtitle)

        layout.addSpacing(6)

        # Separator
        sep = QLabel()
        sep.setFixedHeight(1)
        sep.setStyleSheet("background: #00D4FF; opacity: 0.3;")
        layout.addWidget(sep)

        layout.addSpacing(24)

        # ── Name field ────────────────────────────────────────────── #
        name_lbl = QLabel("HOW SHOULD I ADDRESS YOU?")
        name_lbl.setObjectName("section")
        layout.addWidget(name_lbl)

        layout.addSpacing(8)

        self._name_edit = QLineEdit()
        self._name_edit.setPlaceholderText("Your name  (e.g. Tony)")
        self._name_edit.setFixedHeight(40)
        layout.addWidget(self._name_edit)

        layout.addSpacing(22)

        # ── Groq key field ────────────────────────────────────────── #
        groq_lbl = QLabel("GROQ API KEY  —  FREE AT  console.groq.com")
        groq_lbl.setObjectName("section")
        layout.addWidget(groq_lbl)

        layout.addSpacing(8)

        self._key_edit = QLineEdit()
        self._key_edit.setPlaceholderText("gsk_…   (leave blank to use local Ollama only)")
        self._key_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self._key_edit.setFixedHeight(40)
        # Pre-populate from env/config if already available
        pre_groq = self._pre_cfg.get("groq_api_key", "")
        if pre_groq:
            self._key_edit.setText(pre_groq)
        layout.addWidget(self._key_edit)

        layout.addSpacing(6)

        # Show count of API keys already detected from environment
        _providers = ["gemini_api_key", "nvidia_api_key", "openrouter_api_key",
                      "sarvam_api_key", "elevenlabs_api_key"]
        _detected  = sum(1 for k in _providers if self._pre_cfg.get(k, ""))
        if pre_groq or _detected:
            _parts = []
            if pre_groq:
                _parts.append("Groq")
            if _detected:
                _parts.append(f"{_detected} other provider{'s' if _detected > 1 else ''}")
            _env_lbl = QLabel(f"Detected from environment: {', '.join(_parts)}")
            _env_lbl.setObjectName("status")
            layout.addWidget(_env_lbl)

        self._status_lbl = QLabel("")
        self._status_lbl.setObjectName("status")
        layout.addWidget(self._status_lbl)

        layout.addStretch()

        # ── Buttons ───────────────────────────────────────────────── #
        btn_row = QHBoxLayout()
        btn_row.setSpacing(12)

        skip_btn = QPushButton("Skip setup")
        skip_btn.setObjectName("skip")
        skip_btn.clicked.connect(self._skip)

        start_btn = QPushButton("INITIALISE JARVIS")
        start_btn.setObjectName("primary")
        start_btn.setDefault(True)
        start_btn.clicked.connect(self._confirm)

        btn_row.addWidget(skip_btn)
        btn_row.addStretch()
        btn_row.addWidget(start_btn)
        layout.addLayout(btn_row)

    def _confirm(self) -> None:
        name = self._name_edit.text().strip()
        key  = self._key_edit.text().strip()

        self._cfg_result["user_name"]    = name
        self._cfg_result["groq_api_key"] = key if key.startswith("gsk_") else ""

        if name:
            self._status_lbl.setText(f"Hello, {name}. Systems initialising…")
            if self._tts:
                threading.Thread(
                    target=lambda: self._tts.speak(f"Hello, {name}. Good to meet you. Systems coming online."),
                    daemon=True,
                ).start()
        else:
            self._status_lbl.setText("Systems initialising…")
            if self._tts:
                threading.Thread(
                    target=lambda: self._tts.speak("Very well. Systems coming online."),
                    daemon=True,
                ).start()

        QTimer.singleShot(1200, self.accept)

    def _skip(self) -> None:
        self._cfg_result["user_name"]    = ""
        self._cfg_result["groq_api_key"] = ""
        self.accept()

    # Allow dragging the frameless window
    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self._drag_pos = e.globalPosition().toPoint() - self.frameGeometry().topLeft()

    def mouseMoveEvent(self, e):
        if e.buttons() == Qt.MouseButton.LeftButton and hasattr(self, "_drag_pos"):
            self.move(e.globalPosition().toPoint() - self._drag_pos)


def _first_run_wizard(cfg: dict, tts) -> dict:
    """Show GUI first-run setup dialog, update and save config."""
    dlg = _FirstRunDialog(tts, cfg)
    dlg.exec()

    result = dlg._cfg_result
    if result.get("user_name"):
        cfg["user_name"] = result["user_name"]
    if result.get("groq_api_key"):
        cfg["groq_api_key"] = result["groq_api_key"]

    cfg["_first_run_complete"] = True
    cfg_mod.save_config(cfg)
    return cfg


# ──────────────────────────────────────────────────────────────────── #
#  Ollama helpers                                                       #
# ──────────────────────────────────────────────────────────────────── #

def _fix_all_volumes() -> None:
    try:
        from ctypes import cast, POINTER
        from comtypes import CLSCTX_ALL
        from pycaw.pycaw import AudioUtilities, IAudioEndpointVolume, ISimpleAudioVolume

        for session in AudioUtilities.GetAllSessions():
            try:
                vol = session._ctl.QueryInterface(ISimpleAudioVolume)
                if vol.GetMasterVolume() < 0.98:
                    vol.SetMasterVolume(1.0, None)
            except Exception:
                pass

        try:
            from pycaw.pycaw import AudioUtilities as AU2
            speakers = AU2.GetSpeakers()
            dev = getattr(speakers, "_dev", speakers)
            iface = dev.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None)
            endpoint = cast(iface, POINTER(IAudioEndpointVolume))
            if endpoint.GetMute():
                endpoint.SetMute(False, None)
            if endpoint.GetMasterVolumeLevelScalar() < 0.25:
                endpoint.SetMasterVolumeLevelScalar(0.75, None)
        except Exception:
            pass
    except Exception as e:
        print(f"[Main] Volume fix error: {e}")


def _fix_app_volume_async() -> None:
    def _worker():
        try:
            import pythoncom
            pythoncom.CoInitialize()
        except Exception:
            pass
        try:
            import numpy as np, sounddevice as sd
            sd.play(np.zeros(2205, dtype=np.float32), 44100)
            sd.wait()
        except Exception:
            pass
        time.sleep(0.5)
        _fix_all_volumes()

    threading.Thread(target=_worker, daemon=True, name="VolumeFix").start()


def _ollama_alive(host: str) -> bool:
    try:
        return requests.get(f"{host}/api/tags", timeout=3).status_code == 200
    except Exception:
        return False


def _start_ollama() -> None:
    print("[Main] Starting Ollama…")
    subprocess.Popen(
        ["ollama", "serve"],
        shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    time.sleep(3)


_GROQ_MODELS = {
    "llama-3.1-8b-instant", "llama-3.2-3b-preview", "llama3-8b-8192",
    "gemma2-9b-it", "mixtral-8x7b-32768", "llama-3.3-70b-versatile",
}


def _ensure_model(host: str, model: str, groq_key: str = "") -> None:
    if model in _GROQ_MODELS or (groq_key and groq_key.startswith("gsk_")):
        print(f"[Main] Using Groq cloud model '{model}' — no local pull needed.")
        return
    try:
        data = requests.get(f"{host}/api/tags", timeout=5).json()
        pulled = [m["name"].split(":")[0] for m in data.get("models", [])]
        if model.split(":")[0] in pulled:
            print(f"[Main] Model '{model}' is available.")
            return
    except Exception:
        pass
    print(f"[Main] Model '{model}' not found locally — pulling now…")
    subprocess.run(["ollama", "pull", model], check=False)


# ──────────────────────────────────────────────────────────────────── #
#  Settings dialog (existing, kept intact)                             #
# ──────────────────────────────────────────────────────────────────── #

class _StableCombo(QComboBox):
    """QComboBox that activates its parent window before opening the popup.

    On Windows, if the dialog doesn't own foreground focus (e.g. opened
    from the system tray), the first click goes to activating the window
    rather than the combo box, causing the popup to close immediately.
    Forcing activateWindow() before showPopup() prevents this.
    """
    def showPopup(self):
        win = self.window()
        win.activateWindow()
        win.raise_()
        super().showPopup()


class SettingsDialog(QDialog):
    _GROQ_MODELS = [
        "llama-3.3-70b-versatile",
        "llama-3.1-8b-instant",
        "llama-3.2-3b-preview",
        "llama3-8b-8192",
        "gemma2-9b-it",
        "mixtral-8x7b-32768",
    ]

    def __init__(self, config: dict, parent=None,
                 ollama_models=None, sapi_voices=None):
        super().__init__(parent)
        self._cfg = config
        self.setWindowTitle("JARVIS Settings")
        self.setWindowFlags(Qt.WindowType.Dialog)
        self.setFixedSize(480, 540)
        self.setStyleSheet("""
            QDialog   { background: #0A0A1E; color: #F0F0F0; }
            QLabel    { color: #F0F0F0; }
            QSlider::groove:horizontal { background: #1A1A3E; height: 4px; border-radius: 2px; }
            QSlider::handle:horizontal { background: #00D4FF; width: 14px; height: 14px;
                                          border-radius: 7px; margin: -5px 0; }
            QComboBox, QLineEdit { background: #1A1A3E; color: #F0F0F0;
                                    border: 1px solid #00D4FF; padding: 4px; border-radius: 4px; }
            QCheckBox { color: #F0F0F0; }
            QPushButton { background: #00D4FF; color: #000; border: none;
                          padding: 8px 18px; border-radius: 6px; font-weight: bold; }
            QPushButton:hover { background: #00B8E0; }
            QPushButton#cancel { background: #2A2A4A; color: #F0F0F0; }
        """)
        self._build_ui(ollama_models or [], sapi_voices or [])

    def showEvent(self, event):
        super().showEvent(event)
        QTimer.singleShot(50, self._claim_foreground)

    def _claim_foreground(self):
        self.activateWindow()
        self.raise_()
        try:
            import ctypes
            user32  = ctypes.windll.user32
            kernel32 = ctypes.windll.kernel32
            hwnd    = int(self.winId())
            fg_hwnd = user32.GetForegroundWindow()
            fg_tid  = user32.GetWindowThreadProcessId(fg_hwnd, None)
            my_tid  = kernel32.GetCurrentThreadId()
            if fg_tid and fg_tid != my_tid:
                user32.AttachThreadInput(fg_tid, my_tid, True)
                user32.SetForegroundWindow(hwnd)
                user32.AttachThreadInput(fg_tid, my_tid, False)
            else:
                user32.SetForegroundWindow(hwnd)
        except Exception:
            pass

    def _row(self, label, widget):
        row = QHBoxLayout()
        lbl = QLabel(label)
        lbl.setFixedWidth(150)
        row.addWidget(lbl)
        row.addWidget(widget)
        return row

    def _build_ui(self, ollama_models, sapi_voices):
        vbox = QVBoxLayout(self)
        vbox.setSpacing(10)
        vbox.setContentsMargins(22, 18, 22, 18)

        def _sep(label):
            w = QLabel(label)
            w.setStyleSheet("color: #00D4FF; font-size: 10px; padding-top: 4px;")
            return w

        vbox.addWidget(_sep("── Voice & Mic ──────────────────────────────"))

        self._mic_slider = QSlider(Qt.Orientation.Horizontal)
        self._mic_slider.setRange(10, 80)
        self._mic_slider.setValue(int(self._cfg.get("mic_sensitivity", 3.5) * 10))
        vbox.addLayout(self._row("Clap sensitivity:", self._mic_slider))

        self._rate_slider = QSlider(Qt.Orientation.Horizontal)
        self._rate_slider.setRange(100, 260)
        self._rate_slider.setValue(self._cfg.get("voice_rate", 165))
        vbox.addLayout(self._row("Voice speed:", self._rate_slider))

        self._voice_combo = _StableCombo()
        if sapi_voices:
            self._voice_combo.addItems(sapi_voices)
            idx = self._voice_combo.findText(self._cfg.get("tts_voice", ""))
            if idx >= 0:
                self._voice_combo.setCurrentIndex(idx)
        else:
            self._voice_combo.addItem("(no SAPI voices found)")
        vbox.addLayout(self._row("TTS voice:", self._voice_combo))

        vbox.addWidget(_sep("── AI Backend ───────────────────────────────"))

        self._groq_edit = QLineEdit()
        self._groq_edit.setPlaceholderText("Groq API key (gsk_…) — free at groq.com/keys")
        self._groq_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self._groq_edit.setText(self._cfg.get("groq_api_key", ""))
        vbox.addLayout(self._row("Groq API key:", self._groq_edit))

        self._model_combo = _StableCombo()
        cur_model = self._cfg.get("model", "llama-3.3-70b-versatile")
        all_models = [f"[Groq] {m}" for m in self._GROQ_MODELS]
        if ollama_models:
            all_models += [f"[Ollama] {m}" for m in ollama_models]
        else:
            all_models.append("[Ollama] (none installed)")
        self._model_combo.addItems(all_models)
        for i, entry in enumerate(all_models):
            if cur_model in entry:
                self._model_combo.setCurrentIndex(i)
                break
        vbox.addLayout(self._row("AI model:", self._model_combo))

        self._stt_combo = _StableCombo()
        _STT_OPTIONS = [
            "tiny.en (~40 MB, fastest)",
            "base.en (~150 MB, balanced)",
            "small.en (~460 MB, most accurate)",
        ]
        self._stt_combo.addItems(_STT_OPTIONS)
        cur_stt = self._cfg.get("stt_model", "base.en")
        for i, opt in enumerate(_STT_OPTIONS):
            if opt.startswith(cur_stt):
                self._stt_combo.setCurrentIndex(i)
                break
        vbox.addLayout(self._row("STT model:", self._stt_combo))

        vbox.addWidget(_sep("── Behaviour ────────────────────────────────"))

        self._wake_chk = QCheckBox("Hey JARVIS wake word (always listening)")
        self._wake_chk.setChecked(self._cfg.get("wake_word_enabled", True))
        vbox.addWidget(self._wake_chk)

        self._proactive_chk = QCheckBox("Proactive observations (JARVIS speaks unprompted)")
        self._proactive_chk.setChecked(self._cfg.get("proactive_mode", True))
        vbox.addWidget(self._proactive_chk)

        self._emotion_chk = QCheckBox("Emotion detection (voice + face)")
        self._emotion_chk.setChecked(self._cfg.get("emotion_detection", True))
        vbox.addWidget(self._emotion_chk)

        self._startup_chk = QCheckBox("Start with Windows")
        self._startup_chk.setChecked(self._cfg.get("startup_with_windows", False))
        vbox.addWidget(self._startup_chk)

        vbox.addStretch()

        btns = QHBoxLayout()
        cancel = QPushButton("Cancel")
        cancel.setObjectName("cancel")
        cancel.clicked.connect(self.reject)
        save = QPushButton("Save")
        save.clicked.connect(self._save)
        btns.addStretch()
        btns.addWidget(cancel)
        btns.addWidget(save)
        vbox.addLayout(btns)

    def _save(self):
        self._cfg["mic_sensitivity"]   = self._mic_slider.value() / 10.0
        self._cfg["voice_rate"]        = self._rate_slider.value()
        self._cfg["tts_rate"]          = self._rate_slider.value()
        self._cfg["groq_api_key"]      = self._groq_edit.text().strip()
        self._cfg["wake_word_enabled"] = self._wake_chk.isChecked()
        self._cfg["proactive_mode"]    = self._proactive_chk.isChecked()
        self._cfg["emotion_detection"] = self._emotion_chk.isChecked()
        self._cfg["startup_with_windows"] = self._startup_chk.isChecked()

        raw_model = self._model_combo.currentText()
        for prefix in ("[Groq] ", "[Ollama] "):
            if raw_model.startswith(prefix):
                raw_model = raw_model[len(prefix):]
                break
        self._cfg["model"] = raw_model
        self._cfg["groq_model"] = raw_model

        voice_text = self._voice_combo.currentText()
        if voice_text and not voice_text.startswith("("):
            self._cfg["tts_voice"] = voice_text

        stt_text = self._stt_combo.currentText()
        stt_model = stt_text.split(" ")[0]  # e.g. "base.en"
        self._cfg["stt_model"] = stt_model

        cfg_mod.save_config(self._cfg)
        _set_windows_startup(self._startup_chk.isChecked())
        self.accept()


def _set_windows_startup(enabled: bool) -> None:
    import winreg
    _KEY = r"SOFTWARE\Microsoft\Windows\CurrentVersion\Run"
    _NAME = "JARVIS"
    bat = os.path.join(os.path.dirname(os.path.abspath(__file__)), "Start JARVIS.bat")
    cmd = f'"{bat}" --minimized'
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _KEY, 0, winreg.KEY_SET_VALUE) as key:
            if enabled:
                winreg.SetValueEx(key, _NAME, 0, winreg.REG_SZ, cmd)
            else:
                try:
                    winreg.DeleteValue(key, _NAME)
                except FileNotFoundError:
                    pass
    except Exception as e:
        print(f"[Startup] Registry error: {e}")


# ──────────────────────────────────────────────────────────────────── #
#  Core JARVIS controller v2.0                                         #
# ──────────────────────────────────────────────────────────────────── #

class JARVIS:
    def __init__(self):
        self._cfg      = cfg_mod.load_config()
        self._app      = QApplication.instance() or QApplication(sys.argv)

        self._hud     = JARVISHud()
        self._signals = self._hud.signals

        self._conv_state = ConversationState(
            activate_fn=lambda: self._activate("question_followup"),
            config=self._cfg,
        )
        self._clap: Optional[ClapListener]     = None
        self._wakeword: Optional[WakeWordListener] = None
        self._brain: Optional[Brain]            = None
        self._tray: Optional[SystemTray]        = None
        self._monitor: Optional[SystemMonitor]  = None

        # ── New subsystems ────────────────────────────────────────── #
        self._memory: Optional[MemoryEngine]       = None
        self._conv: Optional[ConversationEngine]   = None
        self._context: Optional[ContextEngine]     = None
        self._emotion: Optional[EmotionEngine]     = None
        self._initiator: Optional[ProactiveInitiator] = None
        self._task_chain: Optional[TaskChain]      = None
        self._agent: Optional[AutonomousAgent]     = None
        self._meeting: Optional[MeetingAssistant]  = None
        self._war_room: Optional["WarRoom"]        = None
        self._browser: Optional["BrowserController"] = None

        # ── New voice-engine subsystems ───────────────────────────── #
        self._tts_engine       = None  # TTSEngine if available
        self._stt_engine       = None  # STTEngine if available
        self._turn_detector    = None  # TurnDetector if available
        self._terminal_thread  = None  # TerminalInputThread if available

        self._processing  = False
        self._sleeping    = False
        self._lock        = threading.Lock()
        self._session_start = time.monotonic()

        # ── Connect signals ───────────────────────────────────────── #
        self._signals.show_settings.connect(self._show_settings)
        self._signals.show_war_room.connect(self._show_war_room)
        self._signals.hide_war_room.connect(self._hide_war_room)
        self._settings_sig = _SettingsSignaler()
        self._settings_sig.ready.connect(self._open_settings_dialog)

        self._muted = False
        self._interview_silence_timer: Optional[threading.Timer] = None
        self._interview_prompt_count: int = 0
        # Preload ALL C-extension DLLs on the main thread BEFORE _build_subsystems()
        # starts the STTEngine background thread that loads ctranslate2/faster_whisper.
        # Without this ordering, the main-thread import of ctranslate2 races with the
        # STT background thread loading it → 0xC0000005 ACCESS_VIOLATION.
        _preload_native_extensions()
        self._build_subsystems()
        self._build_tools()
        self._build_brain()
        self._signals.toggle_persona.connect(self._on_toggle_persona)
        self._signals.toggle_mute.connect(self._on_toggle_mute)

    # ------------------------------------------------------------------ #
    #  Subsystem initialization                                            #
    # ------------------------------------------------------------------ #

    def _build_subsystems(self) -> None:
        # Memory engine
        if _HAS_MEMORY:
            try:
                self._memory = MemoryEngine()
                self._memory.start_session()
                print("[Main] MemoryEngine started.")
            except Exception as e:
                print(f"[Main] MemoryEngine init error: {e}")

        # Conversation engine
        if _HAS_CONV:
            try:
                self._conv = ConversationEngine()
                print("[Main] ConversationEngine started.")
                # Write today's session topic so tomorrow's greeting is meaningful
                try:
                    import pathlib, json as _json
                    _logs_dir = pathlib.Path(os.path.expandvars("%APPDATA%")) / "JARVIS" / "logs"
                    _logs_dir.mkdir(parents=True, exist_ok=True)
                    _today_log = _logs_dir / f"{__import__('datetime').date.today()}.json"
                    _existing  = _json.loads(_today_log.read_text(encoding="utf-8")) if _today_log.exists() else {"date": str(__import__('datetime').date.today()), "exchanges": []}
                    _existing["session_topic"] = "JARVIS development — code fixes and feature improvements"
                    _today_log.write_text(_json.dumps(_existing, ensure_ascii=False, indent=2), encoding="utf-8")
                except Exception:
                    pass
            except Exception as e:
                print(f"[Main] ConversationEngine init error: {e}")

        # Learn name from config if set during wizard
        if self._memory and self._cfg.get("user_name"):
            try:
                self._memory.set_name(self._cfg["user_name"])
            except Exception:
                pass

        # ── New voice-engine subsystems ───────────────────────────── #
        if _HAS_TTS_ENGINE:
            try:
                self._tts_engine = TTSEngine(self._cfg)
                print("[Main] TTSEngine ready.")
            except Exception as e:
                print(f"[Main] TTSEngine init error: {e}")

        if _HAS_STT_ENGINE:
            try:
                self._stt_engine = STTEngine(self._cfg)
                print("[Main] STTEngine ready.")
            except Exception as e:
                print(f"[Main] STTEngine init error: {e}")

        if _HAS_TURN_DETECTOR:
            try:
                self._turn_detector = TurnDetector(sample_rate=16000)
                print("[Main] TurnDetector ready.")
            except Exception as e:
                print(f"[Main] TurnDetector init error: {e}")

    # ------------------------------------------------------------------ #
    #  Tool registry                                                       #
    # ------------------------------------------------------------------ #

    def _build_tools(self) -> None:
        cfg = self._cfg
        def spk(text: str) -> None:
            self._speak(text)   # language-aware: routes Hindi through TTSEngine
        pa_tools._set_speak(spk)
        meeting_tools._set_speak(spk)
        presenter_tools._set_speak(spk)
        presenter_tools._set_tts(self._tts_engine)
        presenter_tools._set_busy_hooks(
            self._pause_background_services,
            self._resume_background_services,
        )
        meeting_tools._set_busy_hooks(
            self._pause_background_services,
            self._resume_background_services,
        )

        self._tools: dict = {
            # ── System ──────────────────────────────────────────── #
            "open_app":          sys_tools.open_app,
            "close_app":         sys_tools.close_app,
            "list_running_apps": sys_tools.list_running_apps,
            "type_text":         sys_tools.type_text,
            "press_keys":        sys_tools.press_keys,
            "scroll":            sys_tools.scroll,
            "take_screenshot":   sys_tools.take_screenshot,
            "read_screen":       lambda: sys_tools.read_screen(cfg),
            "read_document_camera": lambda task="ocr", image_path="": sys_tools.read_document_camera(task=task, image_path=image_path, config=cfg),
            "run_command":       sys_tools.run_command,
            "open_url":          sys_tools.open_url,
            "set_volume":        sys_tools.set_volume,
            "get_clipboard":     sys_tools.get_clipboard,
            "set_clipboard":     sys_tools.set_clipboard,
            "get_battery":       sys_tools.get_battery,
            "get_system_info":   sys_tools.get_system_info,
            "lock_screen":       sys_tools.lock_screen,
            "shutdown":          sys_tools.shutdown,
            "get_wifi_networks": sys_tools.get_wifi_networks,
            # ── Files ───────────────────────────────────────────── #
            "find_file":         file_tools.find_file,
            "read_file":         file_tools.read_file,
            "create_file":       file_tools.create_file,
            "list_directory":    file_tools.list_directory,
            "move_file":         file_tools.move_file,
            "delete_file":       file_tools.delete_file,
            "open_file":         file_tools.open_file,
            # ── Web ─────────────────────────────────────────────── #
            "get_news":              web_tools.get_news,
            "web_search":            web_tools.web_search,
            "get_weather":           web_tools.get_weather,
            "get_weather_forecast":  web_tools.get_weather_forecast,
            "get_live_score":        web_tools.get_live_score,
            "get_location":          web_tools.get_location,
            # ── Utils ───────────────────────────────────────────── #
            "get_datetime":  util_tools.get_datetime,
            "set_reminder":  lambda message, minutes: util_tools.set_reminder(
                                 message, minutes, speak_fn=spk),
            "calculate":     util_tools.calculate,
            # ── PA tools ────────────────────────────────────────── #
            "set_timer":         pa_tools.set_timer,
            "add_note":          pa_tools.add_note,
            "read_notes":        pa_tools.read_notes,
            "clear_notes":       pa_tools.clear_notes,
            "remember":          pa_tools.remember,
            "recall":            pa_tools.recall,
            "play_pause_media":  pa_tools.play_pause_media,
            "next_track":        pa_tools.next_track,
            "prev_track":        pa_tools.prev_track,
            "stop_media":        pa_tools.stop_media,
            "volume_up":         pa_tools.volume_up,
            "volume_down":       pa_tools.volume_down,
            "mute_toggle":       pa_tools.mute_toggle,
            "get_connected_wifi": pa_tools.get_connected_wifi,
            "get_public_ip":     pa_tools.get_public_ip,
            "export_conversation": lambda: pa_tools.export_conversation(self._brain),
            "remember_contact":  pa_tools.remember_contact,
            "list_contacts":     pa_tools.list_contacts,
            "forget_contact":    pa_tools.forget_contact,
            # ── Web extras ──────────────────────────────────────────── #
            "get_stock_price":      web_tools.get_stock_price,
            "translate_text":       web_tools.translate_text,
            "get_morning_briefing": lambda: __import__("personality.initiator", fromlist=["get_morning_briefing"]).get_morning_briefing(),
            # ── Code execution ──────────────────────────────────────── #
            "run_python":        sys_tools.run_python,
            "send_whatsapp":     sys_tools.send_whatsapp,
            "initiate_call":     sys_tools.initiate_call,
            # ── Presenter ───────────────────────────────────────────── #
            "present_file":          presenter_tools.present_file,
            "next_slide":            presenter_tools.next_slide,
            "prev_slide":            presenter_tools.prev_slide,
            "goto_slide":            presenter_tools.goto_slide,
            "read_current_slide":    presenter_tools.read_current_slide,
            "presentation_overview": presenter_tools.presentation_overview,
            "pause_presentation":    presenter_tools.pause_presentation,
            "resume_presentation":   presenter_tools.resume_presentation,
            "end_presentation":      presenter_tools.end_presentation,
            # ── Email ───────────────────────────────────────────────── #
            "draft_email":       email_tools.draft_email,
            "send_email":        email_tools.send_email,
            "read_emails":       email_tools.read_emails,
            "search_emails":     email_tools.search_emails,
            "reply_email":       email_tools.reply_email,
            "get_unread_count":  email_tools.get_unread_count,
            # ── Meeting / Interview / Focus ──────────────────────────── #
            "start_meeting":       lambda title="Meeting", agenda="", participants="": meeting_tools.start_meeting(title, agenda, participants),
            "next_agenda_item":    meeting_tools.next_agenda_item,
            "add_meeting_note":    meeting_tools.add_meeting_note,
            "record_action_item":  meeting_tools.record_action_item,
            "meeting_status":      meeting_tools.meeting_status,
            "end_meeting":         meeting_tools.end_meeting,
            "start_interview":     meeting_tools.start_interview,
            "end_interview":       meeting_tools.end_interview,
            "start_focus_session": lambda minutes=25, task="": meeting_tools.start_focus_session(int(minutes), task),
            "end_focus_session":   meeting_tools.end_focus_session,
            "focus_status":        meeting_tools.focus_status,
        }

    def _build_brain(self) -> None:
        self._filler = FillerSpeech()
        self._brain = Brain(
            config=self._cfg,
            tool_registry=self._tools,
            speak_fn=(lambda t, _e=self._tts_engine: None if _e.is_speaking() else _e.speak(t))
                     if self._tts_engine else lambda t: None,
            status_fn=lambda s: self._signals.set_state.emit(s),
            memory_engine=self._memory,
            conversation_engine=self._conv,
            context_engine=self._context,
            emotion_engine=self._emotion,
        )

    # ------------------------------------------------------------------ #
    #  Helpers                                                             #
    # ------------------------------------------------------------------ #

    def _set_status(self, state: str) -> None:
        self._signals.set_state.emit(state)

    def _pause_background_services(self) -> None:
        """Suppress proactive/monitor chatter while presenting or interviewing."""
        for svc in (self._initiator, self._monitor):
            if svc is not None:
                try:
                    svc.pause()
                except Exception:
                    pass

    def _resume_background_services(self) -> None:
        """Re-enable proactive/monitor chatter after a busy period ends."""
        for svc in (self._initiator, self._monitor):
            if svc is not None:
                try:
                    svc.resume()
                except Exception:
                    pass

    def _on_toggle_persona(self) -> None:
        """HUD center circle clicked — swap persona only, language stays unchanged."""
        try:
            current = getattr(self._brain, "_persona", "jarvis") if self._brain else "jarvis"
            new_p   = "friday" if current == "jarvis" else "jarvis"

            # Change persona only — never force a language change here.
            # Language only changes when user explicitly says "switch to Hindi / English".
            if self._brain:
                self._brain.set_persona(new_p)

            # Switch TTS voice to match persona
            if self._tts_engine:
                self._tts_engine.switch_persona(new_p)
                if new_p == "jarvis":
                    self._tts_engine.switch_language("en")  # JARVIS is always English

            # Update HUD immediately
            self._signals.set_persona_sig.emit(new_p)

            # Confirmation spoken in the NEW persona's voice
            _msg = "Friday here, sir." if new_p == "friday" else "JARVIS back online, sir."
            if self._tts_engine:
                threading.Thread(
                    target=self._tts_engine.speak,
                    args=(_msg,),
                    daemon=True,
                ).start()
        except Exception as _e:
            print(f"[JARVIS] Persona toggle error: {_e}")

    def _on_toggle_persona_by_name(self, persona: str) -> None:
        """Switch to a named persona — called from terminal /friday or /jarvis."""
        try:
            if self._brain:
                self._brain.set_persona(persona)
            if self._tts_engine:
                self._tts_engine.switch_persona(persona)
                if persona == "jarvis":
                    self._tts_engine.switch_language("en")
            self._signals.set_persona_sig.emit(persona)
            _msg = "Friday here, sir." if persona == "friday" else "JARVIS back online, sir."
            if self._tts_engine:
                threading.Thread(target=self._tts_engine.speak, args=(_msg,), daemon=True).start()
        except Exception as _e:
            print(f"[JARVIS] Persona switch error: {_e}")

    def _on_toggle_mute(self) -> None:
        """HUD mute zone clicked — pause or resume listening."""
        self._muted = not self._muted
        self._signals.set_muted_sig.emit(self._muted)
        if self._muted:
            if self._wakeword:
                self._wakeword.pause()
            self._speak("Microphone muted.")
        else:
            if self._wakeword:
                self._wakeword.resume()
            self._speak("Listening resumed.")

    # ------------------------------------------------------------------ #
    #  Interview patience — speak prompts while waiting for an answer     #
    # ------------------------------------------------------------------ #

    def _start_interview_patience(self) -> None:
        """Start a patience timer after JARVIS asks an interview question."""
        self._cancel_interview_patience()
        self._interview_prompt_count = 0
        t = threading.Timer(20.0, self._on_interview_silence)
        t.daemon = True
        t.start()
        self._interview_silence_timer = t

    def _cancel_interview_patience(self) -> None:
        t = self._interview_silence_timer
        if t:
            t.cancel()
            self._interview_silence_timer = None

    def _on_interview_silence(self) -> None:
        """Candidate hasn't answered — prompt them patiently, then move on."""
        import tools.meeting as _mt
        if not _mt._interview_active or self._processing:
            return
        count = self._interview_prompt_count
        if count == 0:
            self._interview_prompt_count = 1
            self._speak("Take your time, sir. No rush.")
            t = threading.Timer(20.0, self._on_interview_silence)
            t.daemon = True
            t.start()
            self._interview_silence_timer = t
        elif count == 1:
            self._interview_prompt_count = 2
            self._speak("Whenever you're ready. Or say 'next question' to move on.")
            t = threading.Timer(25.0, self._on_interview_silence)
            t.daemon = True
            t.start()
            self._interview_silence_timer = t
        else:
            self._interview_silence_timer = None
            self._speak("Let's move on to the next question.")
            time.sleep(1.5)
            self._activate(source="vad", pre_text="next question please")

    def _push_war_room(self, user_text: str, jarvis_text: str) -> None:
        """Feed a completed exchange into the War Room display if it's open."""
        if self._war_room and self._war_room.isVisible():
            try:
                self._war_room.push_conversation(user_text, jarvis_text)
            except Exception:
                pass

    def _current_lang(self) -> str:
        """Current TTS language — "en" or "hi"."""
        return getattr(self._tts_engine, '_language', 'en') if self._tts_engine else 'en'

    def _speak(self, text: str) -> None:
        self._set_status("speaking")
        play_response_start()
        if self._clap:
            self._clap.pause()
        if self._wakeword:
            self._wakeword.pause()
        if self._turn_detector is not None:
            self._turn_detector.mark_processing_done()
        print(f"[JARVIS] Speaking: '{text}'")

        if self._tts_engine:
            try:
                self._tts_engine.speak(text)
            except Exception as _tts_err:
                print(f"[TTS] speak() failed: {_tts_err}")

        def _watch():
            # Echo-gate-aware cooldown: min 0.8s, max 1.8s
            try:
                from audio.noise_pipeline import _sys_audio_gate as _gate
                _gate_available = True
            except Exception:
                _gate_available = False
            cooldown_deadline = time.monotonic() + 1.8
            min_wait = time.monotonic() + 0.8
            while time.monotonic() < cooldown_deadline:
                time.sleep(0.1)
                if _gate_available and time.monotonic() > min_wait:
                    if not _gate.is_playing():
                        break
            if self._turn_detector is not None:
                self._turn_detector.mark_tts_done()
            self._unpause_listeners()
            if not self._processing:
                # Stay in "speaking" state while presentation is actively running
                try:
                    import tools.presenter as _pres
                    if _pres._session.get("slides") and not _pres._session.get("auto_stopped"):
                        return
                except Exception:
                    pass
                self._set_status("idle")

        threading.Thread(target=_watch, daemon=True).start()

    def _unpause_listeners(self) -> None:
        if self._clap:
            self._clap.resume()
        if self._wakeword:
            self._wakeword.resume()

    def _start_terminal_input(self) -> None:
        """Read typed commands from stdin — fallback when mic is noisy."""
        if _HAS_TERMINAL_THREAD and TerminalInputThread is not None:
            self._terminal_thread = TerminalInputThread(
                handle_text_fn=self._on_terminal_text,
                tts_engine=self._tts_engine,
                stt_engine=self._stt_engine,
                quit_fn=self._quit,
                get_persona_fn=lambda: getattr(self._brain, "_persona", "jarvis") if self._brain else "jarvis",
                set_persona_fn=self._on_toggle_persona_by_name,
                quiet_fn=self._pause_background_services,
                wake_fn=self._trigger_activation,
                language_switch_fn=lambda lang: handle_language_switch(
                    lang, self._tts_engine, self._stt_engine, brain=self._brain
                ),
            )
            self._terminal_thread.start()
            return

        # Legacy inline fallback (if terminal_input.py unavailable)
        def _loop():
            print("\n[JARVIS] Terminal input active. Type a command and press Enter.\n")
            while True:
                try:
                    text = input(">>> ").strip()
                except (EOFError, KeyboardInterrupt):
                    break
                if not text:
                    continue
                if self._tts_engine and self._tts_engine.is_speaking():
                    self._tts_engine.stop_immediately()
                self._activate(source="terminal", pre_text=text)
        threading.Thread(target=_loop, daemon=True, name="TerminalInput").start()

    def _trigger_activation(self) -> None:
        """Play activation sound and show HUD — used by /wake stdin command."""
        self._signals.show_win.emit()
        play_activation()

    def _on_terminal_text(self, text: str) -> None:
        """Dispatch text typed in the terminal to the JARVIS pipeline."""
        print(f"[You] {text}")
        if self._tts_engine and self._tts_engine.is_speaking():
            self._tts_engine.stop_immediately()
        # Reset conv_state so can_activate() passes even if JARVIS was mid-turn.
        # _activate() also bypasses _processing for terminal source.
        self._conv_state.interrupted()
        self._activate(source="terminal", pre_text=text)

    def _start_vad_pipeline(self) -> None:
        """Start the continuous VAD mic pipeline (event-driven turn detection).

        Runs alongside the existing wakeword/clap activation — they coexist.
        When the VAD detector triggers on_speech_end, the transcribed text
        is passed to the existing _activate() as pre_text, skipping STT.
        """
        if not _HAS_TURN_DETECTOR or self._turn_detector is None:
            return
        if not _HAS_STT_ENGINE or self._stt_engine is None:
            return

        td = self._turn_detector

        def _on_speech_start():
            # Don't flip HUD to "listening" while JARVIS is speaking —
            # that's just TTS echo being picked up by the mic.
            if td.get_state() != "SPEAKING":
                self._set_status("listening")
            # Pre-warm Groq TCP connection so first token arrives faster
            if self._brain:
                threading.Thread(
                    target=self._brain._router._warm_groq, daemon=True
                ).start()

        def _on_speech_end(audio):
            if self._muted:
                td.set_state("IDLE")
                return

            # If already processing another request, discard silently without
            # changing the UI state (the pipeline will reset it when done).
            if self._processing:
                td.set_state("IDLE")
                return

            # Show "thinking" immediately — STT can take 2–5s on CPU
            self._set_status("thinking")
            try:
                text = self._stt_engine.transcribe(audio).strip()
            except Exception as e:
                print(f"[VAD] STT error: {e}")
                self._set_status("idle")
                td.set_state("IDLE")
                return
            if not text:
                self._set_status("idle")
                td.set_state("IDLE")
                return

            # Whisper hallucination guard: same word/syllable repeated 5+ times
            # (happens when mic picks up TTS speaker output — yields "हाँ हाँ हाँ...")
            _wds = text.split()
            if len(_wds) >= 5 and len(set(w.lower() for w in _wds)) / len(_wds) < 0.25:
                self._set_status("idle")
                td.set_state("IDLE")
                return

            tl = text.lower().strip(" .,!?")
            if tl in self._NOISE or not any(c.isalpha() for c in tl):
                td.set_state("IDLE")
                return
            if any(p in tl for p in self._SELF_PHRASES):
                td.set_state("IDLE")
                return

            # Language switch detection
            if _HAS_LANG_SWITCH:
                lang = check_language_switch(text)
                if lang:
                    handle_language_switch(lang, self._tts_engine, self._stt_engine, td, brain=self._brain)
                    # Sync TTS persona with brain persona after language switch
                    new_persona = getattr(self._brain, "_persona", "jarvis") if self._brain else "jarvis"
                    if self._tts_engine:
                        self._tts_engine.switch_persona(new_persona)
                    self._signals.set_persona_sig.emit(new_persona)
                    td.set_state("IDLE")
                    return

            print(f"[JARVIS] Heard (voice): '{text}'")
            self._activate(source="vad", pre_text=text)

        def _on_interrupt():
            if self._tts_engine:
                self._tts_engine.stop_immediately()

        td.on_speech_start = _on_speech_start
        td.on_speech_end   = _on_speech_end
        td.on_interrupt    = _on_interrupt

        def _mic_loop():
            import sounddevice as sd
            import numpy as np
            sample_rate = 16000
            chunk_ms    = 30
            chunk_frames = int(sample_rate * chunk_ms / 1000)
            print("[VAD] Mic pipeline started.")
            try:
                with sd.InputStream(
                    samplerate=sample_rate,
                    channels=1,
                    dtype="float32",
                    blocksize=chunk_frames,
                ) as stream:
                    while True:
                        chunk, _ = stream.read(chunk_frames)
                        raw = chunk.flatten()
                        try:
                            processed, _, _ = process_mic_chunk(raw, sample_rate)
                        except Exception:
                            processed = raw
                        td.feed(processed)
            except Exception as e:
                print(f"[VAD] Mic pipeline error: {e}")

        threading.Thread(target=_mic_loop, daemon=True, name="VAD-Mic").start()

    # ------------------------------------------------------------------ #
    #  Activation                                                          #
    # ------------------------------------------------------------------ #

    def _on_clap(self) -> None:
        if self._tts_engine and self._tts_engine.is_speaking():
            self._tts_engine.stop_immediately()
        self._activate(source="clap")

    def _on_wake_word(self, command_text: str = "") -> None:
        if self._tts_engine and self._tts_engine.is_speaking():
            self._tts_engine.stop_immediately()
        self._activate(source="wakeword", pre_text=command_text)

    def _activate(self, source: str, pre_text: str = "") -> None:
        self._cancel_interview_patience()
        is_terminal = (source == "terminal")
        if not is_terminal and source != "question_followup" and not self._conv_state.can_activate():
            return
        with self._lock:
            if self._processing and not is_terminal:
                return
            now = time.monotonic()
            # Terminal and VAD bypass cooldown — VAD already filters with silence detection
            if source not in ("terminal", "vad") and now - getattr(self, "_last_activated_at", 0) < 4.0:
                return
            self._last_activated_at = now
            self._processing = True

        self._signals.show_win.emit()
        self._cancel_hide()
        print(f"[JARVIS] Activated via {source}" + (f" | '{pre_text}'" if pre_text else ""))
        play_activation()

        # Watchdog: if pipeline hangs longer than 90s (e.g. all LLM providers
        # timing out simultaneously), force-reset so new inputs are accepted.
        def _watchdog():
            time.sleep(90)
            if self._processing:
                print("[JARVIS] Watchdog: pipeline took >90s — force-resetting.")
                self._processing = False
                self._unpause_listeners()
                self._set_status("idle")
        threading.Thread(target=_watchdog, daemon=True, name="PipelineWatchdog").start()

        threading.Thread(
            target=self._pipeline, args=(pre_text, False), daemon=True, name="Pipeline"
        ).start()

    _HIDE_DELAY = 15.0

    def _cancel_hide(self) -> None:
        t = getattr(self, "_hide_timer", None)
        if t and t.is_alive():
            t.cancel()
        self._hide_timer = None

    def _schedule_hide(self) -> None:
        self._cancel_hide()
        t = threading.Timer(self._HIDE_DELAY, self._signals.hide_win.emit)
        t.daemon = True
        t.start()
        self._hide_timer = t

    # ------------------------------------------------------------------ #
    #  Pipeline                                                            #
    # ------------------------------------------------------------------ #

    _NOISE = frozenset({
        "you", "you.", "hmm", "um", "uh", "ah", "oh", "hm",
        "no", "no.", ".", "...", " ", "bye", "what", "huh",
        # Hindi Whisper hallucinations (TTS echo picked up by mic)
        "हाँ", "हाँ।", "हां", "हां।", "han", "haan",
        "ठीक है", "ठीक है।", "धन्यवाद", "धन्यवाद।",
        "जी", "जी।", "जी हाँ", "जी हाँ।",
    })
    _CONTINUE = frozenset({
        "yes", "yeah", "sure", "ok", "okay", "go on", "continue",
        "and", "also", "more", "tell me more",
    })
    _SELF_PHRASES = (
        "jarvis here", "say the word", "ready when you are",
        "all systems nominal", "initialized", "online and standing by",
        "always listening", "give me something to work with",
        "at your service", "standing by", "good morning", "good evening",
        # Catch JARVIS's own startup greeting echoing back through the mic
        "systems online", "i'm listening", "good afternoon, sir",
        "good evening, sir", "good morning, sir",
    )
    _GOODBYE = frozenset({
        "thank you", "thanks", "that's all", "thats all",
        "goodbye", "bye", "stop", "enough", "that will be all",
    })

    def _pipeline(self, pre_text: str = "", filler_queued: bool = False) -> None:
        ambient = (self._clap.ambient_rms if self._clap else 0.02)
        silence_thresh = max(ambient * 2.5, 0.005)

        try:
            if self._clap:
                self._clap.pause()
            if self._wakeword:
                self._wakeword.pause()

            # Validate pre-text
            if pre_text:
                tl = pre_text.lower().strip(" .,!?")
                if not tl or tl in self._NOISE or any(p in tl for p in self._SELF_PHRASES):
                    pre_text = ""

            if pre_text:
                text = pre_text
                print(f"[JARVIS] Wake-word command: '{text}'")
            else:
                self._set_status("listening")
                play_listening_start()
                time.sleep(0.55)

                sarvam_key = self._cfg.get("sarvam_api_key", "").strip()
                # Streaming STT only when explicitly pinned to "sarvam"
                # Auto mode uses local Whisper (faster, offline, no network round-trip)
                use_streaming = bool(
                    sarvam_key
                    and self._cfg.get("stt_provider", "auto") == "sarvam"
                )

                audio = None
                if use_streaming:
                    # Live partial transcript → conversation panel while user speaks
                    def _on_partial(partial: str) -> None:
                        if partial:
                            self._signals.show_text.emit(partial + "…", "")
                    text = self._stt_engine.listen_sarvam_streaming(
                        on_partial=_on_partial,
                        max_seconds=10.0,
                    ).strip() if self._stt_engine else ""
                else:
                    audio = self._stt_engine.record(
                        max_seconds=10.0,
                        silence_threshold=silence_thresh,
                        silence_duration=1.5,
                    ) if self._stt_engine else None
                    text = self._stt_engine.transcribe(audio).strip() if self._stt_engine and audio is not None else ""

                if not text:
                    print("[JARVIS] No speech detected.")
                    return
                tl = text.lower().strip(" .,!?")
                if tl in self._NOISE or any(p in tl for p in self._SELF_PHRASES):
                    print(f"[JARVIS] Filtered: '{text}'")
                    return
                # Discard transcriptions with no real words (e.g. ". . . . . ." TTS echo)
                if not any(c.isalpha() for c in tl):
                    print(f"[JARVIS] Filtered (no words): '{text}'")
                    return
                print(f"[JARVIS] Heard: '{text}'")

                # Emotion analysis on voice audio (local Whisper path only)
                if self._emotion and audio is not None:
                    try:
                        self._emotion.analyze_voice(audio, text)
                    except Exception:
                        pass

            # ── Conversation loop ────────────────────────────────── #
            MAX_TURNS = 4
            for turn in range(MAX_TURNS):
                tl = text.lower()

                # ── Built-in commands ──────────────────────────── #
                if any(w in tl for w in ("sleep", "go to sleep", "stand by", "standby")):
                    self._speak("Standing by, sir.")
                    self._sleeping = True
                    self._signals.hide_win.emit()
                    return

                # ── Persona switch via voice ───────────────────── #
                if any(w in tl for w in ("switch to friday", "activate friday",
                                         "hey friday", "friday mode")):
                    self._on_toggle_persona_by_name("friday")
                    return
                if any(w in tl for w in ("switch to jarvis", "activate jarvis",
                                         "jarvis mode", "back to jarvis")):
                    self._on_toggle_persona_by_name("jarvis")
                    return

                # ── Interview: skip / next question ───────────── #
                import tools.meeting as _mt_iv
                if _mt_iv._interview_active and any(
                    p in tl for p in (
                        "next question", "skip", "skip this", "move on",
                        "next one", "pass", "pass this",
                    )
                ):
                    text = (
                        "The candidate wants to skip this question. "
                        "Acknowledge briefly and ask the next interview question."
                    )

                # ── Task chain detection ──────────────────────── #
                if self._task_chain and self._task_chain.is_chain_command(text):
                    chain_name = self._task_chain.get_chain_name(text)
                    if chain_name:
                        self._set_status("thinking")
                        self._task_chain.run(chain_name)
                        break

                # ── Meeting commands ───────────────────────────── #
                if self._meeting:
                    meeting_reply = self._meeting.handle_command(text)
                    if meeting_reply:
                        self._signals.show_text.emit(text, meeting_reply)
                        self._speak(meeting_reply)
                        break

                # ── Stop agent / browser ─────────────────────── #
                if tl.strip() in ("stop", "abort", "cancel"):
                    if self._agent:
                        self._agent.request_stop()
                    if self._browser and self._browser._active:
                        self._browser.stop()
                    self._speak("Stopping.")
                    break

                # ── Browser control ───────────────────────────── #
                if _HAS_BROWSER and self._browser and is_browser_command(text):
                    self._set_status("thinking")
                    result = self._browser.execute(text)
                    if result:
                        self._signals.show_text.emit(text, result)
                        self._speak(result)
                    break

                # ── Settings ──────────────────────────────────── #
                if any(w in tl for w in ("open settings", "show settings", "jarvis settings")):
                    self._speak("Opening settings, sir.")
                    self._signals.show_settings.emit()
                    return

                # ── War room / World monitor ────────────────────── #
                if any(w in tl for w in ("show war room", "open war room", "war room",
                                          "world monitor", "show world monitor",
                                          "open world monitor", "pull world monitor",
                                          "pull up world monitor")) or \
                        ("world" in tl and "monitor" in tl):
                    # Emit first — TTS failure must not prevent the window from opening
                    self._signals.show_war_room.emit()
                    self._speak("Pulling up the world monitor, sir.")
                    break
                if any(w in tl for w in (
                    "hide war room", "close war room", "dismiss war room",
                    "hide world monitor", "close world monitor", "dismiss world monitor",
                    "close monitor", "close the monitor", "hide monitor",
                    "take it down", "take down the monitor", "shut it down",
                    "get rid of it", "remove it", "turn it off",
                )):
                    self._signals.hide_war_room.emit()
                    self._speak("War room dismissed, sir.")
                    break
                if "move to second screen" in tl or "war room second" in tl:
                    if self._war_room:
                        self._war_room.show_on_screen(1)
                    self._speak("Moving war room to second screen, sir.")
                    break
                if "move to main screen" in tl or "war room main" in tl:
                    if self._war_room:
                        self._war_room.show_on_screen(0)
                    self._speak("War room on main screen, sir.")
                    break

                # ── Greetings ─────────────────────────────────── #
                if any(w in tl for w in ("good morning", "good evening", "good afternoon",
                                          "hello jarvis", "hi jarvis")):
                    from datetime import datetime as _dt
                    h = _dt.now().hour
                    tod = ("Good morning" if h < 12 else
                           "Good afternoon" if h < 17 else "Good evening")
                    reply = f"{tod}, sir. What can I do for you?"
                    self._signals.show_text.emit(text, reply)
                    self._speak(reply)
                    break

                # ── Agentic tasks ─────────────────────────────── #
                elif _HAS_AGENT and self._agent and is_agentic_request(text):
                    self._set_status("thinking")

                    def _confirm(plan_str: str) -> bool:
                        # Auto-confirm for now (add voice confirmation later)
                        return True

                    threading.Thread(
                        target=self._agent.run,
                        args=(text, _confirm),
                        daemon=True,
                    ).start()
                    break

                # ── Standard brain processing ─────────────────── #
                else:
                    if self._sleeping:
                        self._sleeping = False
                        self._signals.show_win.emit()

                    self._set_status("thinking")
                    _query = text

                    # ── Filler speech — zero latency ──────────── #
                    # Only queue if VAD didn't already queue one in _on_speech_end
                    if not filler_queued:
                        try:
                            filler = self._filler.get_for_input(text, language=self._current_lang())
                            if filler and self._filler.should_use_filler(text) and self._tts_engine:
                                self._tts_engine.speak_filler(filler)
                                self._set_status("speaking")
                        except Exception:
                            pass

                    _sentence_spoken = threading.Event()
                    _final_reply: list[str] = [""]
                    _hindi_buf: list[str] = []
                    _first_sentence_started = [False]

                    def _on_response(reply: str, q: str = _query) -> None:
                        _heartbeat_done.set()   # cancel heartbeat — real response is here
                        _final_reply[0] = reply
                        self._signals.show_text.emit(q, reply)
                        self._push_war_room(q, reply)
                        if self._current_lang() == 'hi' and self._tts_engine:
                            # Cut off any still-playing filler before speaking the real response
                            if self._tts_engine.is_speaking():
                                self._tts_engine.stop_immediately()
                            # Speak buffered Hindi sentences as one Sarvam call
                            full_hi = ' '.join(_hindi_buf) if _hindi_buf else reply
                            if self._turn_detector is not None:
                                self._turn_detector.mark_processing_done()
                            self._set_status("speaking")
                            self._tts_engine.speak(full_hi)   # blocking Sarvam call
                            # Post-speech cleanup — mirrors _speak()._watch() for Hindi path
                            time.sleep(0.8)
                            if self._turn_detector is not None:
                                self._turn_detector.mark_tts_done()
                            self._unpause_listeners()
                            if not self._processing:
                                self._set_status("idle")
                        elif not _sentence_spoken.is_set():
                            self._speak(reply)

                    def _on_sentence(sentence: str) -> None:
                        _heartbeat_done.set()   # cancel heartbeat — first sentence is streaming
                        _sentence_spoken.set()
                        self._set_status("speaking")
                        if self._current_lang() == 'hi':
                            _hindi_buf.append(sentence)
                        else:
                            if not _first_sentence_started[0]:
                                _first_sentence_started[0] = True
                                # Cut off any still-playing filler before the first real sentence
                                if self._tts_engine and self._tts_engine.is_speaking():
                                    self._tts_engine.stop_immediately()
                                if self._turn_detector is not None:
                                    self._turn_detector.mark_processing_done()
                            if self._tts_engine:
                                self._tts_engine.queue_sentence(sentence)

                    # Heartbeat: if LLM takes > 4.5s, say "Still on it" so user knows
                    _heartbeat_done = threading.Event()
                    def _heartbeat_fn(_done=_heartbeat_done):
                        if not _done.wait(timeout=4.5):
                            phrase = "अभी कर रही हूं।" if self._current_lang() == 'hi' else "Still on it, sir."
                            if self._tts_engine:
                                self._tts_engine.speak_filler(phrase)
                    threading.Thread(target=_heartbeat_fn, daemon=True, name="LLMHeartbeat").start()

                    self._brain.process(
                        text,
                        on_response=_on_response,
                        on_sentence=_on_sentence,
                    )
                    _heartbeat_done.set()

                # ── Wait for TTS ──────────────────────────────── #
                if self._tts_engine:
                    _safety = time.monotonic() + 45.0
                    while self._tts_engine.is_speaking() and time.monotonic() < _safety:
                        time.sleep(0.1)

                # For English streaming path: _speak() was never called so _watch()
                # never fired → manually do echo cooldown + mark TurnDetector done.
                if _sentence_spoken.is_set() and self._current_lang() != 'hi':
                    def _stream_finish(_td=self._turn_detector):
                        try:
                            from audio.noise_pipeline import _sys_audio_gate as _gate
                            deadline = time.monotonic() + 1.8
                            min_wait = time.monotonic() + 0.8
                            while time.monotonic() < deadline:
                                time.sleep(0.1)
                                if time.monotonic() > min_wait and not _gate.is_playing():
                                    break
                        except Exception:
                            time.sleep(1.0)
                        if _td is not None and _td.get_state() == "SPEAKING":
                            _td.mark_tts_done()
                        self._unpause_listeners()
                        if not self._processing:
                            self._set_status("idle")
                    threading.Thread(target=_stream_finish, daemon=True, name="StreamFinish").start()

                # ── Conversation state update ──────────────────── #
                if _final_reply[0]:
                    if reply_ends_with_question(_final_reply[0]):
                        self._conv_state.jarvis_asked_question()
                        import tools.meeting as _mt_q
                        if _mt_q._interview_active:
                            self._start_interview_patience()
                    else:
                        self._conv_state.jarvis_done_speaking()

                # ── Follow-up listen ──────────────────────────── #
                if turn >= MAX_TURNS - 1 or self._sleeping:
                    break

                # VAD pipeline owns the mic — it handles the next utterance
                if self._turn_detector is not None:
                    break

                self._set_status("listening")
                time.sleep(0.35)

                import tools.meeting as _mt_rec
                _iv_mode = bool(_mt_rec._interview_active)
                followup_audio = self._stt_engine.record(
                    max_seconds=60.0 if _iv_mode else 6.0,
                    silence_threshold=silence_thresh,
                    silence_duration=2.5 if _iv_mode else 1.2,
                ) if self._stt_engine else None
                followup_text = self._stt_engine.transcribe(followup_audio).strip() if self._stt_engine and followup_audio is not None else ""

                if not followup_text:
                    break
                # No alphabetic content → TTS echo / pure noise
                if not any(c.isalpha() for c in followup_text):
                    break
                # Too long → ambient noise transcribed as speech (skip this guard in interview mode)
                _fw = followup_text.split()
                if _iv_mode and _fw:
                    pass  # interview answers can be long — no word-count cap
                if len(_fw) > 20:
                    break
                _filler_words = {"oh", "no", "yeah", "yes", "um", "uh", "hmm", "hm", "okay"}
                if len(_fw) >= 4 and sum(1 for w in _fw if w.lower() in _filler_words) / len(_fw) > 0.6:
                    break
                tl_check = followup_text.lower().strip(" .,!?")
                if tl_check in self._NOISE or any(p in tl_check for p in self._SELF_PHRASES):
                    break
                if tl_check in self._GOODBYE or any(g in tl_check for g in self._GOODBYE):
                    self._speak("Of course, sir.")
                    time.sleep(1.5)
                    break
                if tl_check in self._CONTINUE:
                    self._speak("What else can I do for you?")
                    time.sleep(1.5)
                    audio2 = self._stt_engine.record(max_seconds=8.0,
                                                    silence_threshold=silence_thresh,
                                                    silence_duration=1.5) if self._stt_engine else None
                    followup_text = self._stt_engine.transcribe(audio2).strip() if self._stt_engine and audio2 is not None else ""
                    if not followup_text:
                        break
                    tl_check = followup_text.lower().strip(" .,!?")
                    if tl_check in self._NOISE or tl_check in self._CONTINUE:
                        break

                print(f"[JARVIS] Follow-up ({turn + 1}): '{followup_text}'")
                text = followup_text

        except Exception as e:
            print(f"[JARVIS] Pipeline error: {e}")
            import traceback; traceback.print_exc()
            try:
                self._speak("My apologies — something went sideways. Try again, sir.")
            except Exception:
                pass
        finally:
            self._processing = False
            # Safety: if TurnDetector got stuck in PROCESSING (no _speak was called),
            # reset it so the next utterance can be detected.
            if self._turn_detector is not None:
                state = self._turn_detector.get_state()
                if state == "PROCESSING":
                    self._turn_detector.set_state("IDLE")
            # Always unpause — the streaming path never calls _speak()/_watch() so
            # listeners can get stuck paused if we only unpause when TTS is silent.
            self._unpause_listeners()
            self._set_status("idle")
            self._schedule_hide()

    # ------------------------------------------------------------------ #
    #  Settings                                                            #
    # ------------------------------------------------------------------ #

    def _show_settings(self) -> None:
        def _fetch():
            ollama_models: list = []
            try:
                host = self._cfg.get("ollama_host", "http://localhost:11434")
                data = requests.get(f"{host}/api/tags", timeout=3).json()
                ollama_models = sorted({m["name"].split(":")[0]
                                        for m in data.get("models", [])})
            except Exception:
                pass
            sapi_voices: list = []
            try:
                import subprocess as _sp
                ps = ("Add-Type -AssemblyName System.Speech;"
                      "$s = New-Object System.Speech.Synthesis.SpeechSynthesizer;"
                      "$s.GetInstalledVoices() | ForEach-Object { $_.VoiceInfo.Name }")
                r = _sp.run(
                    ["powershell", "-NoProfile", "-NonInteractive", "-c", ps],
                    capture_output=True, text=True, timeout=10,
                )
                sapi_voices = [v.strip() for v in r.stdout.splitlines() if v.strip()]
            except Exception:
                pass
            self._settings_sig.ready.emit(ollama_models, sapi_voices)

        threading.Thread(target=_fetch, daemon=True, name="SettingsFetch").start()

    def _open_settings_dialog(self, ollama_models: list, sapi_voices: list) -> None:
        dlg = SettingsDialog(self._cfg, self._hud, ollama_models, sapi_voices)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self._cfg = cfg_mod.load_config()
            if self._tts_engine:
                self._tts_engine._config = self._cfg
            self._brain.set_model(self._cfg.get("model", "llama-3.3-70b-versatile"))
            self._brain.set_groq_key(self._cfg.get("groq_api_key", ""))

    # ------------------------------------------------------------------ #
    #  Startup & run                                                       #
    # ------------------------------------------------------------------ #

    def run(self, minimized: bool = False) -> None:
        self._start_minimized = minimized
        memory_facts = 0
        patterns = 0
        if self._memory:
            try:
                memory_facts = len(self._memory.get("facts", []))
            except Exception:
                pass

        if minimized:
            self._start_subsystems()
        else:
            if self._cfg.get("boot_animation", True):
                def _post_boot():
                    self._hud.show()
                    self._start_subsystems()
                self._boot_seq = BootSequence(
                    on_complete=_post_boot,
                    memory_facts=memory_facts,
                    patterns=patterns,
                )
            else:
                self._hud.show()
                self._start_subsystems()

        sys.exit(self._app.exec())

    def _start_subsystems(self) -> None:
        print("[STARK INDUSTRIES] Subsystems initializing…", flush=True)

        # System tray
        self._tray = SystemTray(
            show_fn=lambda: self._signals.show_win.emit(),
            hide_fn=lambda: self._signals.hide_win.emit(),
            settings_fn=lambda: self._signals.show_settings.emit(),
            quit_fn=self._quit,
        )
        self._tray.start()

        # Clap listener (enhanced or legacy)
        try:
            self._clap = ClapListener(self._on_clap, self._cfg)
            self._clap.calibrate()
            self._clap.start()
        except Exception as _clap_err:
            print(f"[Main] Clap listener unavailable (audio device busy?): {_clap_err}")
            self._clap = None

        # Wake word
        if self._cfg.get("wake_word_enabled", True):
            try:
                _ambient = self._clap.ambient_rms if self._clap else 0
                self._wakeword = WakeWordListener(
                    callback=self._on_wake_word,
                    ambient_rms=_ambient,
                    config=self._cfg,
                )
                self._wakeword.start()
            except Exception as _ww_err:
                print(f"[Main] Wake word listener unavailable: {_ww_err}")
                self._wakeword = None

        # Terminal input fallback (type commands when mic is noisy)
        self._start_terminal_input()

        # VAD-driven turn detector (event-driven, replaces time-based STT.record)
        self._start_vad_pipeline()

        # System monitor
        self._monitor = SystemMonitor(speak_fn=self._speak, config=self._cfg)
        self._monitor.start()

        # Context engine
        if _HAS_CONTEXT:
            try:
                self._context = ContextEngine(
                    router=self._brain._router if self._brain else None,
                    speak_fn=self._speak,
                    config=self._cfg,
                )
                self._context.start()
                if self._brain:
                    self._brain._context = self._context
                print("[STARK INDUSTRIES] Context engine online.")
            except Exception as e:
                print(f"[Main] Context engine error: {e}")

        # Emotion engine
        if _HAS_EMOTION:
            try:
                self._emotion = EmotionEngine(
                    config=self._cfg, speak_fn=self._speak
                )
                self._emotion.start()
                if self._brain:
                    self._brain._emotion = self._emotion
                print("[STARK INDUSTRIES] Emotion engine online.")
            except Exception as e:
                print(f"[Main] Emotion engine error: {e}")

        # Proactive initiator
        if _HAS_INITIATOR:
            try:
                self._initiator = ProactiveInitiator(
                    speak_fn=self._speak,
                    memory=self._memory,
                    context_engine=self._context,
                    monitor=self._monitor,
                    config=self._cfg,
                )
                self._initiator.start()
                print("[STARK INDUSTRIES] Initiator online.")
            except Exception as e:
                print(f"[Main] Initiator error: {e}")

        # Task chain
        if _HAS_TASKCHAIN:
            try:
                self._task_chain = TaskChain(
                    speak_fn=self._speak,
                    execute_tool=lambda name, args: (
                        self._tools.get(name, lambda **kw: "Tool unavailable")(**args)
                    ),
                    memory=self._memory,
                    monitor=self._monitor,
                )
                print("[STARK INDUSTRIES] Task chains online.")
            except Exception as e:
                print(f"[Main] TaskChain error: {e}")

        # Autonomous agent
        if _HAS_AGENT and self._brain:
            try:
                self._agent = AutonomousAgent(
                    router=self._brain._router,
                    tool_executor=lambda name, args: (
                        self._tools.get(name, lambda **kw: "Tool unavailable")(**args)
                    ),
                    speak_fn=self._speak,
                    status_fn=self._set_status,
                )
                print("[STARK INDUSTRIES] Autonomous agent online.")
            except Exception as e:
                print(f"[Main] Agent error: {e}")

        # Meeting assistant
        if _HAS_MEETING:
            try:
                self._meeting = MeetingAssistant(
                    speak_fn=self._speak,
                    stt=self._stt_engine,
                    router=self._brain._router if self._brain else None,
                    config=self._cfg,
                )
                self._meeting.start()
                print("[STARK INDUSTRIES] Meeting assistant online.")
            except Exception as e:
                print(f"[Main] Meeting assistant error: {e}")

        # Browser controller (lazy — Playwright launches on first use)
        if _HAS_BROWSER and self._brain:
            try:
                self._browser = BrowserController(
                    speak_fn=self._speak,
                    router=self._brain._router,
                )
                print("[STARK INDUSTRIES] Browser controller ready.")
            except Exception as e:
                print(f"[Main] Browser controller error: {e}")

        # War room — auto-launch if second monitor present or config says so
        if _HAS_WAR_ROOM:
            try:
                screens = QApplication.screens()
                auto = self._cfg.get("war_room_auto", False)
                if auto or len(screens) > 1:
                    self._war_room = WarRoom(
                        memory=self._memory,
                        context_engine=self._context,
                        monitor=self._monitor,
                        conversation_engine=self._conv,
                    )
                    self._war_room.show_on_screen(1 if len(screens) > 1 else 0)
                    print("[STARK INDUSTRIES] War room online.")
            except Exception as e:
                print(f"[Main] War room error: {e}")

        # RMS feed to HUD — must be instance variable or Python GC deletes it immediately
        self._rms_timer = QTimer()
        self._rms_timer.timeout.connect(
            lambda: self._signals.update_rms.emit(
                self._clap.current_rms if self._clap else 0.0
            )
        )
        self._rms_timer.start(50)

        # Whisper model load — started LAST, after all audio streams are open and stable.
        # Starting it earlier races with ClapListener/VAD sounddevice init → 0xC0000005.
        if self._stt_engine:
            self._stt_engine.preload()
            print("[Main] Whisper model loading in background.")

        # Greeting
        self._greet()
        print("[STARK INDUSTRIES] All systems nominal.")

    def _greet(self) -> None:
        from datetime import datetime
        h = datetime.now().hour
        if h < 12:
            tod = "Good morning"
        elif h < 17:
            tod = "Good afternoon"
        else:
            tod = "Good evening"

        # Build greeting with context
        greeting_parts = [f"{tod}, sir. Systems online."]

        # Yesterday's context (cross-session continuity)
        if self._conv:
            try:
                yesterday_tail = self._conv.get_yesterday_tail()
                if yesterday_tail:
                    greeting_parts.append(
                        f"Yesterday we were on: {yesterday_tail[:70]}."
                        " Shall we continue, or something new on your mind?"
                    )
            except Exception:
                pass

        # Pending reminders from last session
        try:
            from tools.utils import check_pending_reminders as _chk_rem
            overdue = _chk_rem(speak_fn=None)   # reschedule; collect overdue messages
            if overdue:
                for msg in overdue[:2]:
                    greeting_parts.append(f"You had a reminder from earlier: {msg}.")
        except Exception:
            pass

        if len(greeting_parts) == 1:
            greeting_parts.append("I'm listening.")

        greeting = " ".join(greeting_parts)

        def _greet_then_listen():
            time.sleep(0.5)
            self._speak(greeting)
            # Wait for TTS to finish, then let room echo settle
            if self._tts_engine:
                safety = time.monotonic() + 30.0
                while self._tts_engine.is_speaking() and time.monotonic() < safety:
                    time.sleep(0.05)
            time.sleep(1.5)
            # If VAD pipeline is running it already handles mic — skip boot listen
            if self._turn_detector is None:
                self._activate("boot")

        threading.Thread(target=_greet_then_listen, daemon=True).start()

    # ------------------------------------------------------------------ #
    #  War room                                                            #
    # ------------------------------------------------------------------ #

    def _show_war_room(self) -> None:
        if not _HAS_WAR_ROOM:
            self._speak("War room module not available, sir.")
            return
        try:
            if self._war_room is None:
                self._war_room = WarRoom(
                    memory=self._memory,
                    context_engine=self._context,
                    monitor=self._monitor,
                    conversation_engine=self._conv,
                )
            screens = QApplication.screens()
            self._war_room.show_on_screen(1 if len(screens) > 1 else 0)
            # Brief the user on what's displayed — wait for "Pulling up..." TTS to finish
            wr = self._war_room
            def _brief():
                # Wait for "Pulling up..." TTS to finish, then speak immediately
                if self._tts_engine:
                    deadline = time.monotonic() + 8.0
                    while self._tts_engine.is_speaking() and time.monotonic() < deadline:
                        time.sleep(0.1)
                time.sleep(0.2)
                try:
                    briefing = wr.get_briefing()
                    self._speak(briefing)
                except Exception as e:
                    print(f"[Main] War room briefing error: {e}")
            threading.Thread(target=_brief, daemon=True).start()
        except Exception as e:
            print(f"[Main] War room show error: {e}")
            self._speak("War room unavailable, sir.")

    def _hide_war_room(self) -> None:
        if self._war_room:
            self._war_room.hide()
            self._speak("War room dismissed, sir.")
        else:
            self._speak("War room is not active, sir.")

    def _quit(self) -> None:
        try:
            if self._clap:
                self._clap.stop()
            if self._wakeword:
                self._wakeword.stop()
            if self._monitor:
                self._monitor.stop()
            if self._tray:
                self._tray.stop()
            if self._context:
                self._context.stop()
            if self._emotion:
                self._emotion.stop()
            if self._initiator:
                self._initiator.stop()
            if self._meeting:
                self._meeting.stop()
            if self._browser:
                self._browser.stop()
            if self._war_room:
                self._war_room.close()
            if self._memory:
                summary = ""
                if self._brain and len(self._brain._history) >= 4:
                    try:
                        last_msgs = self._brain._history[-10:]
                        conv_text = "\n".join(
                            f"{m['role'].upper()}: {m.get('content','')[:120]}"
                            for m in last_msgs
                        )
                        summary = self._brain._router.quick_complete(
                            system="Summarize this session in ONE sentence starting with 'Today you'. Max 20 words.",
                            user=conv_text,
                            timeout=8,
                        ) or ""
                    except Exception:
                        pass
                self._memory.end_session(summary=summary)
            if self._tts_engine:
                self._tts_engine.stop_immediately()
        except Exception:
            pass
        try:
            self._signals.quit_app.emit()
        except Exception:
            os._exit(0)


# ──────────────────────────────────────────────────────────────────── #
#  Native extension pre-loader — prevents 0xC0000005 crash            #
# ──────────────────────────────────────────────────────────────────── #

def _preload_native_extensions() -> None:
    """Import every C-extension DLL on the main thread before any thread starts.

    Root cause of 0xC0000005 (ACCESS_VIOLATION) on Windows
    -------------------------------------------------------
    Windows DLL initialisation runs inside the loader lock (LdrpLoaderLock).
    If two threads try to load the same native DLL simultaneously, the loader
    lock serialises them — but CPython's GIL + COM apartment model means that
    while thread B is blocked on the lock, thread A's GC can fire and call
    __del__ on a comtypes COM proxy created on the main STA thread.  Calling
    Release() from a non-STA thread on an STA-bound COM object = immediate
    ACCESS_VIOLATION in ntdll.

    Second vector: ctranslate2 / OpenBLAS / MKL init code is NOT thread-safe.
    Loading it from a background thread while other C code runs on the main
    thread can corrupt internal threading state → crash.

    Fix: import every C extension HERE, on the main Qt thread, before pystray,
    sounddevice, ctranslate2, cv2, or onnxruntime are touched from any thread.
    Subsequent imports of the same module are a dict lookup — zero risk.
    """
    import gc

    _MODS = [
        "sounddevice", "soundfile", "soundcard",
        "PIL.Image", "PIL.ImageDraw", "PIL.GifImagePlugin",
        "cv2",
        "numpy", "torch", "torchaudio", "ctranslate2", "faster_whisper", "onnxruntime",
        "comtypes", "comtypes.client", "comtypes.automation", "pycaw.pycaw",
        "psutil", "pyautogui", "pyperclip",
    ]
    for mod in _MODS:
        try:
            __import__(mod)
        except Exception:
            pass

    gc.collect()
    gc.collect()
    try:
        gc.freeze()
    except AttributeError:
        pass

    # Force BLAS/OpenBLAS full initialization on the main thread NOW.
    # Merely importing numpy/torch doesn't trigger BLAS init — the first
    # matrix multiplication does.  If we skip this, the STT-Load thread's
    # first WhisperModel forward-pass triggers BLAS init concurrently with
    # TurnDetector's Silero VAD init → two BLAS inits racing → 0xC0000005.
    try:
        import numpy as _np
        _a = _np.random.rand(16, 16).astype(_np.float32)
        _ = _a @ _a
        del _a
    except Exception:
        pass
    try:
        import torch as _torch
        _t = _torch.rand(8, 8)
        _ = _t @ _t
        del _t
    except Exception:
        pass

    try:
        from audio import vad as _vad_mod
        _vad_mod.preload()
    except Exception:
        pass
    try:
        from audio.noise_pipeline import start_echo_gate as _seg
        _seg()
    except Exception:
        pass

    print("[Main] Native extensions pre-loaded.")


# ──────────────────────────────────────────────────────────────────── #
#  Entry point                                                          #
# ──────────────────────────────────────────────────────────────────── #

def main() -> None:
    minimized = "--minimized" in sys.argv

    print("=" * 56)
    print("  J.A.R.V.I.S v2.0 — Stark Industries" + (" (minimized)" if minimized else ""))
    print("=" * 56)

    _fix_app_volume_async()

    # Ensure Ollama is available in background — don't block startup if Groq key exists
    def _ollama_init_bg():
        host = "http://localhost:11434"
        print("[Main] Checking Ollama (fallback)…")
        if not _ollama_alive(host):
            _start_ollama()
        if _ollama_alive(host):
            print("[Main] Ollama is running.")
            _cfg = cfg_mod.load_config()
            _model = _cfg.get("ollama_model", "llama3.2")
            _groq_key = _cfg.get("groq_api_key", "")
            _ensure_model(host, _model, _groq_key)
        else:
            print("[Main] Ollama not found — Groq will be the only AI backend.")

    _early_cfg = cfg_mod.load_config()
    _has_groq = bool(_early_cfg.get("groq_api_key", "").strip()
                     or os.getenv("GROQ_API_KEY", "").strip())
    if _has_groq:
        # Groq is primary — Ollama check can happen in background
        threading.Thread(target=_ollama_init_bg, daemon=True, name="OllamaInit").start()
    else:
        # No cloud key — must wait for Ollama before starting
        _ollama_init_bg()

    # Load config and check for first run
    cfg = cfg_mod.load_config()

    # Must be set before QApplication is created
    os.environ.setdefault("QT_AUTO_SCREEN_SCALE_FACTOR", "1")
    os.environ.setdefault("QT_ENABLE_HIGHDPI_SCALING", "1")

    app = QApplication(sys.argv)

    # First-run wizard (needs TTS before brain is built)
    if not cfg.get("_first_run_complete"):
        tts = TTSEngine(cfg) if _HAS_TTS_ENGINE and TTSEngine else None
        time.sleep(0.5)
        cfg = _first_run_wizard(cfg, tts)
        if tts:
            tts.stop_immediately()

    print("[STARK INDUSTRIES] Phase 0 — Online.")
    print("[STARK INDUSTRIES] Phase 1 — Online.")
    print("[STARK INDUSTRIES] Phase 2 — Online.")
    print("[STARK INDUSTRIES] Phase 3 — Online.")
    print("[STARK INDUSTRIES] Phase 4 — Online.")
    print("[STARK INDUSTRIES] Phase 5 — Online.")
    print("[STARK INDUSTRIES] Phase 6 — Online.")

    JARVIS().run(minimized=minimized)


if __name__ == "__main__":
    main()
