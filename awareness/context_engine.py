"""awareness/context_engine.py — Screen and activity context awareness.

Every 60 seconds, background thread:
1. Reads active window title
2. OCR active window (max 500 chars)
3. One-sentence LLM summary of what user is doing
4. Injects into every LLM system prompt

Also triggers proactive help when context warrants it.
"""

import threading
import time
from datetime import datetime
from typing import Callable, Optional

# ── Optional imports ──────────────────────────────────────────────── #
try:
    import pygetwindow as _gw
    _HAS_GW = True
except Exception:
    _gw = None
    _HAS_GW = False

try:
    import pytesseract as _tesseract
    _HAS_OCR = True
except Exception:
    _tesseract = None
    _HAS_OCR = False

try:
    from PIL import ImageGrab as _ImageGrab
    _HAS_PIL = True
except Exception:
    _ImageGrab = None
    _HAS_PIL = False


class ContextEngine:
    """Tracks what the user is doing every 60 seconds."""

    def __init__(
        self,
        router=None,
        speak_fn: Optional[Callable] = None,
        config: Optional[dict] = None,
    ):
        self._router = router
        self._speak = speak_fn
        self._config = config or {}

        self.current_activity = ""
        self.active_window = ""
        self.active_window_history: list[str] = []  # last 10 windows

        self._running = False
        self._last_scan = 0.0
        self._thread: Optional[threading.Thread] = None
        self._last_proactive = 0.0
        self._ocr_interval = self._config.get("screen_scan_interval", 60)

        # Track how many times same file/window opened today
        self._window_counts: dict[str, int] = {}

    def start(self) -> None:
        if not self._config.get("emotion_detection", True):
            return
        self._running = True
        self._thread = threading.Thread(
            target=self._loop, daemon=True, name="ContextEngine"
        )
        self._thread.start()

    def stop(self) -> None:
        self._running = False

    # ------------------------------------------------------------------ #
    #  Main loop                                                           #
    # ------------------------------------------------------------------ #

    def _loop(self) -> None:
        time.sleep(30)  # initial delay
        while self._running:
            try:
                self._scan()
            except Exception as e:
                print(f"[ContextEngine] Error: {e}")
            time.sleep(self._ocr_interval)

    def _scan(self) -> None:
        # Step 1: Active window title
        window_title = self._get_active_window()
        if window_title:
            self.active_window = window_title
            if window_title not in self.active_window_history:
                self.active_window_history.append(window_title)
                if len(self.active_window_history) > 10:
                    self.active_window_history.pop(0)
            # Count window opens for proactive triggers
            self._window_counts[window_title] = (
                self._window_counts.get(window_title, 0) + 1
            )

        # Step 2: OCR active window (max 500 chars)
        ocr_text = ""
        if _HAS_OCR and _HAS_PIL:
            try:
                ocr_text = self._ocr_active_window()[:500]
            except Exception:
                pass

        # Step 3: Build activity summary
        combined_text = f"Window: {window_title}\n{ocr_text}".strip()
        if combined_text and self._router:
            try:
                messages = [
                    {"role": "system",
                     "content": "Describe in one sentence what the user is currently doing based on the screen context. Be specific. No filler words."},
                    {"role": "user", "content": combined_text},
                ]
                summary = self._router.chat_sync(messages, max_tokens=50)
                if summary:
                    self.current_activity = summary
            except Exception:
                # Fallback: use window title
                self.current_activity = f"Using {window_title}" if window_title else ""

        # Step 4: Check for proactive triggers
        self._check_proactive_triggers(window_title, ocr_text)

    def _get_active_window(self) -> str:
        if _HAS_GW and _gw:
            try:
                active = _gw.getActiveWindow()
                if active:
                    return active.title or ""
            except Exception:
                pass
        # Fallback: Windows API via ctypes
        try:
            import ctypes
            hwnd = ctypes.windll.user32.GetForegroundWindow()
            length = ctypes.windll.user32.GetWindowTextLengthW(hwnd)
            buf = ctypes.create_unicode_buffer(length + 1)
            ctypes.windll.user32.GetWindowTextW(hwnd, buf, length + 1)
            return buf.value or ""
        except Exception:
            return ""

    def _ocr_active_window(self) -> str:
        if not (_HAS_PIL and _HAS_OCR):
            return ""
        try:
            # Capture active window region only
            import ctypes
            hwnd = ctypes.windll.user32.GetForegroundWindow()

            rect = ctypes.wintypes.RECT()
            ctypes.windll.user32.GetWindowRect(hwnd, ctypes.byref(rect))
            x, y, w, h = rect.left, rect.top, rect.right, rect.bottom

            # Limit capture area to reasonable size
            screenshot = _ImageGrab.grab(bbox=(x, y, min(w, x + 1920), min(h, y + 1080)))

            tess_path = self._config.get(
                "tesseract_path", "C:/Program Files/Tesseract-OCR/tesseract.exe"
            )
            if _tesseract:
                _tesseract.pytesseract.tesseract_cmd = tess_path
                text = _tesseract.image_to_string(screenshot, config="--psm 6")
                return text[:500]
        except Exception:
            pass
        return ""

    # ------------------------------------------------------------------ #
    #  Proactive help triggers                                             #
    # ------------------------------------------------------------------ #

    def _check_proactive_triggers(self, window: str, ocr_text: str) -> None:
        if not self._speak:
            return
        now = time.monotonic()
        if now - self._last_proactive < 1800:  # max once per 30 min from here
            return

        window_lower = window.lower()
        ocr_lower = ocr_text.lower()

        # VS Code open same file > 45 mins
        if "visual studio code" in window_lower or ".py" in window_lower:
            count = self._window_counts.get(window, 0)
            if count >= 3:
                self._last_proactive = now
                self._speak(f"Still on {window.split('-')[0].strip()}? Need a second pair of eyes?")
                return

        # Error visible on screen
        _ERROR_KEYWORDS = ("error", "exception", "traceback", "failed", "unhandled")
        if any(kw in ocr_lower for kw in _ERROR_KEYWORDS):
            self._last_proactive = now
            self._speak("Looks like an error on screen. Want me to look at it?")
            return

        # Document open a while (writing detected)
        _DOC_KEYWORDS = ("word", ".docx", "google docs", "notepad")
        if any(kw in window_lower for kw in _DOC_KEYWORDS):
            self._last_proactive = now
            self._speak("Writing going well? Want me to proofread when you're done?")
            return
