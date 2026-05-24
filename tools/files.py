"""File operation tools — all deletions go through send2trash (Recycle Bin)."""

import os
import shutil
import subprocess

try:
    import send2trash
    _S2T = True
except ImportError:
    _S2T = False


def _x(path: str) -> str:
    """Expand environment variables and user home shortcut."""
    return os.path.expandvars(os.path.expanduser(path))


def find_file(name: str, search_path: str | None = None) -> str:
    base = _x(search_path) if search_path else os.path.expandvars("%USERPROFILE%")
    try:
        r = subprocess.run(
            ["where", "/r", base, name],
            capture_output=True, text=True, shell=True, timeout=30,
        )
        hits = [p.strip() for p in r.stdout.strip().splitlines() if p.strip()]
        if hits:
            return "Found: " + ", ".join(hits[:5])
        return f"'{name}' not found in {base}."
    except subprocess.TimeoutExpired:
        return "Search timed out — try a more specific path."
    except Exception as e:
        return f"Search failed: {e}"


def read_file(path: str) -> str:
    full = _x(path)
    if not os.path.isfile(full):
        return f"File not found: {full}"
    try:
        with open(full, "r", encoding="utf-8", errors="replace") as f:
            content = f.read(3000)
        note = " (truncated at 3000 chars)" if os.path.getsize(full) > 3000 else ""
        return content + note
    except Exception as e:
        return f"Could not read file: {e}"


def create_file(path: str, content: str = "") -> str:
    full = _x(path)
    parent = os.path.dirname(full)
    if parent:
        os.makedirs(parent, exist_ok=True)
    try:
        with open(full, "w", encoding="utf-8") as f:
            f.write(content)
        return f"File created: {full}"
    except Exception as e:
        return f"Could not create file: {e}"


def list_directory(path: str = ".") -> str:
    full = _x(path)
    if not os.path.isdir(full):
        return f"Directory not found: {full}"
    try:
        entries = sorted(os.listdir(full))[:50]
        lines = []
        for e in entries:
            ep = os.path.join(full, e)
            if os.path.isfile(ep):
                lines.append(f"{e}  ({os.path.getsize(ep):,} B)")
            else:
                lines.append(f"{e}/")
        return "\n".join(lines) if lines else "(empty directory)"
    except Exception as e:
        return f"Could not list directory: {e}"


def move_file(src: str, dst: str) -> str:
    try:
        shutil.move(_x(src), _x(dst))
        return f"Moved to {_x(dst)}."
    except Exception as e:
        return f"Could not move file: {e}"


def delete_file(path: str) -> str:
    full = _x(path)
    if not os.path.exists(full):
        return f"File not found: {full}"
    if not _S2T:
        return "send2trash is not installed — refusing to delete without Recycle Bin safety."
    try:
        send2trash.send2trash(full)
        return f"Sent to Recycle Bin: {full}"
    except Exception as e:
        return f"Could not delete: {e}"


def open_file(path: str) -> str:
    full = _x(path)
    if not os.path.exists(full):
        return f"File not found: {full}"
    try:
        os.startfile(full)
        return f"Opened {full}."
    except Exception as e:
        return f"Could not open file: {e}"
