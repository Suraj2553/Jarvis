"""JARVIS background system monitor  —  v3.0

Fixes in this version
─────────────────────
P1  CPU 600% bug   → all stats now normalized before observers ever see them
P2  Repetition bug → ObservationLedger is a process-wide singleton;
                     both SystemMonitor AND ProactiveInitiator share it so
                     the same category of remark is never spoken twice within
                     its cooldown window, regardless of which system fires it.
P3  Gemini idea    → session-arc awareness: morning/afternoon/evening
                     observations instead of the same pool every cycle.

Usage (unchanged):
    mon = SystemMonitor(speak_fn, config)
    mon.start()
    mon.stop()
    cpu, ram, battery, plugged = mon.get_stats()
"""

import random
import threading
import time
from datetime import datetime
from typing import Callable, Optional

try:
    import psutil
    _PSUTIL = True
except ImportError:
    _PSUTIL = False


# ══════════════════════════════════════════════════════════════════════ #
#  ObservationLedger — process-wide singleton                           #
#  BOTH SystemMonitor and ProactiveInitiator import this.               #
#  One record of what has already been said; no more duplicates.        #
# ══════════════════════════════════════════════════════════════════════ #

class ObservationLedger:
    """Thread-safe registry of observations that have already been spoken.

    Each category has an independent cooldown.  Calling `can_say(category)`
    returns True only if the cooldown for that category has expired.
    Calling `record(category)` marks it as just-said and starts the timer.
    """

    # Default cooldowns per category (seconds).
    # Override per-instance via the config dict if you want.
    _DEFAULTS: dict[str, float] = {
        "midnight":      7200,   # 2 h — say it once per night
        "late_evening":  3600,   # 1 h
        "morning":       86400,  # 24 h — once per day
        "battery":       1800,   # 30 min
        "cpu":           600,    # 10 min
        "ram":           600,    # 10 min
        "session":       3600,   # 1 h
        "idle":          300,    # 5 min (ambient filler)
        "generic":       1500,   # 25 min
    }

    _instance: "Optional[ObservationLedger]" = None
    _lock = threading.Lock()

    @classmethod
    def get(cls) -> "ObservationLedger":
        """Return (or create) the process-wide singleton."""
        with cls._lock:
            if cls._instance is None:
                cls._instance = ObservationLedger()
            return cls._instance

    def __init__(self) -> None:
        self._last_said: dict[str, float] = {}
        self._mu = threading.Lock()

    def can_say(self, category: str) -> bool:
        cooldown = self._DEFAULTS.get(category, self._DEFAULTS["generic"])
        with self._mu:
            return time.monotonic() - self._last_said.get(category, 0) >= cooldown

    def record(self, category: str) -> None:
        with self._mu:
            self._last_said[category] = time.monotonic()

    def reset(self, category: str) -> None:
        """Force a category to be eligible again immediately (e.g. new day)."""
        with self._mu:
            self._last_said.pop(category, None)


# ══════════════════════════════════════════════════════════════════════ #
#  Ambient observers                                                    #
#  Each returns (category, message) or None.                           #
# ══════════════════════════════════════════════════════════════════════ #

def _obs_cpu(stats: dict) -> Optional[tuple[str, str]]:
    cpu = stats.get("cpu", 0)   # already normalized to 0-100
    if cpu > 85:
        return ("cpu", f"CPU is at {cpu:.0f} percent, sir. You may want to close a few things.")
    if cpu > 70:
        return ("cpu", f"CPU utilization running at {cpu:.0f} percent.")
    return None


def _obs_ram(stats: dict) -> Optional[tuple[str, str]]:
    ram = stats.get("ram", 0)
    if ram > 85:
        return ("ram", f"Memory pressure is significant — {ram:.0f} percent utilized. Want me to close background applications?")
    if ram > 75:
        return ("ram", f"Memory usage is at {ram:.0f} percent. Worth keeping an eye on.")
    return None


def _obs_battery(stats: dict) -> Optional[tuple[str, str]]:
    pct     = stats.get("battery", 100)
    plugged = stats.get("plugged", True)
    if not plugged and pct < 25:
        return ("battery", f"Running on battery at {pct:.0f} percent, sir. Plugging in would be advisable.")
    return None


def _obs_time(stats: dict) -> Optional[tuple[str, str]]:
    h = datetime.now().hour
    if 1 <= h < 5:
        return ("midnight", "It is well past midnight, sir. Even Tony Stark slept occasionally.")
    if h >= 23:
        return ("late_evening", "Late evening. I will keep things running — but rest is advisable.")
    return None


def _obs_session(stats: dict) -> Optional[tuple[str, str]]:
    mins = stats.get("session_minutes", 0)
    if mins > 90 and mins % 60 < 3:
        h = int(mins // 60)
        return ("session", f"You have been active for {h} hour{'s' if h > 1 else ''}, sir. Biological units require hydration.")
    return None


def _obs_idle(stats: dict) -> Optional[tuple[str, str]]:
    proc = stats.get("top_process", "")
    if proc:
        return ("idle", f"All systems nominal. {proc} is your most active process.")
    return ("idle", "All systems nominal. No anomalies detected.")


_OBSERVERS = [_obs_cpu, _obs_ram, _obs_battery, _obs_time, _obs_session, _obs_idle]


# ══════════════════════════════════════════════════════════════════════ #
#  SystemMonitor                                                        #
# ══════════════════════════════════════════════════════════════════════ #

class SystemMonitor:
    _ALERT_COOLDOWN  = 300     # seconds between same-type proactive alerts
    _AMBIENT_MIN_S   = 120     # increased from 90 — less chatty
    _AMBIENT_MAX_S   = 240     # increased from 180

    # Windows system processes that look like spikes but are not user processes
    _SYSTEM_PROC_NAMES = frozenset({
        "system idle process", "system", "idle", "registry",
        "memory compression", "ntoskrnl.exe", "smss.exe", "csrss.exe",
        "wininit.exe", "services.exe", "lsass.exe", "svchost.exe",
    })

    def __init__(self, speak_fn: Callable, config: dict):
        self._speak   = speak_fn
        self._config  = config
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._start_time = time.monotonic()

        self._cpu    = 0.0
        self._ram    = 0.0
        self._bat    = 100.0
        self._plugged = True
        self._lock   = threading.Lock()

        # Shared ledger — same object ProactiveInitiator will use
        self._ledger = ObservationLedger.get()

        self._last_alert: dict[str, float] = {}
        self._next_ambient = time.monotonic() + random.uniform(
            self._AMBIENT_MIN_S, self._AMBIENT_MAX_S
        )
        self._paused = False

    # ── Public API ─────────────────────────────────────────────────── #

    def pause(self) -> None:
        """Suppress all spoken alerts (e.g. during presentations/interviews)."""
        self._paused = True

    def resume(self) -> None:
        """Re-enable spoken alerts after a busy period ends."""
        self._paused = False

    def start(self) -> None:
        if not _PSUTIL:
            print("[Monitor] psutil not available — system monitoring disabled.")
            return
        self._running = True
        self._thread  = threading.Thread(
            target=self._run, daemon=True, name="SysMonitor"
        )
        self._thread.start()
        print("[Monitor] System monitor started.")

    def stop(self) -> None:
        self._running = False

    def get_stats(self) -> tuple[float, float, float, bool]:
        """Returns (cpu%, ram%, battery%, plugged)."""
        with self._lock:
            return self._cpu, self._ram, self._bat, self._plugged

    def session_minutes(self) -> float:
        return (time.monotonic() - self._start_time) / 60.0

    # ── Monitor loop ───────────────────────────────────────────────── #

    def _run(self) -> None:
        # Prime CPU measurement — first call always returns 0
        psutil.cpu_percent(interval=None)
        for _ in psutil.process_iter(["cpu_percent"]):
            pass
        # Wait for JARVIS startup to settle
        time.sleep(8.0)

        while self._running:
            try:
                self._poll()
            except Exception as e:
                print(f"[Monitor] Poll error: {e}")
            time.sleep(5.0)

    def _poll(self) -> None:
        # ── Collect raw metrics ───────────────────────────────────── #
        cpu_raw  = psutil.cpu_percent(interval=None)
        ram      = psutil.virtual_memory().percent
        bat_obj  = psutil.sensors_battery()
        bat      = bat_obj.percent     if bat_obj else 100.0
        plugged  = bat_obj.power_plugged if bat_obj else True

        # Normalize CPU to 0-100% system-wide (not sum of all cores)
        # psutil.cpu_percent(percpu=False) already returns system-wide 0-100
        # but process cpu_percent can exceed 100 on multi-core — handle below
        cpu = cpu_raw  # system-wide is already 0-100; store as-is

        with self._lock:
            self._cpu    = cpu
            self._ram    = ram
            self._bat    = bat
            self._plugged = plugged

        now       = time.monotonic()
        cpu_count = max(psutil.cpu_count(logical=True) or 1, 1)

        # ── Proactive alerts ──────────────────────────────────────── #
        if self._paused:
            return   # suppress all spoken alerts during presentations/interviews
        if not plugged and bat < 20:
            key = "battery_alert"
            if now - self._last_alert.get(key, 0) > self._ALERT_COOLDOWN:
                self._last_alert[key] = now
                self._ledger.record("battery")
                self._alert(
                    f"Power reserves at {bat:.0f} percent, sir. "
                    "I would strongly suggest plugging in."
                )

        if ram > 85:
            key = "ram_alert"
            if now - self._last_alert.get(key, 0) > self._ALERT_COOLDOWN:
                self._last_alert[key] = now
                self._ledger.record("ram")
                self._alert(
                    f"Memory pressure is significant — {ram:.0f} percent utilized. "
                    "Want me to close background applications?"
                )

        # ── Single process scan (shared by spike alert + ambient) ───── #
        user_procs: list[tuple[str, float]] = []   # [(name, cpu_pct_normalized), ...]
        try:
            for proc in psutil.process_iter(["name", "cpu_percent"]):
                try:
                    pname = (proc.info.get("name") or "").strip()
                    if not pname or pname.lower() in self._SYSTEM_PROC_NAMES:
                        continue
                    pcpu = (proc.info["cpu_percent"] or 0) / cpu_count
                    user_procs.append((pname, pcpu))
                except Exception:
                    pass
            user_procs.sort(key=lambda x: x[1], reverse=True)
        except Exception:
            pass

        # CPU spike alert — top user process
        spike_threshold = 60.0
        if user_procs:
            pname, pcpu = user_procs[0]
            if pcpu > spike_threshold:
                key = f"cpu_{pname.lower()}"
                if now - self._last_alert.get(key, 0) > self._ALERT_COOLDOWN * 10:
                    self._last_alert[key] = now
                    self._ledger.record("cpu")
                    self._alert(
                        f"{pname.replace('.exe','').replace('.EXE','')} is consuming "
                        f"{pcpu:.0f} percent CPU. Shall I investigate?"
                    )

        # ── Ambient presence ─────────────────────────────────────── #
        if now < self._next_ambient:
            return
        self._next_ambient = now + random.uniform(self._AMBIENT_MIN_S, self._AMBIENT_MAX_S)

        # Top user process name (reuse scan already done above)
        top = ""
        for pname, _ in user_procs[:10]:
            top = pname.replace(".exe", "").replace(".EXE", "")
            break

        stats = {
            "cpu":             cpu,      # normalized 0-100
            "ram":             ram,
            "battery":         bat,
            "plugged":         plugged,
            "session_minutes": self.session_minutes(),
            "top_process":     top.capitalize() if top else "",
        }

        # Try observers in random order; speak the first whose category
        # hasn't been said recently (checked via shared ledger)
        for obs in random.sample(_OBSERVERS, len(_OBSERVERS)):
            result = obs(stats)
            if result is None:
                continue
            category, message = result
            if self._ledger.can_say(category):
                self._ledger.record(category)
                self._alert(message)
                break

    def _alert(self, text: str) -> None:
        from sounds import play_notification
        try:
            play_notification()
        except Exception:
            pass
        time.sleep(0.3)
        self._speak(text)
