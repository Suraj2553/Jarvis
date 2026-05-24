"""Windows system control tools — apps, keyboard, volume, screenshot, etc."""

import os
import subprocess
import webbrowser
import winreg

import psutil
import pyautogui
import pyperclip

# ── optional heavy imports ──────────────────────────────────────────── #
try:
    from ctypes import cast, POINTER
    from comtypes import CLSCTX_ALL
    from pycaw.pycaw import AudioUtilities, IAudioEndpointVolume
    _PYCAW = True
except Exception:
    _PYCAW = False

try:
    import pytesseract
    _TESSERACT = True
except ImportError:
    _TESSERACT = False


# ------------------------------------------------------------------ #
#  App management                                                      #
# ------------------------------------------------------------------ #

def open_app(name: str) -> str:
    # 1. Try shell 'start' — works for apps registered in PATH / shell
    try:
        subprocess.Popen(f'start "" "{name}"', shell=True)
        return f"Opening {name}."
    except Exception:
        pass

    # 2. Walk common install directories
    search_roots = [
        r"C:\Program Files",
        r"C:\Program Files (x86)",
        os.path.expandvars(r"%APPDATA%"),
        os.path.expandvars(r"%LOCALAPPDATA%"),
    ]
    needle = name.lower().replace(" ", "")
    for root in search_roots:
        if not os.path.isdir(root):
            continue
        for dirpath, _, files in os.walk(root):
            for fname in files:
                if not fname.lower().endswith(".exe"):
                    continue
                stem = os.path.splitext(fname)[0].lower().replace(" ", "")
                if needle in stem or stem in needle:
                    try:
                        subprocess.Popen([os.path.join(dirpath, fname)])
                        return f"Opening {fname}."
                    except Exception:
                        continue

    # 3. Registry App Paths
    reg_path = r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths"
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, reg_path) as key:
            idx = 0
            while True:
                try:
                    sub = winreg.EnumKey(key, idx)
                    stem = os.path.splitext(sub)[0].lower()
                    if needle in stem or stem in needle:
                        with winreg.OpenKey(key, sub) as sk:
                            exe_path, _ = winreg.QueryValueEx(sk, "")
                            subprocess.Popen([exe_path])
                            return f"Opening {sub}."
                    idx += 1
                except OSError:
                    break
    except Exception:
        pass

    return f"Could not find '{name}'. Try the full application name."


def close_app(name: str) -> str:
    exe = name if name.lower().endswith(".exe") else f"{name}.exe"
    r = subprocess.run(["taskkill", "/F", "/IM", exe], capture_output=True, text=True)
    if r.returncode == 0:
        return f"Closed {exe}."
    # Retry without extension
    r2 = subprocess.run(["taskkill", "/F", "/IM", name], capture_output=True, text=True)
    if r2.returncode == 0:
        return f"Closed {name}."
    return f"Could not close '{name}'. It may not be running."


def list_running_apps() -> str:
    names: set[str] = set()
    for proc in psutil.process_iter(["name"]):
        try:
            n = proc.info["name"]
            if n and n.lower().endswith(".exe"):
                names.add(n)
        except Exception:
            pass
    top = sorted(names)[:30]
    return "Running: " + ", ".join(top) if top else "No processes found."


# ------------------------------------------------------------------ #
#  Keyboard / mouse                                                    #
# ------------------------------------------------------------------ #

def type_text(text: str) -> str:
    try:
        text.encode("ascii")
        pyautogui.write(text, interval=0.03)
    except (UnicodeEncodeError, UnicodeDecodeError):
        pyperclip.copy(text)
        pyautogui.hotkey("ctrl", "v")
    return f"Typed: {text[:60]}"


def press_keys(combo: str) -> str:
    keys = [k.strip() for k in combo.lower().split("+")]
    pyautogui.hotkey(*keys)
    return f"Pressed {combo}."


def scroll(direction: str, amount: int = 3) -> str:
    delta = int(amount) if direction.lower() == "up" else -int(amount)
    pyautogui.scroll(delta)
    return f"Scrolled {direction} by {amount}."


# ------------------------------------------------------------------ #
#  Screen / clipboard                                                  #
# ------------------------------------------------------------------ #

def take_screenshot() -> str:
    desktop = os.path.expandvars(r"%USERPROFILE%\Desktop")
    path = os.path.join(desktop, "nova_screenshot.png")
    pyautogui.screenshot().save(path)
    return f"Screenshot saved to {path}"


def read_screen(config: dict | None = None) -> str:
    if not _TESSERACT:
        return "Tesseract OCR is not installed. See README for setup instructions."
    if config:
        pytesseract.pytesseract.tesseract_cmd = config.get(
            "tesseract_path", r"C:/Program Files/Tesseract-OCR/tesseract.exe"
        )
    img = pyautogui.screenshot()
    text = pytesseract.image_to_string(img)
    return (text[:3000] if text.strip() else "No readable text found on screen.")


def get_clipboard() -> str:
    t = pyperclip.paste()
    return t if t else "(clipboard is empty)"


def set_clipboard(text: str) -> str:
    pyperclip.copy(text)
    return f"Copied to clipboard."


# ------------------------------------------------------------------ #
#  Volume                                                              #
# ------------------------------------------------------------------ #

def set_volume(level: int) -> str:
    level = max(0, min(100, int(level)))
    if not _PYCAW:
        return "pycaw is not installed; volume control unavailable."
    try:
        devices = AudioUtilities.GetSpeakers()
        interface = devices.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None)
        vol = cast(interface, POINTER(IAudioEndpointVolume))
        vol.SetMasterVolumeLevelScalar(level / 100.0, None)
        return f"Volume set to {level}%."
    except Exception as e:
        return f"Could not set volume: {e}"


# ------------------------------------------------------------------ #
#  System info / power                                                 #
# ------------------------------------------------------------------ #

def get_battery() -> str:
    from tools.utils import _cached
    def _fetch():
        b = psutil.sensors_battery()
        if b is None:
            return "No battery found — this appears to be a desktop."
        status = "plugged in" if b.power_plugged else "on battery"
        return f"Battery is at {b.percent:.0f}% and {status}."
    return _cached("battery", _fetch, 15.0)


def get_system_info() -> str:
    from tools.utils import _cached
    def _fetch():
        cpu = psutil.cpu_percent(interval=1)
        ram = psutil.virtual_memory()
        disk = psutil.disk_usage("/")
        return (
            f"CPU: {cpu}% used. "
            f"RAM: {ram.used / 1e9:.1f} GB of {ram.total / 1e9:.1f} GB. "
            f"Disk C: {disk.used / 1e9:.1f} GB of {disk.total / 1e9:.1f} GB used."
        )
    return _cached("system_info", _fetch, 20.0)


def lock_screen() -> str:
    subprocess.run(["rundll32.exe", "user32.dll,LockWorkStation"])
    return "Screen locked."


def shutdown(minutes: int = 0) -> str:
    secs = max(0, int(minutes)) * 60
    subprocess.run(["shutdown", "/s", "/t", str(secs)])
    return f"Shutdown scheduled in {minutes} minute(s)." if minutes else "Shutting down now."


def get_wifi_networks() -> str:
    r = subprocess.run(["netsh", "wlan", "show", "networks"],
                       capture_output=True, text=True)
    if r.returncode != 0:
        return "Could not scan WiFi. WiFi may be disabled."
    ssids = []
    for line in r.stdout.splitlines():
        if "SSID" in line and "BSSID" not in line:
            parts = line.split(":", 1)
            if len(parts) == 2 and parts[1].strip():
                ssids.append(parts[1].strip())
    if ssids:
        return f"Found {len(ssids)} network(s): " + ", ".join(ssids[:10])
    return "No WiFi networks found."


# ------------------------------------------------------------------ #
#  Shell command                                                       #
# ------------------------------------------------------------------ #

_DESTRUCTIVE = ("del ", "rmdir", "format", "rd /s", "erase ", "rm -rf")


def run_command(cmd: str) -> str:
    if any(d in cmd.lower() for d in _DESTRUCTIVE):
        # Without a UI confirm hook we just warn rather than block
        print(f"[Tools] Warning: potentially destructive command: {cmd}")
    try:
        r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=30)
        out = (r.stdout.strip() or r.stderr.strip())[:1000]
        return out if out else "Command completed with no output."
    except subprocess.TimeoutExpired:
        return "Command timed out after 30 seconds."
    except Exception as e:
        return f"Command failed: {e}"


def open_url(url: str) -> str:
    webbrowser.open(url)
    return f"Opened {url} in default browser."


def read_document_camera(
    task: str = "ocr",
    image_path: str = "",
    config: dict | None = None,
) -> str:
    """Read/OCR a document using Sarvam Vision or local Tesseract.

    task: ocr | summarize | structured | translate
    image_path: file path, or empty to capture from camera.
    """
    config = config or {}

    # Capture from camera if no path given
    if not image_path:
        try:
            import cv2
            cap = cv2.VideoCapture(0)
            if not cap.isOpened():
                return "Camera not available."
            ret, frame = cap.read()
            cap.release()
            if not ret:
                return "Failed to capture camera frame."
            tmp = os.path.join(os.environ.get("TEMP", "."), "jarvis_doc_cap.jpg")
            cv2.imwrite(tmp, frame)
            image_path = tmp
        except ImportError:
            return "opencv-python not installed — provide an image_path or install cv2."
        except Exception as e:
            return f"Camera capture failed: {e}"

    # Try Sarvam Vision first (cloud, multilingual OCR)
    sarvam_key = config.get("sarvam_api_key", "").strip()
    if sarvam_key:
        try:
            from audio.sarvam_client import SarvamClient
            client = SarvamClient(sarvam_key, config)
            result = client.analyze_document(image_path, task=task)
            if result:
                return result
        except Exception as e:
            print(f"[DocReader] Sarvam Vision failed ({e}) — trying Tesseract")

    # Fallback: local Tesseract OCR
    if _TESSERACT:
        try:
            from PIL import Image
            img = Image.open(image_path)
            text = pytesseract.image_to_string(img)
            return text.strip() or "No text detected."
        except Exception as e:
            return f"Tesseract OCR failed: {e}"

    return "No OCR backend available. Set sarvam_api_key in config or install pytesseract."


# ------------------------------------------------------------------ #
#  Safe Python execution                                              #
# ------------------------------------------------------------------ #

_BLOCKED_PATTERNS = (
    "import os", "os.system", "os.popen",
    "import subprocess", "subprocess.",
    "open(", "__import__",
    "exec(", "compile(",
    "ctypes", "socket",
)


def run_python(code: str) -> str:
    """Execute a Python snippet in a subprocess and return its output."""
    code_lower = code.lower()
    for pat in _BLOCKED_PATTERNS:
        if pat in code_lower:
            return f"Blocked: '{pat}' is not permitted for safety."
    try:
        result = subprocess.run(
            [os.sys.executable, "-c", code],
            capture_output=True, text=True, timeout=8,
        )
        out = result.stdout.strip()
        err = result.stderr.strip()
        if out:
            return f"Output: {out[:600]}"
        if err:
            return f"Error: {err[:400]}"
        return "Executed with no output."
    except subprocess.TimeoutExpired:
        return "Execution timed out (8s limit)."
    except Exception as e:
        return f"Execution failed: {e}"


# ------------------------------------------------------------------ #
#  WhatsApp via pywhatkit                                             #
# ------------------------------------------------------------------ #

def send_whatsapp(contact: str, message: str) -> str:
    """Send a WhatsApp message via pywhatkit (requires WhatsApp Web in Chrome)."""
    import json as _json
    import os as _os
    try:
        import pywhatkit as _pwk
    except ImportError:
        return "pywhatkit not installed. Run: pip install pywhatkit"

    # Resolve contact name to phone number from contacts store
    _contacts_path = _os.path.join(_os.environ.get("APPDATA", ""), "JARVIS", "contacts.json")
    phone = contact
    try:
        with open(_contacts_path, "r", encoding="utf-8") as f:
            contacts = _json.load(f)
        if contact.lower() in contacts:
            phone = contacts[contact.lower()]["phone"]
    except Exception:
        pass

    # If not resolved and contains no digits, give a helpful error
    if not any(c.isdigit() for c in phone):
        return f"Contact '{contact}' not found. Say 'remember contact {contact} as +91xxxxxxxxxx' first."

    if not phone.startswith("+"):
        phone = "+" + phone.lstrip("+")

    try:
        _pwk.sendwhatmsg_instantly(phone, message, wait_time=12, tab_close=True)
        return f"WhatsApp message sent to {contact}."
    except Exception as e:
        return f"WhatsApp send failed: {e}"


# ------------------------------------------------------------------ #
#  Phone calls                                                         #
# ------------------------------------------------------------------ #

def initiate_call(contact: str, via: str = "phone") -> str:
    """Initiate a phone/video call via Windows Phone Link, Teams, or WhatsApp Web."""
    import subprocess as _sp
    import urllib.parse as _up

    via = (via or "phone").lower()

    if via == "teams":
        # Open Teams deep-link (works if Teams is installed)
        encoded = _up.quote(contact)
        try:
            _sp.Popen(["cmd", "/c", f"start ms-teams:https://teams.microsoft.com/l/call/0/0?users={encoded}"])
            return f"Opening Teams call with {contact}."
        except Exception as e:
            return f"Teams call failed: {e}"

    elif via == "whatsapp":
        # Look up contact phone number then open WhatsApp Web call link
        import json as _json, os as _os
        _contacts_path = _os.path.join(_os.environ.get("APPDATA", ""), "JARVIS", "contacts.json")
        phone = contact
        try:
            with open(_contacts_path, "r", encoding="utf-8") as f:
                contacts = _json.load(f)
            if contact.lower() in contacts:
                phone = contacts[contact.lower()]["phone"]
        except Exception:
            pass
        phone = phone.lstrip("+").replace(" ", "")
        try:
            _sp.Popen(["cmd", "/c", f"start https://web.whatsapp.com/send?phone={phone}&text="])
            return f"Opening WhatsApp for {contact}. You can initiate the call from WhatsApp Web."
        except Exception as e:
            return f"WhatsApp call failed: {e}"

    else:
        # Windows Phone Link (Your Phone / Link to Windows)
        try:
            _sp.Popen(["cmd", "/c", "start ms-yourphone://"])
            return (f"Opening Phone Link. To call {contact}, dial from the Phone Link app. "
                    "Make sure your phone is connected via Bluetooth.")
        except Exception as e:
            return f"Phone Link failed: {e}"
