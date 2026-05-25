"""Small Windows and logging helpers for the recording automation."""

from __future__ import annotations

import ctypes
import logging
import os
import random
import subprocess
import time
from datetime import datetime
from pathlib import Path
from typing import Iterable

from config import LOG_DIR, RECORDING_DIR


def ensure_dirs() -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    RECORDING_DIR.mkdir(parents=True, exist_ok=True)


def timestamp() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def setup_logging() -> Path:
    ensure_dirs()
    path = LOG_DIR / f"session_{timestamp()}.log"
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s.%(msecs)03d %(levelname)-8s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
        handlers=[
            logging.FileHandler(path, encoding="utf-8"),
            logging.StreamHandler(),
        ],
    )
    return path


def natural_pause(base: float = 0.0, jitter: tuple[float, float] = (0.0, 1.0)) -> None:
    time.sleep(max(0.0, base + random.uniform(*jitter)))


def minimize_all_windows() -> None:
    try:
        ctypes.windll.user32.keybd_event(0x5B, 0, 0, 0)
        ctypes.windll.user32.keybd_event(ord("D"), 0, 0, 0)
        ctypes.windll.user32.keybd_event(ord("D"), 0, 2, 0)
        ctypes.windll.user32.keybd_event(0x5B, 0, 2, 0)
        time.sleep(1.0)
    except Exception as exc:
        logging.getLogger(__name__).warning("Could not minimize windows: %s", exc)


def set_console_title(title: str) -> None:
    try:
        ctypes.windll.kernel32.SetConsoleTitleW(title)
    except Exception:
        pass


def hide_taskbar() -> None:
    try:
        hwnd = ctypes.windll.user32.FindWindowW("Shell_TrayWnd", None)
        if hwnd:
            ctypes.windll.user32.ShowWindow(hwnd, 0)
    except Exception as exc:
        logging.getLogger(__name__).warning("Could not hide taskbar: %s", exc)


def show_taskbar() -> None:
    try:
        hwnd = ctypes.windll.user32.FindWindowW("Shell_TrayWnd", None)
        if hwnd:
            ctypes.windll.user32.ShowWindow(hwnd, 5)
    except Exception:
        pass


def run_best_effort(cmd: Iterable[str], timeout: float = 5.0) -> None:
    try:
        subprocess.run(list(cmd), timeout=timeout, check=False, capture_output=True, text=True)
    except Exception as exc:
        logging.getLogger(__name__).debug("Best-effort command failed: %s", exc)


def disable_focus_assist_best_effort() -> None:
    # Windows does not expose a stable public CLI for Focus Assist on all builds.
    # This keeps the feature best-effort without mutating registry policy.
    os.environ["JARVIS_RECORDING_MODE"] = "1"


def typewriter(lines: list[str], delay: float = 0.025) -> None:
    for line in lines:
        for ch in line:
            print(ch, end="", flush=True)
            time.sleep(delay)
        print(flush=True)

