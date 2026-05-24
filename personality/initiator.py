"""personality/initiator.py — Proactive conversation initiator  v3.0

Changes from v2
───────────────
• Now uses the shared ObservationLedger from monitor.py — both systems
  write to the same record so "it's midnight" can NEVER be said twice,
  even if the monitor timer and the initiator timer both fire at once.
• Removed redundant per-flag booleans (_said_morning, _said_late_night)
  that duplicated what the ledger already tracks.
• Gemini suggestion: time-of-day awareness — morning brief includes
  weather; late-night checks are single, calm, and don't repeat.
• Minimum interval raised to 30 min (was 25). Backoff is softer.
• _build_morning_message now tries Groq weather summary first.
"""

import random
import threading
import time
from datetime import datetime
from typing import Callable, Optional

from monitor import ObservationLedger   # shared singleton

# Module-level hook — set by main.py so tools can trigger a briefing on demand
_briefing_instance: "Optional[ProactiveInitiator]" = None


def get_morning_briefing() -> str:
    """Trigger a full live briefing: weather + headlines + markets + scores.
    Called as a JARVIS tool when the user asks for a briefing."""
    if _briefing_instance is not None:
        try:
            return _briefing_instance._build_comprehensive_briefing(datetime.now())
        except Exception as e:
            return f"Briefing unavailable: {e}"
    return "Briefing system not ready yet."

_MIN_INTERVAL         = 1800   # 30 min minimum between any proactive speech
_KEYBOARD_IDLE_NEEDED = 10.0   # keyboard must be idle this long before speaking
_PROACTIVE_VOL_FACTOR = 0.85   # 15% quieter than normal (used by TTS if supported)


class ProactiveInitiator:
    """Monitors context and fires proactive JARVIS observations at the right moments.

    All observations are gated through the shared ObservationLedger, so
    SystemMonitor and ProactiveInitiator never duplicate each other.
    """

    def __init__(
        self,
        speak_fn: Callable[[str], None],
        memory=None,
        context_engine=None,
        monitor=None,
        config: Optional[dict] = None,
    ):
        self._speak   = speak_fn
        self._memory  = memory
        self._context = context_engine
        self._monitor = monitor
        self._config  = config or {}

        self._last_proactive        = 0.0
        self._last_keyboard_activity = time.monotonic()
        self._ignored_count         = 0
        self._positive_responses    = 0
        self._session_start         = time.monotonic()
        self._active                = True
        self._paused                = False

        # Shared ledger — same singleton SystemMonitor uses
        self._ledger = ObservationLedger.get()

        self._thread = threading.Thread(
            target=self._loop, daemon=True, name="Initiator"
        )

    def start(self) -> None:
        global _briefing_instance
        _briefing_instance = self
        self._thread.start()
        threading.Thread(target=self._briefing_loop, daemon=True,
                         name="DailyBriefing").start()

    def stop(self) -> None:
        self._active = False

    def pause(self) -> None:
        """Suppress proactive speech (e.g. during presentations or interviews)."""
        self._paused = True

    def resume(self) -> None:
        """Resume proactive speech after a busy period ends."""
        self._paused = False

    def notify_keyboard_activity(self) -> None:
        self._last_keyboard_activity = time.monotonic()

    def notify_user_responded(self, positively: bool = True) -> None:
        if positively:
            self._positive_responses += 1
        else:
            self._ignored_count += 1

    # ── Main loop ──────────────────────────────────────────────────── #

    def _loop(self) -> None:
        time.sleep(90)           # let JARVIS finish booting before first check
        while self._active:
            if not self._paused:
                try:
                    self._check_triggers()
                except Exception as e:
                    print(f"[Initiator] Error: {e}")
            time.sleep(30)       # check every 30 s

    # ── Gate conditions ─────────────────────────────────────────────  #

    def _is_media_playing(self) -> bool:
        try:
            import psutil
            MEDIA = {
                "spotify.exe", "vlc.exe", "wmplayer.exe", "musicbee.exe",
                "foobar2000.exe", "winamp.exe", "groove.exe", "music.ui.exe",
            }
            for proc in psutil.process_iter(["name"]):
                if proc.info["name"] and proc.info["name"].lower() in MEDIA:
                    return True
        except Exception:
            pass
        return False

    def _can_speak(self) -> bool:
        now = time.monotonic()

        # Minimum interval + soft backoff for being ignored
        backoff = min(self._ignored_count * 180, 1800)   # max +30 min extra
        if now - self._last_proactive < _MIN_INTERVAL + backoff:
            return False

        # Don't interrupt typing
        if now - self._last_keyboard_activity < _KEYBOARD_IDLE_NEEDED:
            return False

        if not self._config.get("proactive_mode", True):
            return False

        if self._is_media_playing():
            return False

        return True

    def _fire(self, message: str, category: str = "generic") -> None:
        self._last_proactive = time.monotonic()
        self._ledger.record(category)
        print(f"[Initiator] Proactive ({category}): {message}")
        self._speak(message)

    # ── Trigger checks ──────────────────────────────────────────────  #

    def _check_triggers(self) -> None:
        if not self._can_speak():
            return

        now      = datetime.now()
        h        = now.hour
        elapsed  = (time.monotonic() - self._session_start) / 60

        # ── Morning greeting ─────────────────────────────────────── #
        if 6 <= h < 11 and self._ledger.can_say("morning"):
            msg = self._build_comprehensive_briefing(now)
            if msg:
                self._fire(msg, "morning")
                return

        # ── Late-night single check-in ───────────────────────────── #
        if h >= 23 and self._ledger.can_say("midnight") and elapsed > 30:
            self._fire(
                "It's late. The work will be here tomorrow — and so will I.",
                "midnight"
            )
            return

        # ── Battery warning (complement to monitor — only if monitor missed it) #
        if self._monitor and self._ledger.can_say("battery"):
            try:
                _, _, bat_pct, plugged = self._monitor.get_stats()
                if not plugged and bat_pct <= 18:
                    self._fire(
                        f"Power reserves at {bat_pct:.0f} percent, sir. "
                        "Worth plugging in before we lose momentum.",
                        "battery"
                    )
                    return
            except Exception:
                pass

        # ── Contextual check-ins (relationship level 2+, 2h+ session) #
        rel_level = 0
        if self._memory:
            try:
                rel_level = self._memory.get("relationship_level", 0)
            except Exception:
                pass

        if rel_level >= 2 and elapsed > 120 and self._ledger.can_say("session"):
            msg = self._build_contextual_checkin(elapsed)
            if msg:
                self._fire(msg, "session")
                return

    # ── Message builders ────────────────────────────────────────────  #

    def _build_comprehensive_briefing(self, now: datetime) -> str:
        """Parallel-fetch weather / news / markets / scores, then LLM-stitch."""
        from concurrent.futures import ThreadPoolExecutor, as_completed

        def _weather():
            try:
                from tools.web import get_weather
                return get_weather("")          # auto-detects location
            except Exception:
                return ""

        def _news():
            try:
                from tools.web import get_news
                raw = get_news("world")
                # Keep first 3 headlines only (truncate long dumps)
                lines = [l.strip() for l in raw.splitlines() if l.strip()][:4]
                return " | ".join(lines) if lines else raw[:400]
            except Exception:
                return ""

        def _market():
            try:
                from tools.web import get_stock_price
                from concurrent.futures import ThreadPoolExecutor, as_completed as _ac
                syms = ["^NSEI", "^BSESN", "BTC-USD"]
                with ThreadPoolExecutor(max_workers=3) as p:
                    futs = {p.submit(get_stock_price, s): s for s in syms}
                    parts = []
                    for fut in _ac(futs, timeout=5):
                        try:
                            r = fut.result()
                            if r and "error" not in r.lower():
                                parts.append(r)
                        except Exception:
                            pass
                return "  ".join(parts)
            except Exception:
                return ""

        def _scores():
            try:
                from tools.web import get_live_score
                r = get_live_score("cricket live")
                if r and "no live" not in r.lower() and "no match" not in r.lower():
                    return r
            except Exception:
                pass
            return ""

        # Run all four fetches simultaneously; hard cap at 9 s.
        # Do NOT use "with" — the context manager calls shutdown(wait=True)
        # which blocks until ALL threads finish even after the timeout fires.
        results: dict = {"weather": "", "news": "", "market": "", "scores": ""}
        tasks = {"weather": _weather, "news": _news, "market": _market, "scores": _scores}
        pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix="Briefing")
        fmap = {pool.submit(fn): key for key, fn in tasks.items()}
        try:
            for fut in as_completed(fmap, timeout=9):
                key = fmap[fut]
                try:
                    results[key] = fut.result() or ""
                except Exception:
                    pass
        except Exception:
            # Timeout — collect whatever finished, abandon the rest immediately
            for fut, key in fmap.items():
                if fut.done():
                    try:
                        results[key] = fut.result() or ""
                    except Exception:
                        pass
        finally:
            pool.shutdown(wait=False, cancel_futures=True)

        # ── Assemble context for LLM ──────────────────────────────── #
        tod = "morning" if now.hour < 12 else ("afternoon" if now.hour < 17 else "evening")
        sections = [f"Time of day: {tod}"]
        if results["weather"]:
            sections.append(f"Weather: {results['weather']}")
        if results["news"]:
            sections.append(f"Top headlines: {results['news']}")
        if results["market"]:
            sections.append(f"Markets: {results['market']}")
        if results["scores"]:
            sections.append(f"Live scores: {results['scores']}")

        raw_data = "\n".join(sections)

        # ── LLM stitch (Groq via requests — no SDK needed) ─────────── #
        import os, requests as _req
        groq_key = os.environ.get("GROQ_API_KEY", "")
        if groq_key:
            try:
                payload = {
                    "model": "llama-3.3-70b-versatile",
                    "messages": [
                        {"role": "system", "content": (
                            "You are JARVIS, a sharp AI assistant giving a morning briefing. "
                            "Weave the data below into a single fluid monologue under 90 seconds: "
                            "greet, weather, top 2-3 news items, market snapshot, live scores if any. "
                            "Sound confident and concise — no bullet points, no headers. "
                            "End with one sentence offering to help."
                        )},
                        {"role": "user", "content": raw_data},
                    ],
                    "max_tokens": 400,
                    "temperature": 0.7,
                    "stream": False,
                }
                resp = _req.post(
                    "https://api.groq.com/openai/v1/chat/completions",
                    headers={"Authorization": f"Bearer {groq_key}",
                             "Content-Type": "application/json"},
                    json=payload, timeout=8,
                )
                resp.raise_for_status()
                stitched = resp.json()["choices"][0]["message"]["content"].strip()
                if stitched:
                    return stitched
            except Exception as e:
                print(f"[Initiator] LLM stitch failed: {e}")

        # ── Fallback: concatenate ─────────────────────────────────── #
        parts = [f"Good {tod}, sir."]
        if results["weather"]:
            parts.append(results["weather"])
        if results["news"]:
            parts.append(f"Headlines: {results['news'][:250]}.")
        if results["market"]:
            parts.append(f"Markets: {results['market']}.")
        if results["scores"]:
            parts.append(results["scores"])
        parts.append("Ready when you are.")
        return " ".join(parts)

    def _briefing_loop(self) -> None:
        """Daily briefing: fires once per day at the configured time."""
        _last_fired_date = None
        while self._active:
            try:
                cfg = self._config
                if cfg.get("daily_briefing_enabled"):
                    time_str = cfg.get("daily_briefing_time", "08:00")
                    now = datetime.now()
                    try:
                        h, m = (int(x) for x in time_str.split(":"))
                    except Exception:
                        h, m = 8, 0
                    if now.hour == h and now.minute == m:
                        today = now.date().isoformat()
                        if _last_fired_date != today:
                            _last_fired_date = today
                            msg = self._build_comprehensive_briefing(now)
                            self._speak(msg)
            except Exception as e:
                print(f"[DailyBriefing] Error: {e}")
            time.sleep(60)

    def _build_contextual_checkin(self, elapsed_minutes: float) -> str:
        candidates = [
            f"You have been at this for {elapsed_minutes:.0f} minutes. How is it going?",
            "Still with you, sir.",
            "Anything you need from me?",
        ]
        if self._context:
            try:
                activity = self._context.current_activity
                if activity:
                    candidates.insert(0, f"Still working on {activity}?")
            except Exception:
                pass
        return random.choice(candidates)
