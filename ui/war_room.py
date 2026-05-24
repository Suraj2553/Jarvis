"""ui/war_room.py — JARVIS War Room  v4.0  (Bloomberg-style)

Layout
──────
  TOP BAR (48px)   : clock  /  title  /  [X]
  LEFT   (60%)     : Leaflet world map (QWebEngineView)
  RIGHT  (40%)     : TOP 68% — news card feed (single column)
                   : BOT 32% — markets + weather data panel
  TICKER (32px)    : scrolling headline ticker
"""

import json
import math
import os
import threading
import time
from datetime import datetime
from typing import Optional
from xml.etree import ElementTree as ET

import requests as _rq
from PyQt6.QtCore import Qt, QTimer, QRectF, QPointF
from PyQt6.QtGui import (
    QBrush, QColor, QPainter, QPen, QFont, QKeyEvent, QLinearGradient,
    QPolygonF, QRadialGradient,
)
from PyQt6.QtWidgets import QApplication, QWidget

# ── World map polygons (Natural Earth 110m, loaded once at import) ─────── #
def _load_world_polygons() -> list:
    """Load country polygons from world_map.geojson → list of rings (list of (lon,lat))."""
    _base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    path  = os.path.join(_base, "world_map.geojson")
    rings: list = []
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        for feat in data.get("features", []):
            geom = feat.get("geometry", {})
            gtype = geom.get("type", "")
            coords = geom.get("coordinates", [])
            if gtype == "Polygon":
                rings.append(coords[0])          # outer ring only
            elif gtype == "MultiPolygon":
                for poly in coords:
                    rings.append(poly[0])
    except Exception as e:
        print(f"[WarRoom] World map load error: {e}")
    return rings

_WORLD_RINGS = _load_world_polygons()
print(f"[WarRoom] World map: {len(_WORLD_RINGS)} country rings loaded.")

from ui import theme


# ── GDELT event categories → color ───────────────────────────────────── #
_GDELT_COLORS = {
    "KILL":     "#FF3B3B",
    "WOUND":    "#FF6B35",
    "ARREST":   "#FFB830",
    "PROTEST":  "#FFD700",
    "DISASTER": "#FF8C00",
    "DEFAULT":  "#00D4FF",
}

# ── Source→category colour map for news cards ─────────────────────────── #
_SRC_COLORS = {
    "BBC":       "#BB1919",
    "Reuters":   "#FF7700",
    "Bloomberg": "#1769FF",
    "CNN":       "#CC0000",
    "AP":        "#004080",
}

def _src_color(source: str) -> str:
    for k, v in _SRC_COLORS.items():
        if k.lower() in source.lower():
            return v
    return "#00D4FF"   # default arc-blue


class WarRoom(QWidget):
    """Full-screen JARVIS War Room — Bloomberg-style v4.0."""

    _NEWS_FETCH_INTERVAL    = 600
    _GDELT_FETCH_INTERVAL   = 300
    _MARKET_FETCH_INTERVAL  = 60
    _WEATHER_FETCH_INTERVAL = 1800
    _SPORTS_FETCH_INTERVAL  = 90
    _NEWS_SCROLL_SPEED      = 1

    _MARKET_TICKERS = [
        ("^BSESN",   "SENSEX"),
        ("^NSEI",    "NIFTY"),
        ("BTC-USD",  "BTC"),
        ("USDINR=X", "USD/INR"),
        ("^GSPC",    "S&P 500"),
        ("^IXIC",    "NASDAQ"),
    ]
    _WEATHER_CITIES = ["Delhi", "Mumbai", "New York", "London", "Tokyo"]

    def __init__(
        self,
        memory=None,
        context_engine=None,
        monitor=None,
        conversation_engine=None,
    ):
        super().__init__()
        self._memory  = memory
        self._context = context_engine
        self._monitor = monitor
        self._conv    = conversation_engine

        self._cpu     = 0.0
        self._ram     = 0.0
        self._bat     = 100.0
        self._plugged = True
        self._net_up  = True

        self._news_cards:   list[dict] = []
        self._gdelt_events: list[dict] = []
        self._markets:      list[dict] = []
        self._weather_data: list[dict] = []
        self._sports:       list[dict] = []
        self._conv_log:     list[tuple] = []

        self._news_scroll_x  = 0
        self._news_ticker    = "Fetching headlines…"
        self._news_panel_scroll_y = 0   # vertical scroll offset for news cards

        self._t0         = time.monotonic()
        self._blink      = True
        self._close_rect: Optional[tuple] = None
        self._map_scan_y = 0.0   # 0.0→1.0, scan line sweeps down the map

        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, False)
        self.setStyleSheet(f"background: {theme.VOID_BLACK};")
        self.setMouseTracking(True)
        self._position_on_screen()

        self._stats_timer = QTimer(self)
        self._stats_timer.timeout.connect(self._refresh_stats)
        self._stats_timer.start(2000)

        self._frame_timer = QTimer(self)
        self._frame_timer.timeout.connect(self._tick)
        self._frame_timer.start(16)

        self._news_timer = QTimer(self)
        self._news_timer.timeout.connect(self._fetch_news_async)
        self._news_timer.start(self._NEWS_FETCH_INTERVAL * 1000)

        self._gdelt_timer = QTimer(self)
        self._gdelt_timer.timeout.connect(self._fetch_gdelt_async)
        self._gdelt_timer.start(self._GDELT_FETCH_INTERVAL * 1000)

        self._market_timer = QTimer(self)
        self._market_timer.timeout.connect(self._fetch_markets_async)
        self._market_timer.start(self._MARKET_FETCH_INTERVAL * 1000)

        self._weather_timer = QTimer(self)
        self._weather_timer.timeout.connect(self._fetch_weather_async)
        self._weather_timer.start(self._WEATHER_FETCH_INTERVAL * 1000)

        self._sports_timer = QTimer(self)
        self._sports_timer.timeout.connect(self._fetch_sports_async)
        self._sports_timer.start(self._SPORTS_FETCH_INTERVAL * 1000)

        for fn in [
            self._fetch_news_async,
            self._fetch_gdelt_async,
            self._fetch_markets_async,
            self._fetch_weather_async,
            self._fetch_sports_async,
        ]:
            threading.Thread(target=fn, daemon=True).start()


    # ── Screen positioning ────────────────────────────────────────────── #

    def _position_on_screen(self) -> None:
        screens = QApplication.screens()
        geo = screens[1].geometry() if len(screens) > 1 else \
              QApplication.primaryScreen().geometry()
        self.setGeometry(geo)

    def show_on_screen(self, screen_index: int = 1) -> None:
        screens = QApplication.screens()
        idx = min(screen_index, len(screens) - 1)
        self.setGeometry(screens[idx].geometry())
        self.showFullScreen()

    # ── Data fetchers ─────────────────────────────────────────────────── #

    def _fetch_gdelt_async(self) -> None:
        def _fetch():
            events: list[dict] = []

            # 1. USGS earthquakes — always fast, no auth, real coordinates
            try:
                r = _rq.get(
                    "https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/4.5_week.geojson",
                    timeout=6, headers={"User-Agent": "JARVIS/4.0"}
                )
                if r.status_code == 200:
                    seen: set = set()
                    for feat in r.json().get("features", [])[:40]:
                        try:
                            coords = feat["geometry"]["coordinates"]
                            lon, lat = float(coords[0]), float(coords[1])
                            mag  = feat["properties"].get("mag", 0) or 0
                            name = feat["properties"].get("place", "Unknown") or "Unknown"
                            name = name.encode("ascii", "ignore").decode()[:80]
                            key  = (round(lat, 1), round(lon, 1))
                            if key in seen:
                                continue
                            seen.add(key)
                            events.append({
                                "lat": lat, "lon": lon,
                                "color": "#00BBFF",   # cyan for earthquakes (distinct from conflict orange/red)
                                "title": f"M{mag:.1f} — {name}",
                                "count": max(1, int((mag - 4) * 3)),
                            })
                        except Exception:
                            continue
                    print(f"[WarRoom] USGS: {len(events)} earthquakes loaded.")
            except Exception as e:
                print(f"[WarRoom] USGS error: {e}")

            # 2. GDELT geo search — conflict/protest (with cached fallback)
            gdelt = self._try_gdelt_geo()
            events.extend(gdelt)

            if events:
                self._gdelt_events = events   # map redraws on next frame tick
            elif self._gdelt_events:
                pass  # keep showing cached data on timeout

        threading.Thread(target=_fetch, daemon=True).start()

    def _try_gdelt_geo(self) -> list[dict]:
        """GDELT geo search for conflict & protest events."""
        _QUERIES = [
            ("conflict attack shooting",  "KILL"),
            ("protest demonstration riot", "PROTEST"),
        ]
        events: list[dict] = []
        seen: set = set()
        for query, theme_key in _QUERIES:
            try:
                url = (
                    "https://api.gdeltproject.org/api/v2/geo/geo"
                    f"?query={_rq.utils.quote(query)}"
                    "&mode=pointdata&MAXRECORDS=20&format=json"
                )
                r = _rq.get(url, timeout=10, headers={"User-Agent": "JARVIS/4.0"})
                if r.status_code != 200 or not r.text.strip():
                    continue
                for pt in r.json().get("features", [])[:20]:
                    try:
                        geo  = pt.get("geometry", {}).get("coordinates", [])
                        if len(geo) < 2:
                            continue
                        lon, lat = float(geo[0]), float(geo[1])
                        props = pt.get("properties", {})
                        name  = props.get("name", props.get("actor1name", "Unknown"))
                        key   = (round(lat, 1), round(lon, 1))
                        if key in seen:
                            continue
                        seen.add(key)
                        events.append({
                            "lat":   lat, "lon": lon,
                            "color": _GDELT_COLORS.get(theme_key, _GDELT_COLORS["DEFAULT"]),
                            "title": str(name)[:80],
                            "count": 1,
                        })
                    except Exception:
                        continue
            except Exception as e:
                print(f"[WarRoom] GDELT '{query}' error: {e}")
        return events

    def _fetch_news_async(self) -> None:
        def _fetch():
            cards = []
            try:
                feed = _rq.get(
                    "https://news.google.com/rss?hl=en-US&gl=US&ceid=US:en",
                    timeout=8, headers={"User-Agent": "JARVIS/4.0"}
                )
                root = ET.fromstring(feed.content)
                for item in root.findall(".//item")[:10]:
                    title  = (item.findtext("title") or "").strip()
                    source = (item.findtext("source") or "Google News").strip()
                    pub    = (item.findtext("pubDate") or "")[:16]
                    if title:
                        cards.append({
                            "title":  title[:120],
                            "source": source[:30],
                            "time":   pub,
                        })
            except Exception:
                pass

            if not cards:
                try:
                    from tools.web import get_news
                    raw = get_news("world")
                    if raw:
                        for h in raw.split("•")[:10]:
                            h = h.strip()
                            if h:
                                cards.append({"title": h[:120], "source": "", "time": ""})
                except Exception:
                    pass

            if cards:
                self._news_cards = cards
                self._news_ticker = "  •  ".join(c["title"] for c in cards) + "   "
        threading.Thread(target=_fetch, daemon=True).start()

    def _fetch_markets_async(self) -> None:
        def _fetch():
            results = []
            for ticker, label in self._MARKET_TICKERS:
                try:
                    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}"
                    r   = _rq.get(url, timeout=5,
                                  headers={"User-Agent": "Mozilla/5.0"})
                    if r.status_code != 200:
                        continue
                    meta  = r.json()["chart"]["result"][0]["meta"]
                    price = meta.get("regularMarketPrice", 0)
                    prev  = meta.get("chartPreviousClose", price)
                    chg   = ((price - prev) / prev * 100) if prev else 0
                    results.append({
                        "label":  label,
                        "price":  price,
                        "change": chg,
                    })
                except Exception:
                    continue
            if results:
                self._markets = results
        threading.Thread(target=_fetch, daemon=True).start()

    def _fetch_weather_async(self) -> None:
        def _fetch():
            _WX_CODES = {
                0: "Clear", 1: "Mostly clear", 2: "Partly cloudy",
                3: "Overcast", 45: "Foggy", 51: "Drizzle",
                61: "Rain", 71: "Snow", 80: "Showers", 95: "Storm",
            }
            results = []
            for city in self._WEATHER_CITIES:
                try:
                    geo = _rq.get(
                        "https://geocoding-api.open-meteo.com/v1/search",
                        params={"name": city, "count": 1, "format": "json"},
                        timeout=5
                    ).json().get("results", [])
                    if not geo:
                        continue
                    lat, lon = geo[0]["latitude"], geo[0]["longitude"]
                    wx = _rq.get(
                        "https://api.open-meteo.com/v1/forecast",
                        params={"latitude": lat, "longitude": lon,
                                "current": "temperature_2m,weathercode",
                                "timezone": "auto"},
                        timeout=5
                    ).json().get("current", {})
                    results.append({
                        "city": city,
                        "temp": wx.get("temperature_2m", "--"),
                        "desc": _WX_CODES.get(wx.get("weathercode", 0), "Unknown"),
                    })
                except Exception:
                    continue
            if results:
                self._weather_data = results
        threading.Thread(target=_fetch, daemon=True).start()

    def _fetch_sports_async(self) -> None:
        def _fetch():
            try:
                from tools.web import get_live_score
                queries = [
                    "India cricket live score",
                    "IPL live score",
                    "international cricket live score",
                ]
                cards = []
                seen = set()
                for q in queries:
                    raw = get_live_score(q)
                    if not raw or raw.startswith("Could not fetch"):
                        continue
                    text = raw.replace("Live score update:", "").replace("Live score:", "").strip()
                    if text in seen:
                        continue
                    seen.add(text)
                    label = "CRICKET"
                    if "ipl" in q.lower():
                        label = "IPL"
                    elif "india" in q.lower():
                        label = "INDIA"
                    cards.append({"league": label, "summary": text[:220]})
                if cards:
                    self._sports = cards[:3]
            except Exception as e:
                print(f"[WarRoom] Sports fetch error: {e}")
        threading.Thread(target=_fetch, daemon=True).start()

    # ── Stats refresh ─────────────────────────────────────────────────── #

    def _refresh_stats(self) -> None:
        try:
            import psutil
            self._cpu  = psutil.cpu_percent(interval=None)
            self._ram  = psutil.virtual_memory().percent
            bat        = psutil.sensors_battery()
            if bat:
                self._bat     = bat.percent
                self._plugged = bat.power_plugged
            self._net_up = psutil.net_io_counters() is not None
        except Exception:
            pass

    # ── Events ───────────────────────────────────────────────────────── #

    def wheelEvent(self, event) -> None:
        """Scroll the news panel with mouse wheel."""
        w = self.width()
        h = self.height()
        map_w = int(w * 0.60)
        news_h = int((h - 48 - 32) * 0.56)
        mx = event.position().x() if hasattr(event, "position") else event.pos().x()
        my = event.position().y() if hasattr(event, "position") else event.pos().y()
        if mx > map_w and my < 48 + news_h:
            delta = event.angleDelta().y()
            CARD_H = 65
            max_scroll = max(0, len(self._news_cards) * CARD_H - news_h + 30)
            self._news_panel_scroll_y = max(0, min(max_scroll,
                self._news_panel_scroll_y - delta // 3))
        else:
            super().wheelEvent(event)

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.key() in (Qt.Key.Key_Escape, Qt.Key.Key_Q, Qt.Key.Key_W):
            self.hide()
        else:
            super().keyPressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if self._close_rect:
            bx, by, bw, bh = self._close_rect
            mx = event.pos().x()
            my = event.pos().y()
            if bx <= mx <= bx + bw and by <= my <= by + bh:
                self.setCursor(Qt.CursorShape.PointingHandCursor)
            else:
                self.setCursor(Qt.CursorShape.ArrowCursor)
        super().mouseMoveEvent(event)

    def mouseDoubleClickEvent(self, event) -> None:
        self.hide()

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton and self._close_rect:
            bx, by, bw, bh = self._close_rect
            mx, my = event.pos().x(), event.pos().y()
            if bx <= mx <= bx + bw and by <= my <= by + bh:
                self.hide()
                return
        super().mousePressEvent(event)

    def _tick(self) -> None:
        self._news_scroll_x -= self._NEWS_SCROLL_SPEED
        approx_px = len(self._news_ticker) * 7
        if self._news_scroll_x < -approx_px:
            self._news_scroll_x = self.width()
        t = time.monotonic() - self._t0
        self._blink = (int(t * 2) % 2 == 0)
        self._map_scan_y = (self._map_scan_y + 0.0012) % 1.0
        self.update()

    # ── Paint ─────────────────────────────────────────────────────────── #

    def paintEvent(self, event) -> None:
        try:
            self._paint_safe(event)
        except Exception as exc:
            try:
                print(f"[WarRoom] Paint error (suppressed): {exc}")
            except Exception:
                pass

    def _paint_safe(self, event) -> None:
        w = self.width()
        h = self.height()
        if w < 100 or h < 100:
            return

        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.fillRect(0, 0, w, h, QColor(theme.VOID_BLACK))

        TOP_BAR_H    = 48
        TICKER_H     = 32
        content_h    = h - TOP_BAR_H - TICKER_H
        map_w        = int(w * 0.60)
        right_x      = map_w
        right_w      = w - map_w
        content_y    = TOP_BAR_H

        # Panels
        news_h   = int(content_h * 0.56)
        market_h = content_h - news_h

        self._draw_top_bar(p, w)
        self._draw_world_map(p, 0, content_y, map_w, content_h)

        self._draw_news_feed(p, right_x, content_y, right_w, news_h)
        self._draw_market_panel(p, right_x, content_y + news_h, right_w, market_h)

        # Vertical divider
        div_c = QColor(theme.ARC_BLUE)
        div_c.setAlpha(45)
        p.setPen(QPen(div_c, 1))
        p.drawLine(map_w, content_y, map_w, h - TICKER_H)

        self._draw_ticker(p, w, h - TICKER_H, TICKER_H)
        p.end()

    # ── Top bar ───────────────────────────────────────────────────────── #

    def _draw_top_bar(self, p: QPainter, w: int) -> None:
        # Gradient background — deep navy to almost black
        grad = QLinearGradient(0, 0, 0, 48)
        grad.setColorAt(0.0, QColor("#080E1C"))
        grad.setColorAt(1.0, QColor("#02040A"))
        p.fillRect(0, 0, w, 48, QBrush(grad))

        # Accent strip across top (rainbow-ish: red → amber → cyan)
        strip_grad = QLinearGradient(0, 0, w, 0)
        strip_grad.setColorAt(0.0, QColor("#FF3B3B"))
        strip_grad.setColorAt(0.3, QColor("#FF8C00"))
        strip_grad.setColorAt(0.6, QColor("#00D4FF"))
        strip_grad.setColorAt(1.0, QColor("#8B5CF6"))
        p.fillRect(0, 0, w, 3, QBrush(strip_grad))

        # Left title
        title_c = QColor(theme.ARC_BLUE)
        title_c.setAlpha(230 if self._blink else 130)
        p.setPen(title_c)
        f = QFont(theme.FONT_FALLBACK, 12)
        f.setWeight(QFont.Weight.Bold)
        f.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, 2)
        p.setFont(f)
        p.drawText(20, 32, "J.A.R.V.I.S  WAR ROOM")

        # Status dot
        dot_c = QColor("#00FF88") if self._blink else QColor("#10B981")
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(dot_c)
        p.drawEllipse(QPointF(200, 24), 4, 4)

        # Centre clock — large, bright
        p.setPen(QColor("#FFFFFF"))
        p.setFont(QFont(theme.FONT_FALLBACK, 22))
        p.drawText(
            QRectF(0, 5, w, 40),
            Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter,
            datetime.now().strftime("%H:%M:%S")
        )

        # Date right
        p.setPen(QColor("#6A88AA"))
        p.setFont(QFont(theme.FONT_FALLBACK, 9))
        p.drawText(
            QRectF(w - 340, 8, 280, 16),
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignTop,
            datetime.now().strftime("%A, %d %B %Y")
        )

        # [X] close button
        btn_size = 28
        btn_x    = w - btn_size - 8
        btn_y    = 10
        self._close_rect = (btn_x, btn_y, btn_size, btn_size)
        close_bg = QColor("#CC1111")
        close_bg.setAlpha(220)
        p.fillRect(btn_x, btn_y, btn_size, btn_size, close_bg)
        m = 8
        p.setPen(QPen(QColor("#FFFFFF"), 2))
        p.drawLine(btn_x + m, btn_y + m,
                   btn_x + btn_size - m, btn_y + btn_size - m)
        p.drawLine(btn_x + btn_size - m, btn_y + m,
                   btn_x + m, btn_y + btn_size - m)

        # Bottom border (glowing cyan)
        bc = QColor(theme.ARC_BLUE)
        bc.setAlpha(100)
        p.setPen(QPen(bc, 1))
        p.drawLine(0, 47, w, 47)

    # ── Map fallback ──────────────────────────────────────────────────── #

    # ── QPainter world map ────────────────────────────────────────────── #

    def _draw_world_map(
        self, p: QPainter, x: int, y: int, w: int, h: int
    ) -> None:
        """Equirectangular world map drawn entirely with QPainter."""
        # Ocean — deep navy, clearly distinct from land at all monitor brightness levels
        ocean_grad = QLinearGradient(x, y, x, y + h)
        ocean_grad.setColorAt(0.0, QColor("#040F1E"))
        ocean_grad.setColorAt(0.5, QColor("#030C1A"))
        ocean_grad.setColorAt(1.0, QColor("#040F1E"))
        p.fillRect(x, y, w, h, QBrush(ocean_grad))

        def _ll_to_xy(lon: float, lat: float) -> tuple[float, float]:
            px = x + (lon + 180.0) / 360.0 * w
            py = y + (90.0 - lat) / 180.0 * h
            return px, py

        # ── Country polygons — warm forest olive vs cold deep navy ──────── #
        # Land = rich olive/forest green.  Ocean = midnight navy.
        # Strong enough contrast to read at any monitor brightness.
        land_c   = QColor("#1E2B12")   # rich forest olive — warm, clearly NOT blue
        border_c = QColor("#507040")   # lighter military green border
        border_c.setAlpha(200)
        p.setPen(QPen(border_c, 0.8))
        p.setBrush(land_c)
        for ring in _WORLD_RINGS:
            if len(ring) < 3:
                continue
            pts = []
            for coord in ring:
                try:
                    px, py = _ll_to_xy(float(coord[0]), float(coord[1]))
                    pts.append(QPointF(px, py))
                except Exception:
                    continue
            if len(pts) >= 3:
                p.drawPolygon(QPolygonF(pts))

        # ── Grid lines ───────────────────────────────────────────────── #
        grid_c = QColor("#1488CC")
        grid_c.setAlpha(55)
        p.setPen(QPen(grid_c, 0.6))
        p.setBrush(Qt.BrushStyle.NoBrush)
        for lon in range(-180, 181, 30):
            ax, ay = _ll_to_xy(lon, 90)
            bx, by = _ll_to_xy(lon, -90)
            p.drawLine(QPointF(ax, ay), QPointF(bx, by))
        for lat in range(-60, 61, 30):
            ax, ay = _ll_to_xy(-180, lat)
            bx, by = _ll_to_xy(180, lat)
            p.drawLine(QPointF(ax, ay), QPointF(bx, by))
        # Equator — glowing teal line
        eq_c = QColor("#00D4FF")
        eq_c.setAlpha(100)
        p.setPen(QPen(eq_c, 1.5))
        ax, ay = _ll_to_xy(-180, 0)
        bx, by = _ll_to_xy(180, 0)
        p.drawLine(QPointF(ax, ay), QPointF(bx, by))
        # Tropics — subtle amber lines
        tr_c = QColor("#F59E0B")
        tr_c.setAlpha(40)
        p.setPen(QPen(tr_c, 0.6))
        for lat in (23.5, -23.5):
            ax, ay = _ll_to_xy(-180, lat)
            bx, by = _ll_to_xy(180, lat)
            p.drawLine(QPointF(ax, ay), QPointF(bx, by))

        # ── GDELT event markers ──────────────────────────────────────── #
        events = self._gdelt_events
        if events:
            max_cnt = max(int(ev.get("count", 1)) for ev in events) or 1
            for ev in events[:80]:
                try:
                    ex, ey = _ll_to_xy(float(ev["lon"]), float(ev["lat"]))
                    weight = max(0.3, min(1.0, int(ev.get("count", 1)) / max_cnt))
                    base_r = 4 + weight * 10

                    col = QColor(ev.get("color", "#00D4FF"))

                    # Outer glow rings — larger, brighter than before
                    for glow_r, glow_a in [(base_r * 5.0, 40), (base_r * 3.0, 65), (base_r * 1.6, 110)]:
                        gc = QColor(col)
                        gc.setAlpha(int(glow_a * weight))
                        p.setPen(Qt.PenStyle.NoPen)
                        p.setBrush(gc)
                        p.drawEllipse(QPointF(ex, ey), glow_r, glow_r)

                    # Bright centre dot
                    p.setBrush(col)
                    pen = QPen(col.lighter(180), 1.0)
                    p.setPen(pen)
                    p.drawEllipse(QPointF(ex, ey), base_r, base_r)

                    # White hot centre
                    p.setPen(Qt.PenStyle.NoPen)
                    p.setBrush(QColor(255, 255, 255, 200))
                    p.drawEllipse(QPointF(ex, ey), base_r * 0.35, base_r * 0.35)
                except Exception:
                    continue

        # ── Radar scan line ──────────────────────────────────────────── #
        scan_py = y + int(self._map_scan_y * h)
        scan_alpha = int(55 * math.sin(math.pi * self._map_scan_y))
        sc = QColor(theme.ARC_BLUE)
        sc.setAlpha(max(0, scan_alpha))
        p.setPen(QPen(sc, 1))
        p.drawLine(x, scan_py, x + w - 1, scan_py)
        sc_glow = QColor(theme.ARC_BLUE)
        sc_glow.setAlpha(max(0, scan_alpha // 3))
        p.setPen(QPen(sc_glow, 5))
        p.drawLine(x, scan_py, x + w - 1, scan_py)

        # ── Legend ───────────────────────────────────────────────────── #
        leg_x, leg_y = x + 10, y + h - 28
        leg_bg = QColor(0, 0, 0, 180)
        p.fillRect(leg_x - 6, leg_y - 16, 280, 26, leg_bg)
        p.setFont(QFont(theme.FONT_FALLBACK, 7))
        items = [("Conflict", "#FF3B3B"), ("Earthquake", "#00BBFF"),
                 ("Protest", "#FFD700"), ("Activity", "#00D4FF")]
        lx = leg_x
        for label, color in items:
            c = QColor(color)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(c)
            p.drawEllipse(QPointF(lx + 5, leg_y - 4), 5, 5)
            p.setPen(QColor("#AABCCC"))
            p.drawText(int(lx + 14), int(leg_y + 1), label)
            lx += 68

        # ── Header ───────────────────────────────────────────────────── #
        n = len(events)
        if n:
            eq_n  = sum(1 for e in events if e.get("color") == "#00BBFF")
            cfl_n = n - eq_n
            status = f"EVENTS  ·  {cfl_n} CONFLICT  ·  {eq_n} SEISMIC"
        else:
            status = "GLOBAL EVENTS  ·  FETCHING…"
        p.setFont(QFont(theme.FONT_FALLBACK, 8))
        p.setPen(QColor("#00D4FF"))
        p.drawText(x + 10, y + 18, status)
        # Live pulse dot in header
        pulse_c = QColor("#FF3B3B") if self._blink else QColor("#CC0000")
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(pulse_c)
        p.drawEllipse(QPointF(x + w - 22, y + 12), 5, 5)
        p.setPen(QColor("#CC4444"))
        p.setFont(QFont(theme.FONT_FALLBACK, 7))
        p.drawText(x + w - 40, y + 17, "LIVE")

        # ── Vignette — dark edges draw the eye to the hotspots ────────── #
        vig = QRadialGradient(x + w / 2, y + h / 2, max(w, h) * 0.62)
        vig.setColorAt(0.0, QColor(0, 0, 0, 0))
        vig.setColorAt(0.7, QColor(0, 0, 0, 0))
        vig.setColorAt(1.0, QColor(0, 0, 0, 160))
        p.fillRect(x, y, w, h, QBrush(vig))

    # ── News feed (right panel top) ───────────────────────────────────── #

    def _draw_news_feed(
        self, p: QPainter, x: int, y: int, w: int, h: int
    ) -> None:
        """Single-column news cards, Bloomberg-style."""
        HEADER_H = 26
        CARD_H   = 70
        CARD_GAP = 4
        PAD      = 8

        # Section header with gradient
        hdr_grad = QLinearGradient(x, y, x + w, y)
        hdr_grad.setColorAt(0.0, QColor("#0D1828"))
        hdr_grad.setColorAt(1.0, QColor("#080D18"))
        p.fillRect(x, y, w, HEADER_H, QBrush(hdr_grad))

        # Left accent stripe
        p.fillRect(x, y, 3, HEADER_H, QColor("#00D4FF"))

        p.setPen(QColor("#00D4FF"))
        hf = QFont(theme.FONT_FALLBACK, 7)
        hf.setWeight(QFont.Weight.Bold)
        hf.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, 1.5)
        p.setFont(hf)
        p.drawText(x + PAD + 6, y + 17, "LIVE INTEL FEED")

        # Article count badge
        count_str = f"{len(self._news_cards)} articles"
        badge_bg = QColor("#00D4FF")
        badge_bg.setAlpha(30)
        p.setBrush(badge_bg)
        p.setPen(Qt.PenStyle.NoPen)
        p.drawRoundedRect(x + w - 74, y + 5, 66, 14, 4, 4)
        p.setPen(QColor("#00D4FF"))
        p.setFont(QFont(theme.FONT_FALLBACK, 7))
        p.drawText(
            QRectF(x + w - 74, y + 5, 66, 14),
            Qt.AlignmentFlag.AlignCenter | Qt.AlignmentFlag.AlignVCenter,
            count_str
        )

        bc = QColor("#00D4FF")
        bc.setAlpha(60)
        p.setPen(QPen(bc, 1))
        p.drawLine(x, y + HEADER_H - 1, x + w, y + HEADER_H - 1)

        cards = self._news_cards

        if not cards:
            p.setPen(QColor(theme.MUTED_STEEL))
            p.setFont(QFont(theme.FONT_FALLBACK, 9))
            p.drawText(x + PAD, y + HEADER_H + 20, "Fetching headlines…")
            return

        # Clip to news panel area so scrolled cards don't bleed outside
        p.setClipRect(x, y + HEADER_H, w, h - HEADER_H)
        iy = y + HEADER_H + CARD_GAP - self._news_panel_scroll_y

        # Scroll bar (right edge)
        total_card_h = len(cards) * (CARD_H + CARD_GAP)
        content_h    = h - HEADER_H
        if total_card_h > content_h:
            track_h  = content_h - 4
            bar_h    = max(20, int(track_h * content_h / total_card_h))
            scroll_r = self._news_panel_scroll_y / max(1, total_card_h - content_h)
            bar_y    = y + HEADER_H + 2 + int(scroll_r * (track_h - bar_h))
            p.fillRect(x + w - 5, y + HEADER_H + 2, 4, track_h, QColor("#0A1828"))
            p.fillRect(x + w - 5, bar_y, 4, bar_h, QColor("#1E7AAA"))

        for i, card in enumerate(cards):
            if iy + CARD_H < y + HEADER_H:   # above visible area — skip but count
                iy += CARD_H + CARD_GAP
                continue
            if iy > y + h:
                break

            cx = x + PAD
            cw = w - PAD * 2

            # Card background — alternating with subtle colour tint
            bg = QColor("#0C1420") if i % 2 == 0 else QColor("#07111E")
            p.fillRect(cx - 2, iy, cw + 4, CARD_H, bg)

            # Left accent bar — 5px wide, source colour
            src_col = QColor(_src_color(card.get("source", "")))
            p.fillRect(cx - 2, iy + 2, 5, CARD_H - 4, src_col)

            # Source badge — coloured pill background
            src_text = card.get("source", "NEWS")[:20]
            badge_w  = len(src_text) * 6 + 12
            badge_bg = QColor(src_col)
            badge_bg.setAlpha(50)
            p.setBrush(badge_bg)
            p.setPen(Qt.PenStyle.NoPen)
            p.drawRoundedRect(cx + 8, iy + 4, badge_w, 14, 3, 3)
            src_f = QFont(theme.FONT_FALLBACK, 7)
            src_f.setWeight(QFont.Weight.Bold)
            p.setFont(src_f)
            p.setPen(src_col)
            p.drawText(cx + 12, iy + 14, src_text)

            # Timestamp (right-aligned)
            if card.get("time"):
                p.setPen(QColor("#506070"))
                p.setFont(QFont(theme.FONT_FALLBACK, 7))
                p.drawText(
                    QRectF(cx, iy + 4, cw, 14),
                    Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                    card["time"][:16]
                )

            # Headline — bright white
            p.setPen(QColor("#EEF6FF"))
            hf2 = QFont(theme.FONT_FALLBACK, 9)
            p.setFont(hf2)
            p.drawText(
                QRectF(cx + 8, iy + 20, cw - 12, CARD_H - 24),
                Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop
                | Qt.TextFlag.TextWordWrap,
                card["title"]
            )

            # Bottom separator — source colour tint
            sep_c = QColor(src_col)
            sep_c.setAlpha(30)
            p.setPen(QPen(sep_c, 1))
            p.drawLine(cx, iy + CARD_H, cx + cw, iy + CARD_H)

            iy += CARD_H + CARD_GAP

        p.setClipping(False)

    # ── Market + weather panel (right panel bottom) ───────────────────── #

    def _draw_market_panel(
        self, p: QPainter, x: int, y: int, w: int, h: int
    ) -> None:
        """Markets on left half, weather + system on right half."""
        PAD     = 8
        HDR_H   = 26
        mid_x   = x + w // 2

        # Background gradient
        mp_grad = QLinearGradient(x, y, x, y + h)
        mp_grad.setColorAt(0.0, QColor("#060C18"))
        mp_grad.setColorAt(1.0, QColor("#030810"))
        p.fillRect(x, y, w, h, QBrush(mp_grad))
        bc = QColor("#00D4FF")
        bc.setAlpha(50)
        p.setPen(QPen(bc, 1))
        p.drawLine(x, y, x + w, y)

        sports_h = min(118, max(78, int(h * 0.34)))
        self._draw_sports_panel(p, x, y, w, sports_h)
        y += sports_h
        h -= sports_h
        mid_x = x + w // 2

        # Left column header: MARKETS — amber
        p.setPen(QColor("#F59E0B"))
        hf = QFont(theme.FONT_FALLBACK, 7)
        hf.setWeight(QFont.Weight.Bold)
        hf.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, 1.5)
        p.setFont(hf)
        p.fillRect(x + PAD - 2, y + 6, 3, 14, QColor("#F59E0B"))
        p.drawText(x + PAD + 6, y + 17, "MARKETS")

        # Right column header: WORLD WEATHER — sky blue
        p.setPen(QColor("#5BC8F5"))
        p.fillRect(mid_x + PAD - 2, y + 6, 3, 14, QColor("#5BC8F5"))
        p.drawText(mid_x + PAD + 6, y + 17, "WORLD WEATHER")

        # Vertical divider between columns
        div_c = QColor("#00D4FF")
        div_c.setAlpha(35)
        p.setPen(QPen(div_c, 1))
        p.drawLine(mid_x, y + HDR_H, mid_x, y + h - 4)

        # ── Markets ─────────────────────────────────────────────────── #
        miy = y + HDR_H + 6
        col_w = mid_x - x - PAD * 2

        if self._markets:
            for m in self._markets[:6]:
                if miy + 24 > y + h - 2:
                    break
                chg    = m["change"]
                is_up  = chg >= 0
                chg_c  = QColor("#00FF88") if is_up else QColor("#FF4444")
                price  = m["price"]
                price_str = (
                    f"{price:>10,.0f}" if price > 10000 else
                    f"{price:>10,.2f}" if price > 1 else
                    f"{price:>10.4f}"
                )

                # Row bg tint — subtle green or red
                row_bg = QColor(0, 255, 100, 12) if is_up else QColor(255, 50, 50, 12)
                p.fillRect(x + PAD - 2, miy, col_w + 4, 24, row_bg)

                # Label
                lf = QFont(theme.FONT_FALLBACK, 8)
                lf.setWeight(QFont.Weight.Bold)
                p.setFont(lf)
                p.setPen(QColor("#EEF6FF"))
                p.drawText(x + PAD, miy + 15, m["label"])

                # Price (right-aligned in column)
                p.setFont(QFont(theme.FONT_FALLBACK, 8))
                p.setPen(QColor("#EEF6FF"))
                p.drawText(
                    QRectF(x + PAD + 55, miy + 3, col_w - 55, 14),
                    Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                    price_str.strip()
                )

                # Change % pill with coloured background
                sign  = "▲" if is_up else "▼"
                chg_s = f"{sign}{abs(chg):.2f}%"
                pill_bg = QColor(0, 200, 80, 60) if is_up else QColor(220, 40, 40, 60)
                pill_x = int(x + PAD + 60)
                pill_w = 52
                p.setBrush(pill_bg)
                p.setPen(Qt.PenStyle.NoPen)
                p.drawRoundedRect(pill_x, miy + 14, pill_w, 12, 3, 3)
                p.setFont(QFont(theme.FONT_FALLBACK, 7))
                p.setPen(chg_c)
                p.drawText(
                    QRectF(pill_x, miy + 14, pill_w, 12),
                    Qt.AlignmentFlag.AlignCenter | Qt.AlignmentFlag.AlignVCenter,
                    chg_s
                )

                miy += 28
        else:
            p.setPen(QColor(theme.MUTED_STEEL))
            p.setFont(QFont(theme.FONT_FALLBACK, 8))
            p.drawText(x + PAD, y + HDR_H + 22, "Loading markets…")

        # ── Weather ─────────────────────────────────────────────────── #
        wiy = y + HDR_H + 6
        wx_col_w = x + w - mid_x - PAD * 2

        if self._weather_data:
            for wx in self._weather_data[:5]:
                if wiy + 22 > y + h - 2:
                    break
                temp_val = self._to_float(wx["temp"])
                temp_c   = (
                    QColor("#FF6B35") if (temp_val is not None and temp_val > 38) else
                    QColor(theme.WARNING_AMBER) if (temp_val is not None and temp_val > 30) else
                    QColor("#5BC8F5") if (temp_val is not None and temp_val < 5) else
                    QColor(theme.GHOST_WHITE)
                )

                cf = QFont(theme.FONT_FALLBACK, 8)
                cf.setWeight(QFont.Weight.Bold)
                p.setFont(cf)
                p.setPen(QColor(theme.GHOST_WHITE))
                p.drawText(mid_x + PAD, wiy + 13, wx["city"][:12])

                p.setFont(QFont(theme.FONT_FALLBACK, 8))
                p.setPen(temp_c)
                temp_str = f"{wx['temp']}°C" if temp_val is not None else "--"
                p.drawText(
                    QRectF(mid_x + PAD + 70, wiy + 3, wx_col_w - 70, 14),
                    Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                    temp_str
                )

                p.setPen(QColor(theme.MUTED_STEEL))
                p.setFont(QFont(theme.FONT_FALLBACK, 7))
                p.drawText(
                    QRectF(mid_x + PAD + 70, wiy + 14, wx_col_w - 70, 12),
                    Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                    wx["desc"]
                )

                wiy += 26
        else:
            p.setPen(QColor(theme.MUTED_STEEL))
            p.setFont(QFont(theme.FONT_FALLBACK, 8))
            p.drawText(mid_x + PAD, y + HDR_H + 22, "Loading weather…")

        # ── System strip at bottom — with mini fill bars ─────────────── #
        strip_y = y + h - 22
        p.fillRect(x, strip_y - 2, w, 24, QColor("#03070F"))
        bc2 = QColor("#00D4FF")
        bc2.setAlpha(30)
        p.setPen(QPen(bc2, 1))
        p.drawLine(x, strip_y - 2, x + w, strip_y - 2)

        items = [
            (f"CPU {self._cpu:.0f}%",  self._cpu),
            (f"RAM {self._ram:.0f}%",  self._ram),
            (f"BAT {self._bat:.0f}%{'+' if self._plugged else ''}",
             0 if self._plugged else 100 - self._bat),
            ("NET " + ("UP" if self._net_up else "DOWN"), 0 if self._net_up else 100),
        ]
        item_w = w // len(items)
        bar_h  = 3
        p.setFont(QFont(theme.FONT_FALLBACK, 7))
        for i, (label, pct) in enumerate(items):
            ix = x + i * item_w
            c  = self._health_color(pct)
            p.setPen(c)
            p.drawText(
                QRectF(ix, strip_y, item_w, 16),
                Qt.AlignmentFlag.AlignCenter | Qt.AlignmentFlag.AlignVCenter,
                label
            )
            # Mini fill bar below text
            bar_fill = int(item_w * min(pct, 100) / 100)
            bar_bg = QColor(c)
            bar_bg.setAlpha(20)
            p.fillRect(ix, strip_y + 16, item_w, bar_h, bar_bg)
            p.fillRect(ix, strip_y + 16, bar_fill, bar_h, c)

    # ── Bottom ticker ─────────────────────────────────────────────────── #

    def _draw_sports_panel(
        self, p: QPainter, x: int, y: int, w: int, h: int
    ) -> None:
        pad = 8
        # Gradient bg: deep blue to slightly lighter
        sp_grad = QLinearGradient(x, y, x, y + h)
        sp_grad.setColorAt(0.0, QColor("#080C14"))
        sp_grad.setColorAt(1.0, QColor("#060A10"))
        p.fillRect(x, y, w, h, QBrush(sp_grad))

        p.setPen(QColor("#00D4FF"))
        hf = QFont(theme.FONT_FALLBACK, 7)
        hf.setWeight(QFont.Weight.Bold)
        hf.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, 1.5)
        p.setFont(hf)
        p.drawText(x + pad, y + 17, "LIVE SCOREBOARD")

        # "CRICKET / SPORTS" badge with filled bg
        badge_bg = QColor("#F59E0B")
        badge_bg.setAlpha(40)
        p.setBrush(badge_bg)
        p.setPen(Qt.PenStyle.NoPen)
        p.drawRoundedRect(x + w - 102, y + 5, 96, 14, 4, 4)
        p.setPen(QColor("#F59E0B"))
        p.setFont(QFont(theme.FONT_FALLBACK, 7))
        p.drawText(
            QRectF(x + w - 102, y + 5, 96, 14),
            Qt.AlignmentFlag.AlignCenter | Qt.AlignmentFlag.AlignVCenter,
            "CRICKET / SPORTS"
        )

        bc = QColor("#00D4FF")
        bc.setAlpha(55)
        p.setPen(QPen(bc, 1))
        p.drawLine(x, y + 24, x + w, y + 24)
        p.drawLine(x, y + h - 1, x + w, y + h - 1)

        if not self._sports:
            p.setPen(QColor("#4A6080"))
            p.setFont(QFont(theme.FONT_FALLBACK, 8))
            p.drawText(x + pad, y + 50, "Scanning live cricket feeds...")
            return

        _LEAGUE_COLORS = {
            "IPL":    "#FF6B35",
            "INDIA":  "#138808",
            "CRICKET":"#00D4FF",
        }
        cards = self._sports[:3]
        card_w = max(120, (w - pad * 2 - 8) // max(1, len(cards)))
        card_h = max(42, h - 34)
        cx = x + pad
        for card in cards:
            if cx + card_w > x + w - pad + 2:
                break
            # Card bg with gradient
            cg = QLinearGradient(cx, y + 30, cx + card_w, y + 30)
            cg.setColorAt(0.0, QColor("#0C1520"))
            cg.setColorAt(1.0, QColor("#08101A"))
            p.fillRect(cx, y + 30, card_w, card_h, QBrush(cg))

            league = card.get("league", "LIVE")
            lc = QColor(_LEAGUE_COLORS.get(league, "#FF6B35"))
            p.fillRect(cx, y + 30, 4, card_h, lc)

            # League badge — coloured pill
            badge_col = QColor(lc)
            badge_col.setAlpha(55)
            lbl_w = len(league) * 6 + 14
            p.setBrush(badge_col)
            p.setPen(Qt.PenStyle.NoPen)
            p.drawRoundedRect(cx + 8, y + 32, lbl_w, 14, 4, 4)
            p.setPen(lc)
            lf = QFont(theme.FONT_FALLBACK, 7)
            lf.setWeight(QFont.Weight.Bold)
            p.setFont(lf)
            p.drawText(cx + 12, y + 43, league)

            p.setPen(QColor("#EEF6FF"))
            p.setFont(QFont(theme.FONT_FALLBACK, 8))
            p.drawText(
                QRectF(cx + 8, y + 50, card_w - 16, card_h - 22),
                Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop | Qt.TextFlag.TextWordWrap,
                card.get("summary", "")[:170],
            )
            cx += card_w + 4

    def _draw_ticker(self, p: QPainter, w: int, y: int, h: int) -> None:
        # Ticker bg gradient
        tk_grad = QLinearGradient(0, y, 0, y + h)
        tk_grad.setColorAt(0.0, QColor("#08101C"))
        tk_grad.setColorAt(1.0, QColor("#030810"))
        p.fillRect(0, y, w, h, QBrush(tk_grad))

        bc = QColor("#00D4FF")
        bc.setAlpha(80)
        p.setPen(QPen(bc, 1))
        p.drawLine(0, y, w, y)

        # LIVE badge on left
        live_bg = QColor("#CC1111")
        p.setBrush(live_bg)
        p.setPen(Qt.PenStyle.NoPen)
        p.drawRect(0, y, 44, h)
        p.setPen(QColor("#FFFFFF"))
        lf = QFont(theme.FONT_FALLBACK, 8)
        lf.setWeight(QFont.Weight.Bold)
        p.setFont(lf)
        p.drawText(QRectF(0, y, 44, h), Qt.AlignmentFlag.AlignCenter, "LIVE")

        # Scrolling ticker text — near white for readability
        p.setPen(QColor("#E8F0FF"))
        p.setFont(QFont(theme.FONT_FALLBACK, 9))
        p.setClipRect(44, y, w - 280, h)
        p.drawText(int(self._news_scroll_x), y + 21, self._news_ticker)
        p.setClipping(False)

        # Hint right
        hint_c = QColor("#3A5068")
        p.setPen(hint_c)
        p.setFont(QFont(theme.FONT_FALLBACK, 8))
        p.drawText(
            QRectF(w - 230, y + 6, 224, 20),
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
            'ESC / double-click to close'
        )

    # ── Helpers ───────────────────────────────────────────────────────── #

    def _panel_header(
        self, p: QPainter, x: int, y: int, w: int, title: str
    ) -> None:
        p.fillRect(x, y, w, 24, QColor("#080D14"))
        p.setPen(QColor(theme.ARC_BLUE))
        hf = QFont(theme.FONT_FALLBACK, 7)
        hf.setWeight(QFont.Weight.Bold)
        hf.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, 1.5)
        p.setFont(hf)
        p.drawText(x + 10, y + 16, title)
        bc = QColor(theme.ARC_BLUE)
        bc.setAlpha(50)
        p.setPen(QPen(bc, 1))
        p.drawLine(x, y + 23, x + w, y + 23)

    def _health_color(self, pct: float) -> QColor:
        if pct > 85:
            return QColor(theme.CRITICAL_RED)
        if pct > 60:
            return QColor(theme.WARNING_AMBER)
        return QColor(theme.SUIT_GREEN)

    @staticmethod
    def _to_float(value) -> Optional[float]:
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    def get_briefing(self) -> str:
        """Return a full spoken briefing of everything on screen."""
        parts: list[str] = []

        # ── Global events ──────────────────────────────────────────────── #
        n = len(self._gdelt_events)
        if n:
            top = self._gdelt_events[0]
            parts.append(
                f"The global events map is tracking {n} active hotspots. "
                f"The most active region is {top['title']}."
            )

        # ── All headlines ──────────────────────────────────────────────── #
        if self._news_cards:
            cards = self._news_cards[:10]
            parts.append(f"I have {len(cards)} headlines for you.")
            for i, c in enumerate(cards, 1):
                # Strip trailing punctuation/dashes so TTS doesn't stutter;
                # Google News titles already embed "- Source" so don't re-append.
                title = c["title"].rstrip(" .,!?-—")
                parts.append(f"Headline {i}: {title}.")

        # ── Sports ────────────────────────────────────────────────────────#
        if self._sports:
            parts.append("Live sports updates.")
            for s in self._sports:
                parts.append(f"{s['league']}: {s['summary']}.")

        # ── Markets ───────────────────────────────────────────────────── #
        if self._markets:
            parts.append("Market overview.")
            for m in self._markets:
                direction = "up" if m.get("change", 0) >= 0 else "down"
                parts.append(
                    f"{m['label']} is {direction} {abs(m.get('change', 0)):.1f} percent, "
                    f"trading at {m.get('price', 0):,.0f}."
                )

        # ── Weather ───────────────────────────────────────────────────── #
        if self._weather_data:
            parts.append("Current weather.")
            for wx in self._weather_data:
                parts.append(
                    f"{wx['city']}: {wx['temp']} degrees, {wx['desc']}."
                )

        if not parts:
            parts.append("Data feeds are still loading, sir. Please give it a moment.")

        return " ".join(parts)

    def push_conversation(self, user_text: str, jarvis_text: str) -> None:
        ts = datetime.now().strftime("%H:%M")
        if user_text:
            self._conv_log.append((ts, "YOU", user_text))
        if jarvis_text:
            self._conv_log.append((ts, "JARVIS", jarvis_text))
        self._conv_log = self._conv_log[-20:]
