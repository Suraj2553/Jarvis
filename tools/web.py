"""Web tools — news, search, weather. All free, no API keys.

Crash isolation
---------------
DuckDuckGo (ddgs / duckduckgo_search) uses lxml internally for HTML
parsing.  lxml is a C extension and can segfault the whole process on
certain non-ASCII or malformed HTML (e.g. cricket/sports pages with
Devanagari content).

All DDG calls are therefore routed through ddg_worker.py via subprocess.
If that child process crashes, only it dies — JARVIS keeps running.

News source priority
--------------------
  1. DuckDuckGo via subprocess  (JSON, crash-isolated)
  2. Google News RSS             (pure-Python regex, cannot crash)
"""

import json
import os
import re
import sys
from urllib.parse import quote

import requests

_WORKER = os.path.join(os.path.dirname(__file__), "ddg_worker.py")

_location_cache: "dict | None" = None


def _load_config() -> dict:
    path = os.path.join(os.environ.get("APPDATA", ""), "JARVIS", "config.json")
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _save_config(data: dict) -> None:
    path = os.path.join(os.environ.get("APPDATA", ""), "JARVIS", "config.json")
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        existing: dict = {}
        try:
            with open(path, "r", encoding="utf-8") as f:
                existing = json.load(f)
        except Exception:
            pass
        existing.update(data)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(existing, f, indent=2)
    except Exception:
        pass


def _get_current_location() -> "dict | None":
    """Detect user's location via IP geolocation. Cached for session lifetime.

    Returns dict with keys: city, lat, lon, country, region
    Checks saved config first; falls back to ip-api.com (free, no key).
    """
    global _location_cache
    if _location_cache is not None:
        return _location_cache

    config = _load_config()
    if config.get("detected_city") and config.get("detected_lat") is not None:
        _location_cache = {
            "city":    config["detected_city"],
            "lat":     config["detected_lat"],
            "lon":     config["detected_lon"],
            "country": config.get("detected_country", ""),
            "region":  config.get("detected_region", ""),
        }
        return _location_cache

    try:
        resp = requests.get("http://ip-api.com/json/", timeout=8)
        if resp.status_code == 200:
            data = resp.json()
            if data.get("status") == "success":
                _location_cache = {
                    "city":    data.get("city", ""),
                    "lat":     data.get("lat"),
                    "lon":     data.get("lon"),
                    "country": data.get("country", ""),
                    "region":  data.get("regionName", ""),
                }
                _save_config({
                    "detected_city":    _location_cache["city"],
                    "detected_lat":     _location_cache["lat"],
                    "detected_lon":     _location_cache["lon"],
                    "detected_country": _location_cache["country"],
                    "detected_region":  _location_cache["region"],
                })
                return _location_cache
    except Exception:
        pass

    return None


def get_location() -> str:
    """Return the user's detected location as a readable string."""
    loc = _get_current_location()
    if loc:
        parts = [loc["city"]]
        if loc.get("region") and loc["region"] != loc["city"]:
            parts.append(loc["region"])
        if loc.get("country"):
            parts.append(loc["country"])
        return ", ".join(p for p in parts if p)
    return "Location unavailable"


# ------------------------------------------------------------------ #
#  Subprocess DDG caller                                               #
# ------------------------------------------------------------------ #

def _ddg(mode: str, query: str, timeout: int = 12) -> list[dict]:
    """Run ddg_worker.py in a subprocess and return parsed JSON list.

    Returns [] on any failure — including segfault (non-zero exit).
    """
    import subprocess
    try:
        proc = subprocess.run(
            [sys.executable, _WORKER, mode, query],
            capture_output=True, text=True,
            encoding="utf-8", timeout=timeout,
        )
        if proc.returncode == 0 and proc.stdout.strip():
            return json.loads(proc.stdout.strip())
    except subprocess.TimeoutExpired:
        pass
    except Exception:
        pass
    return []


# ------------------------------------------------------------------ #
#  News                                                                #
# ------------------------------------------------------------------ #

def _parse_rss_titles(xml_bytes: bytes, n: int = 6) -> list[str]:
    """Extract up to n titles from Google News RSS using pure-Python regex."""
    try:
        text = xml_bytes.decode("utf-8", errors="replace")
    except Exception:
        return []

    pattern = re.compile(
        r"<item>.*?<title>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</title>"
        r".*?(?:<source[^>]*>(.*?)</source>)?.*?</item>",
        re.DOTALL,
    )
    results: list[str] = []
    for m in pattern.finditer(text):
        title  = (m.group(1) or "").strip()
        source = (m.group(2) or "").strip()
        title  = title.split(" - ")[0].strip()
        if "google news" in title.lower() or not title:
            continue
        results.append(title + (f" from {source}" if source else ""))
        if len(results) >= n:
            break
    return results


def get_news(topic: str = "world", query: str = None) -> str:
    if query is not None and topic == "world":
        topic = query
    # ── Primary: DDG via subprocess (crash-isolated) ──────────────── #
    ddg_results = _ddg("news", topic)
    if ddg_results:
        parts = [r.get("title", "") for r in ddg_results if r.get("title")]
        if parts:
            return f"Top news on {topic}: " + ". ".join(parts[:6]) + "."

    # ── Fallback: Google News RSS (pure-Python, cannot crash) ─────── #
    try:
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
            )
        }
        url = (
            f"https://news.google.com/rss/search"
            f"?q={quote(topic)}&hl=en-IN&gl=IN&ceid=IN:en"
        )
        resp = requests.get(url, headers=headers, timeout=10)
        if resp.status_code == 200:
            parts = _parse_rss_titles(resp.content, n=6)
            if parts:
                return f"Top news on {topic}: " + ". ".join(parts) + "."
    except Exception:
        pass

    return f"Could not fetch news on '{topic}' right now. Try again in a moment."


# ------------------------------------------------------------------ #
#  Web search                                                          #
# ------------------------------------------------------------------ #

def web_search(query: str) -> str:
    results = _ddg("search", query)
    if results:
        snippets = []
        for r in results:
            title = r.get("title", "")
            body  = r.get("body", "")[:150]
            snippets.append(f"{title}: {body}")
        return "Search results — " + " | ".join(snippets)

    # Fallback: Wikipedia summary for factual queries
    try:
        clean = re.sub(r"\b(today|latest|recent|now|current|live|2024|2025)\b", "", query, flags=re.I).strip()
        if clean:
            wiki_url = f"https://en.wikipedia.org/api/rest_v1/page/summary/{quote(clean.replace(' ', '_'))}"
            resp = requests.get(wiki_url, timeout=6)
            if resp.status_code == 200:
                extract = resp.json().get("extract", "")
                if extract and len(extract) > 40:
                    return f"From Wikipedia: {extract[:400]}"
    except Exception:
        pass

    # Fallback: Google News RSS for news-style queries
    news_kw = ("news", "schedule", "update", "result", "match", "latest", "today")
    if any(w in query.lower() for w in news_kw):
        news = get_news(query)
        if news.startswith("Top news"):
            return news

    return f"Search unavailable right now. Try asking more specifically or check back shortly."


# ------------------------------------------------------------------ #
#  Live cricket scores (Cricbuzz + ESPN fallback, no API key)         #
# ------------------------------------------------------------------ #

_SCORE_RE = re.compile(
    r"(\d{1,3}/\d{1,2})"           # runs/wickets  e.g. 145/3
    r"(?:\s*[\(\-]\s*(\d+\.?\d*)\s*(?:ov|overs?)\)?)?"  # (15.2 ov)
)
_NEED_RE = re.compile(r"need[s]?\s+(\d+)\s+(?:more\s+)?runs?", re.I)
_TARGET_RE = re.compile(r"target[:\s]+(\d+)", re.I)


def _pick_sport(query: str) -> str:
    q = query.lower()
    if any(w in q for w in ("nba", "basketball")):
        return "basketball"
    if any(w in q for w in ("tennis", "atp", "wta")):
        return "tennis"
    if any(w in q for w in (
        "football", "soccer", "premier league", "champions league",
        "fifa", "uefa", "laliga", "la liga",
    )):
        return "football"
    return "cricket"


def _format_sportscore_match(match: dict) -> str:
    home = str(match.get("home") or "Home").strip()
    away = str(match.get("away") or "Away").strip()
    home_score = str(match.get("home_score") or "-").strip()
    away_score = str(match.get("away_score") or "-").strip()
    status = str(match.get("status_text") or match.get("status") or "").strip()
    comp = str(match.get("competition") or "").strip()
    score = f"{home} {home_score}, {away} {away_score}"
    if status:
        score += f" ({status})"
    if comp:
        score += f" - {comp}"
    return score


def _fetch_sportscore(match_query: str) -> str | None:
    """Fetch live/recent scores from SportScore's no-key widget API."""
    try:
        sport = _pick_sport(match_query)
        params = {
            "sport": sport,
            "status": "live",
            "limit": 10,
            "src": "jarvis",
        }
        resp = requests.get(
            "https://sportscore.com/api/widget/matches/",
            params=params,
            timeout=10,
            headers={"User-Agent": "JARVIS/4.0"},
        )
        if resp.status_code != 200:
            return None
        matches = resp.json().get("matches", [])
        if not matches:
            return None

        terms = [
            w.lower()
            for w in re.findall(r"[A-Za-z0-9]+", match_query)
            if len(w) > 2 and w.lower() not in {
                "live", "score", "scores", "today", "match", "cricket", "football",
                "sports", "current", "right", "now",
            }
        ]

        def _rank(match: dict) -> tuple[int, int]:
            hay = " ".join(str(match.get(k, "")) for k in (
                "home", "away", "competition", "status", "status_text",
            )).lower()
            live = 1 if "live" in hay or "inning" in hay or match.get("status") == "live" else 0
            hits = sum(1 for term in terms if term in hay)
            return hits, live

        if terms:
            matches = sorted(matches, key=_rank, reverse=True)
            if _rank(matches[0])[0] == 0:
                matches = matches[:3]
            else:
                matches = matches[:1]
        else:
            matches = sorted(matches, key=_rank, reverse=True)[:3]

        formatted = [_format_sportscore_match(m) for m in matches if m]
        if formatted:
            return ". ".join(formatted)
    except Exception:
        return None
    return None


def _fetch_cricbuzz_live(match_query: str) -> str | None:
    """Try to scrape Cricbuzz mobile match list for a live score."""
    try:
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Linux; Android 12; Pixel 6) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124 Mobile Safari/537.36"
            ),
            "Accept-Language": "en-US,en;q=0.9",
        }
        resp = requests.get(
            "https://www.cricbuzz.com/cricket-match/live-scores",
            headers=headers, timeout=10,
        )
        if resp.status_code != 200:
            return None
        text = resp.text
        kw = [w.lower() for w in match_query.split() if len(w) > 2]
        blocks = re.split(r'<div[^>]*class="[^"]*cb-lv-main[^"]*"', text)
        for block in blocks[1:]:
            clean = re.sub(r"<[^>]+>", " ", block)
            clean = re.sub(r"&[a-z]+;", " ", clean)
            clean = re.sub(r"\s+", " ", clean).strip()
            low = clean.lower()
            if sum(1 for k in kw if k in low) >= min(2, len(kw)):
                score_hits = _SCORE_RE.findall(clean)
                if score_hits:
                    return clean[:400]
        return None
    except Exception:
        return None


def _parse_live_score_from_search(results: list[dict], query: str) -> str | None:
    """Extract live score nuggets from DDG search results."""
    score_lines = []
    for r in results:
        body = r.get("body", "") + " " + r.get("title", "")
        hits = _SCORE_RE.findall(body)
        if hits:
            need = _NEED_RE.search(body)
            target = _TARGET_RE.search(body)
            line = r.get("title", "").split("-")[0].strip()
            for score, overs in hits:
                part = score
                if overs:
                    part += f" ({overs} ov)"
                line += f" — {part}"
            if need:
                line += f", need {need.group(1)} more runs"
            elif target:
                line += f", target {target.group(1)}"
            score_lines.append(line.strip())
    return ". ".join(score_lines[:3]) if score_lines else None


def get_live_score(match_query: str) -> str:
    """Return live/current cricket score for the queried match."""
    sportscore = _fetch_sportscore(match_query)
    if sportscore:
        return f"Live score: {sportscore}"

    ql = match_query.lower()
    cricketish = any(w in ql for w in (
        "cricket", "ipl", "t20", "odi", "test", "wicket", "over",
        "runs", "india", "pakistan", "australia", "england",
    ))
    if cricketish:
        cb = _fetch_cricbuzz_live(match_query)
        if cb:
            cb = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", cb)).strip()
            return f"Live score update: {cb[:350]}"

    search_q = (
        f"{match_query} live score today cricbuzz"
        if cricketish else
        f"{match_query} live score today"
    )
    results = _ddg("search", search_q)
    parsed = _parse_live_score_from_search(results, match_query)
    if parsed:
        return f"Live score: {parsed}"

    if results:
        top = results[0]
        title = top.get("title", "")
        body  = top.get("body", "")[:200]
        return f"Match update — {title}: {body}"

    return (
        f"Could not fetch live score for '{match_query}'. "
        "The match may not be live right now, or try asking for news instead."
    )


# ------------------------------------------------------------------ #
#  Weather (Open-Meteo — free, no key)                                #
# ------------------------------------------------------------------ #

# Aliases: old/alternate Indian city names → Open-Meteo canonical name
_CITY_ALIASES = {
    "bangalore":  "bengaluru",
    "bombay":     "mumbai",
    "calcutta":   "kolkata",
    "madras":     "chennai",
    "poona":      "pune",
    "mysore":     "mysuru",
    "mangalore":  "mangaluru",
    "vizag":      "visakhapatnam",
    "cochin":     "kochi",
    "new delhi":  "delhi",
    "allahabad":  "prayagraj",
}

# Known Indian city names where we must bias geocoding towards India
_INDIA_CITIES = {
    "bengaluru", "mumbai", "delhi", "chennai", "hyderabad", "kolkata",
    "pune", "ahmedabad", "jaipur", "lucknow", "kanpur", "nagpur", "surat",
    "kochi", "coimbatore", "bhopal", "patna", "agra", "nashik", "vadodara",
    "ludhiana", "rajkot", "indore", "thane", "noida", "gurgaon", "gurugram",
    "chandigarh", "visakhapatnam", "bhubaneswar", "ranchi", "guwahati",
    "amritsar", "jodhpur", "mysuru", "mangaluru", "madurai", "varanasi",
    "prayagraj", "srinagar", "jammu", "shimla", "dehradun", "rishikesh",
}


def _geocode(city: str) -> dict | None:
    """Geocode a city name, preferring India for known Indian cities."""
    city_clean = city.strip().lower().rstrip(",. ")

    # Apply alias (bangalore → bengaluru, etc.)
    canonical = _CITY_ALIASES.get(city_clean, city_clean)
    prefer_india = canonical in _INDIA_CITIES

    def _fetch(name: str) -> list[dict]:
        try:
            return requests.get(
                f"https://geocoding-api.open-meteo.com/v1/search"
                f"?name={quote(name)}&count=5",
                timeout=10,
            ).json().get("results", [])
        except Exception:
            return []

    # 1. Try canonical/aliased name
    results = _fetch(canonical)
    if prefer_india:
        india = [r for r in results if r.get("country_code", "").upper() == "IN"]
        if india:
            return india[0]
        # 2. Retry with ", India" suffix for disambiguation
        results2 = _fetch(canonical + ", India")
        india2 = [r for r in results2 if r.get("country_code", "").upper() == "IN"]
        if india2:
            return india2[0]

    return results[0] if results else None


_WMO_CODES = {
    0: "clear sky", 1: "mainly clear", 2: "partly cloudy", 3: "overcast",
    45: "foggy", 48: "icy fog",
    51: "light drizzle", 53: "drizzle", 55: "heavy drizzle",
    61: "light rain", 63: "rain", 65: "heavy rain",
    71: "light snow", 73: "snow", 75: "heavy snow", 77: "snow grains",
    80: "light showers", 81: "showers", 82: "heavy showers",
    85: "snow showers", 86: "heavy snow showers",
    95: "thunderstorm", 96: "thunderstorm with hail", 99: "thunderstorm with heavy hail",
}


def get_weather_forecast(city: str = "", days: int = 3, **kwargs) -> str:
    """Multi-day weather forecast using Open-Meteo daily endpoint."""
    city = city or kwargs.get("location", "") or kwargs.get("query", "")
    from datetime import date as _date
    if not city:
        cur = _get_current_location()
        if cur:
            city = cur["city"]
        else:
            return "Could not detect your location. Please specify a city."
    try:
        loc = _geocode(city)
        if not loc:
            return f"Could not geocode '{city}'."
        lat, lon = loc["latitude"], loc["longitude"]
        name    = loc.get("name", city)
        days    = max(1, min(7, int(days)))

        w = requests.get(
            "https://api.open-meteo.com/v1/forecast"
            f"?latitude={lat}&longitude={lon}"
            "&daily=temperature_2m_max,temperature_2m_min,weathercode,precipitation_probability_mean"
            f"&forecast_days={days}&timezone=auto",
            timeout=10,
        ).json()

        daily   = w.get("daily", {})
        dates   = daily.get("time", [])
        maxtemps = daily.get("temperature_2m_max", [])
        mintemps = daily.get("temperature_2m_min", [])
        codes    = daily.get("weathercode", [])
        precips  = daily.get("precipitation_probability_mean", [])

        today = _date.today()
        parts = [f"Forecast for {name}"]
        for i in range(min(days, len(dates))):
            try:
                d = _date.fromisoformat(dates[i])
                delta = (d - today).days
                if delta == 0:
                    day_name = "Today"
                elif delta == 1:
                    day_name = "Tomorrow"
                else:
                    day_name = d.strftime("%A")
            except Exception:
                day_name = dates[i]

            desc  = _WMO_CODES.get(int(codes[i]) if codes else 0, "variable")
            hi    = f"{maxtemps[i]:.0f}" if maxtemps else "?"
            lo    = f"{mintemps[i]:.0f}" if mintemps else "?"
            rain  = f", {precips[i]:.0f}% rain" if precips else ""
            parts.append(f"{day_name}: {desc}, {hi}/{lo}°C{rain}")

        return ". ".join(parts) + "."
    except Exception as e:
        return f"Could not get forecast: {e}"


# ------------------------------------------------------------------ #
#  Stock / Finance (Yahoo Finance, no API key)                        #
# ------------------------------------------------------------------ #

_TICKER_ALIASES = {
    "sensex":  "^BSESN",
    "bse":     "^BSESN",
    "nifty":   "^NSEI",
    "nse":     "^NSEI",
    "bitcoin": "BTC-USD",
    "btc":     "BTC-USD",
    "ethereum":"ETH-USD",
    "eth":     "ETH-USD",
    "gold":    "GC=F",
    "silver":  "SI=F",
    "crude":   "CL=F",
    "oil":     "CL=F",
}


def get_stock_price(symbol: str) -> str:
    """Return current price and daily change for a ticker or alias."""
    symbol = symbol.strip()
    resolved = _TICKER_ALIASES.get(symbol.lower(), symbol.upper())
    try:
        url = (
            f"https://query1.finance.yahoo.com/v8/finance/chart/{quote(resolved)}"
            "?interval=1m&range=1d"
        )
        headers = {"User-Agent": "JARVIS/4.0"}
        resp = requests.get(url, headers=headers, timeout=10)
        if resp.status_code != 200:
            raise RuntimeError(f"HTTP {resp.status_code}")
        data = resp.json()
        meta = data["chart"]["result"][0]["meta"]
        price = meta.get("regularMarketPrice") or meta.get("previousClose")
        prev  = meta.get("previousClose") or meta.get("chartPreviousClose")
        currency = meta.get("currency", "")
        change_pct = ((price - prev) / prev * 100) if price and prev else None
        name = meta.get("longName") or meta.get("shortName") or resolved
        parts = [f"{name}: {price:.2f} {currency}"]
        if change_pct is not None:
            sign = "+" if change_pct >= 0 else ""
            parts.append(f"{sign}{change_pct:.2f}% today")
        return ", ".join(parts) + "."
    except Exception:
        # Fallback: web search
        return web_search(f"{symbol} stock price today")


# ------------------------------------------------------------------ #
#  Translation (Sarvam mayura:v1)                                     #
# ------------------------------------------------------------------ #

_LANG_CODES = {
    "hindi":     "hi-IN",
    "english":   "en-IN",
    "tamil":     "ta-IN",
    "telugu":    "te-IN",
    "kannada":   "kn-IN",
    "malayalam": "ml-IN",
    "bengali":   "bn-IN",
    "gujarati":  "gu-IN",
    "punjabi":   "pa-IN",
    "marathi":   "mr-IN",
    "odia":      "od-IN",
}


def translate_text(text: str, target_language: str) -> str:
    """Translate text to target_language using Sarvam mayura:v1."""
    target_code = _LANG_CODES.get(target_language.lower().strip(), "hi-IN")
    cfg = _load_config()
    key = cfg.get("sarvam_api_key", "")
    if not key:
        return "Sarvam API key not configured — translation unavailable."
    try:
        from audio.sarvam_client import get_client as _get_sarvam
        client = _get_sarvam(cfg)
        if client is None:
            return "Translation service unavailable."
        translated = client.translate(text, target_language_code=target_code)
        return translated or "Translation returned empty."
    except Exception as e:
        return f"Translation failed: {e}"


def get_weather(city: str = "", **kwargs) -> str:
    city = city or kwargs.get("location", "") or kwargs.get("query", "")
    if not city:
        cur = _get_current_location()
        if cur:
            city = cur["city"]
        else:
            return "Could not detect your location. Please specify a city."
    from tools.utils import _cached
    return _cached(f"weather:{city.lower()}", lambda: _get_weather_inner(city), 300.0)


def _get_weather_inner(city: str) -> str:
    try:
        loc = _geocode(city)
        if not loc:
            return f"Could not geocode '{city}'."
        lat, lon = loc["latitude"], loc["longitude"]
        name    = loc.get("name", city)
        country = loc.get("country", "")

        w = requests.get(
            "https://api.open-meteo.com/v1/forecast"
            f"?latitude={lat}&longitude={lon}"
            "&current_weather=true"
            "&hourly=relativehumidity_2m,precipitation_probability",
            timeout=10,
        ).json()

        cw   = w.get("current_weather", {})
        temp = cw.get("temperature", "?")
        wind = cw.get("windspeed", "?")
        code = int(cw.get("weathercode", 0))
        desc = _WMO_CODES.get(code, f"weather code {code}")

        hourly   = w.get("hourly", {})
        humidity = (hourly.get("relativehumidity_2m")       or [None])[0]
        precip   = (hourly.get("precipitation_probability") or [None])[0]

        parts = [f"In {name}, {country}: {desc}, {temp}°C, wind {wind} km/h"]
        if humidity is not None:
            parts.append(f"humidity {humidity}%")
        if precip is not None:
            parts.append(f"{precip}% chance of rain")
        return ", ".join(parts) + "."
    except Exception as e:
        return f"Could not get weather: {e}"
