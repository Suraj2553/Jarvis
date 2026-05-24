"""ui/hud.py — JARVIS Holographic HUD v2.

380×380px frameless transparent always-on-top window.
Click-through when idle (Windows layered window).
Five visual layers as described in the design spec.
Drops in as a replacement for overlay.py + waveform.py.

States: idle | listening | thinking | speaking
"""

import math
import sys
import threading
import time
from datetime import datetime
from typing import Optional

from PyQt6.QtCore import (
    Qt, QPointF, QRectF, QTimer, pyqtSignal, QObject,
    QPropertyAnimation, QEasingCurve,
)
from PyQt6.QtGui import (
    QBrush, QColor, QFont, QPainter, QPen,
    QRadialGradient, QLinearGradient, QPolygonF,
)
from PyQt6.QtWidgets import QApplication, QWidget, QLabel, QVBoxLayout
from PyQt6.QtGui import QRegion

from ui import theme

# Friday warm-rose accent color (replaces cold blue when Friday is active)
_FRIDAY_ROSE  = QColor("#E07896")

# ── Win32 click-through ───────────────────────────────────────────── #
try:
    import win32gui
    import win32con
    _HAS_WIN32 = True
except Exception:
    _HAS_WIN32 = False


def _set_click_through(hwnd: int, enable: bool) -> None:
    if not _HAS_WIN32:
        return
    try:
        style = win32gui.GetWindowLong(hwnd, win32con.GWL_EXSTYLE)
        if enable:
            style |= win32con.WS_EX_TRANSPARENT | win32con.WS_EX_LAYERED
        else:
            style &= ~win32con.WS_EX_TRANSPARENT
        win32gui.SetWindowLong(hwnd, win32con.GWL_EXSTYLE, style)
    except Exception:
        pass


# ── Per-state colors ─────────────────────────────────────────────── #
_STATE_COLOR = {
    "idle":      QColor(theme.ARC_BLUE),
    "listening": QColor(theme.ARC_BLUE),
    "thinking":  QColor(theme.REPULSOR_PURPLE),
    "speaking":  QColor(theme.SUIT_GREEN),
}

_STATE_GLOW = {
    "idle":      0.28,
    "listening": 0.72,
    "thinking":  0.86,
    "speaking":  1.00,
}


class HUDSignals(QObject):
    set_state   = pyqtSignal(str)
    show_text   = pyqtSignal(str, str)   # (user_text, jarvis_text)
    show_win    = pyqtSignal()
    hide_win    = pyqtSignal()
    show_settings = pyqtSignal()
    quit_app    = pyqtSignal()
    update_rms  = pyqtSignal(float)
    meeting_mode = pyqtSignal(bool)
    show_war_room = pyqtSignal()
    hide_war_room = pyqtSignal()
    # Persona / mute — emitted by HUD clicks, consumed by main
    toggle_persona = pyqtSignal()
    toggle_mute    = pyqtSignal()
    # Persona / mute display — emitted by main, consumed by HUD
    set_persona_sig = pyqtSignal(str)    # "jarvis" | "friday"
    set_muted_sig   = pyqtSignal(bool)


class HUDWidget(QWidget):
    """The 380×380 holographic arc reactor HUD."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.state = "idle"
        self.rms_value = 0.0
        self._persona = "jarvis"   # "jarvis" | "friday"
        self._muted   = False

        # System stats (updated every 2s)
        self._cpu = 0.0
        self._ram = 0.0
        self._bat = 100.0
        self._plugged = True
        self._meeting_live = False

        # Animation time base
        self._t0 = time.monotonic()

        # Speaking rings: list of (birth_time)
        self._speaking_rings: list[float] = []

        # Scan line position (0.0 → 1.0)
        self._scan_y = -0.1

        # Thinking arc angle
        self._think_angle = 0.0

        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setMinimumSize(theme.HUD_SIZE, theme.HUD_SIZE)

        # 60fps frame timer
        self._frame_timer = QTimer(self)
        self._frame_timer.timeout.connect(self.update)
        self._frame_timer.start(16)

        # Node update timer
        self._node_timer = QTimer(self)
        self._node_timer.timeout.connect(self._refresh_nodes)
        self._node_timer.start(theme.NODE_UPDATE_MS)

        # Speaking ring spawner
        self._ring_timer = QTimer(self)
        self._ring_timer.timeout.connect(self._spawn_ring)

        # Scan line timer
        self._scan_timer = QTimer(self)
        self._scan_timer.timeout.connect(self._advance_scan)
        self._scan_timer.start(theme.SCAN_LINE_INTERVAL_MS)

        # Thinking arc animation
        self._think_timer = QTimer(self)
        self._think_timer.timeout.connect(self._advance_think)
        self._think_timer.start(16)

    # ------------------------------------------------------------------ #
    #  State management                                                    #
    # ------------------------------------------------------------------ #

    def set_state(self, state: str) -> None:
        self.state = state
        self._ring_timer.stop()
        if state == "speaking":
            self._ring_timer.start(theme.RING_SPAWN_INTERVAL_MS)

    def set_persona_state(self, persona: str) -> None:
        self._persona = persona
        self.update()

    def set_muted_state(self, muted: bool) -> None:
        self._muted = muted
        self.update()

    # ------------------------------------------------------------------ #
    #  Timer callbacks                                                     #
    # ------------------------------------------------------------------ #

    def _refresh_nodes(self) -> None:
        try:
            import psutil
            self._cpu = psutil.cpu_percent(interval=None)
            self._ram = psutil.virtual_memory().percent
            bat = psutil.sensors_battery()
            if bat:
                self._bat = bat.percent
                self._plugged = bat.power_plugged
        except Exception:
            pass

    def _spawn_ring(self) -> None:
        self._speaking_rings.append(time.monotonic())
        # Keep max 5 rings at once
        if len(self._speaking_rings) > 5:
            self._speaking_rings.pop(0)

    def _advance_scan(self) -> None:
        self._scan_y = -0.05

    def _advance_think(self) -> None:
        self._think_angle = (self._think_angle + 3) % 360

    # ------------------------------------------------------------------ #
    #  Paint                                                               #
    # ------------------------------------------------------------------ #

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)

        w = self.width()
        h = self.height()
        cx = w / 2
        cy = h / 2
        t = time.monotonic() - self._t0
        state = self.state
        glow = _STATE_GLOW.get(state, 0.3)
        color = _STATE_COLOR.get(state, QColor(theme.ARC_BLUE))

        # ── Layer 0: Background bloom ─────────────────────────────── #
        self._draw_bloom(painter, cx, cy, color, glow)

        # ── Layer 1: Outer orbit ring (r=175) ─────────────────────── #
        self._draw_orbit_ring(painter, cx, cy, t, glow)

        # ── Layer 2: Data ring (r=155) with nodes ─────────────────── #
        self._draw_data_ring(painter, cx, cy, t)

        # ── Layer 3: Status arc ring (r=140) ──────────────────────── #
        self._draw_status_arc(painter, cx, cy, t, glow)

        # ── Layer 4: State-specific waveform ring ─────────────────── #
        if state == "idle":
            self._draw_idle_rings(painter, cx, cy, t)
        elif state == "listening":
            self._draw_listening_bars(painter, cx, cy, t)
        elif state == "thinking":
            self._draw_thinking_arcs(painter, cx, cy, t)
        elif state == "speaking":
            self._draw_speaking_rings(painter, cx, cy, t, color)

        # ── Layer 5: Center circle (r=65) ─────────────────────────── #
        self._draw_center(painter, cx, cy, t, state, color, glow)

        # ── Scan line ──────────────────────────────────────────────── #
        self._draw_scan_line(painter, w, h, t)

        # ── Meeting live indicator ─────────────────────────────────── #
        if self._meeting_live:
            self._draw_meeting_dot(painter, w)

        painter.end()

    # ------------------------------------------------------------------ #
    #  Layer renderers                                                     #
    # ------------------------------------------------------------------ #

    def _draw_bloom(self, p: QPainter, cx, cy, color: QColor, glow: float) -> None:
        bloom_r = 200
        grad = QRadialGradient(QPointF(cx, cy), bloom_r)
        bloom_color = QColor(color)
        bloom_color.setAlphaF(glow * 0.12)
        grad.setColorAt(0, bloom_color)
        grad.setColorAt(1, QColor(0, 0, 0, 0))
        p.fillRect(0, 0, int(cx * 2), int(cy * 2), QBrush(grad))

    def _draw_orbit_ring(self, p: QPainter, cx, cy, t: float, glow: float) -> None:
        r = 175
        state = self.state
        rpm = -1.2 if state != "idle" else -0.4
        angle = (t * rpm * 360 / 60) % 360

        color = QColor(theme.ARC_BLUE)
        alpha = int(255 * (0.35 if state != "idle" else 0.18))
        color.setAlpha(alpha)

        pen = QPen(color, 1.5, Qt.PenStyle.DotLine)
        p.save()
        p.translate(cx, cy)
        p.rotate(angle)
        p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawEllipse(QRectF(-r, -r, r * 2, r * 2))
        p.restore()

    def _draw_data_ring(self, p: QPainter, cx, cy, t: float) -> None:
        r = 155

        # Ring
        ring_color = QColor(theme.ARC_BLUE)
        ring_color.setAlpha(40)
        p.setPen(QPen(ring_color, 1))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawEllipse(QRectF(cx - r, cy - r, r * 2, r * 2))

        # Four data nodes at 12, 3, 6, 9 o'clock
        nodes = [
            (0,   -r, f"{datetime.now().strftime('%H:%M')}"),
            (r,    0, f"{self._bat:.0f}%{'+' if self._plugged else ''}"),
            (0,    r, f"RAM {self._ram:.0f}%"),
            (-r,   0, f"CPU {self._cpu:.0f}%"),
        ]
        font = QFont(theme.FONT_FALLBACK, 9)
        font.setWeight(QFont.Weight.Normal)
        p.setFont(font)

        for dx, dy, text in nodes:
            x = cx + dx
            y = cy + dy
            # Glow dot
            dot_color = QColor(theme.ARC_BLUE)
            dot_color.setAlpha(180)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QBrush(dot_color))
            p.drawEllipse(QRectF(x - 3, y - 3, 6, 6))
            # Text
            text_color = QColor(theme.MUTED_STEEL)
            p.setPen(text_color)
            p.drawText(QRectF(x - 25, y - 10, 50, 14),
                       Qt.AlignmentFlag.AlignCenter, text)

    def _draw_status_arc(self, p: QPainter, cx, cy, t: float, glow: float) -> None:
        r = 140
        # Health = 100 - average of cpu + ram
        health = 100 - (self._cpu + self._ram) / 2
        health = max(0, min(100, health))

        if health > 70:
            arc_color = QColor(theme.SUIT_GREEN)
        elif health > 40:
            arc_color = QColor(theme.WARNING_AMBER)
        else:
            arc_color = QColor(theme.CRITICAL_RED)

        arc_color.setAlpha(180)

        # Slowly rotating arc
        rotation = (t * 2) % 360  # 2 deg/sec
        span_degrees = int(health * 360 / 100)

        pen = QPen(arc_color, 2)
        p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.save()
        p.translate(cx, cy)
        p.rotate(rotation)
        p.drawArc(
            QRectF(-r, -r, r * 2, r * 2),
            0,
            span_degrees * 16,  # Qt uses 1/16 degree units
        )
        p.restore()

    def _draw_idle_rings(self, p: QPainter, cx, cy, t: float) -> None:
        # Three thin concentric arcs at r=105, 95, 85 with different rotation speeds
        speeds = [0.3, 0.7, 1.1]  # rpm
        radii = [105, 95, 85]
        # Breathing opacity: 15%↔35% at 0.15Hz
        breath = 0.15 + 0.20 * (0.5 + 0.5 * math.sin(2 * math.pi * 0.15 * t))

        for r, rpm in zip(radii, speeds):
            angle = (t * rpm * 360 / 60) % 360
            color = QColor(theme.ARC_BLUE_DIM if theme.ARC_BLUE_DIM != theme.ARC_BLUE else theme.ARC_BLUE)
            color.setAlphaF(breath)
            p.save()
            p.translate(cx, cy)
            p.rotate(angle)
            p.setPen(QPen(color, 1))
            p.setBrush(Qt.BrushStyle.NoBrush)
            # Partial arc (270 degrees, leaving gap)
            p.drawArc(QRectF(-r, -r, r * 2, r * 2), 0, 270 * 16)
            p.restore()

        # 8 tiny dots rotating opposite direction
        dot_angle = -(t * 0.5 * 360 / 60) % 360
        for i in range(8):
            a = math.radians(dot_angle + i * 45)
            r = 98
            dx = cx + r * math.cos(a)
            dy = cy + r * math.sin(a)
            dot_c = QColor(theme.ARC_BLUE)
            dot_c.setAlphaF(breath * 0.6)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QBrush(dot_c))
            p.drawEllipse(QRectF(dx - 2, dy - 2, 4, 4))

    def _draw_listening_bars(self, p: QPainter, cx, cy, t: float) -> None:
        NUM_BARS = 24
        rms = self.rms_value
        bar_color = QColor(theme.ARC_BLUE)
        bar_color.setAlpha(220)
        p.setPen(QPen(bar_color, 2, Qt.PenStyle.SolidLine,
                      Qt.PenCapStyle.RoundCap))

        for i in range(NUM_BARS):
            angle_deg = (i * 360 / NUM_BARS) - 90
            angle = math.radians(angle_deg)

            # Bar height driven by RMS + per-bar variation
            variation = 0.5 + 0.5 * math.sin(t * 8 + i * 0.9)
            height = max(4, (rms * 400 + variation * 15) * (1.3 if i % 3 == 0 else 1.0))
            height = min(height, 35)

            r_inner = 75
            r_outer = r_inner + height

            x1 = cx + r_inner * math.cos(angle)
            y1 = cy + r_inner * math.sin(angle)
            x2 = cx + r_outer * math.cos(angle)
            y2 = cy + r_outer * math.sin(angle)
            p.drawLine(QPointF(x1, y1), QPointF(x2, y2))

        # Radar sweep
        sweep_angle = (t * 180) % 360  # 0.5 rev/sec
        sweep_color = QColor(theme.ARC_BLUE)
        sweep_color.setAlpha(40)
        grad = QLinearGradient(QPointF(cx, cy),
                               QPointF(cx + 110 * math.cos(math.radians(sweep_angle)),
                                       cy + 110 * math.sin(math.radians(sweep_angle))))
        grad.setColorAt(0, QColor(0, 0, 0, 0))
        grad.setColorAt(1, sweep_color)
        p.setBrush(QBrush(grad))
        p.setPen(Qt.PenStyle.NoPen)
        p.save()
        p.translate(cx, cy)
        p.rotate(sweep_angle)
        p.drawPie(QRectF(-110, -110, 220, 220), 0, -40 * 16)
        p.restore()

    def _draw_thinking_arcs(self, p: QPainter, cx, cy, t: float) -> None:
        # 4 arc segments spinning at 180 deg/sec clockwise
        purple = QColor(theme.REPULSOR_PURPLE)
        purple.setAlpha(200)

        # Outer ring (r=100): 4 segments, 60° each with gaps
        p.setPen(QPen(purple, 3))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.save()
        p.translate(cx, cy)
        p.rotate(self._think_angle)  # updated in _advance_think
        for i in range(4):
            p.drawArc(QRectF(-100, -100, 200, 200), i * 90 * 16, 60 * 16)
        p.restore()

        # Inner ring (r=80): counter-spinning
        inner_color = QColor(theme.REPULSOR_PURPLE)
        inner_color.setAlpha(130)
        p.setPen(QPen(inner_color, 2))
        p.save()
        p.translate(cx, cy)
        p.rotate(-self._think_angle * 0.67)
        for i in range(4):
            p.drawArc(QRectF(-80, -80, 160, 160), i * 90 * 16, 45 * 16)
        p.restore()

        # Outer glow pulses
        pulse = 0.40 + 0.50 * (0.5 + 0.5 * math.sin(2 * math.pi * 2 * t))
        glow_c = QColor(theme.REPULSOR_PURPLE)
        glow_c.setAlphaF(pulse * 0.15)
        grad = QRadialGradient(QPointF(cx, cy), 110)
        grad.setColorAt(0, glow_c)
        grad.setColorAt(1, QColor(0, 0, 0, 0))
        p.fillRect(int(cx - 115), int(cy - 115), 230, 230, QBrush(grad))

    def _draw_speaking_rings(self, p: QPainter, cx, cy, t: float, color: QColor) -> None:
        now = time.monotonic()
        dead = []
        for birth in self._speaking_rings:
            age = now - birth
            if age > 1.4:
                dead.append(birth)
                continue
            # Expand from r=40 to r=120 over 1.4s
            progress = age / 1.4
            r = 40 + 80 * progress
            alpha = int(255 * (1.0 - progress) * 0.7)
            ring_c = QColor(color)
            ring_c.setAlpha(alpha)
            p.setPen(QPen(ring_c, 2))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawEllipse(QRectF(cx - r, cy - r, r * 2, r * 2))
        for b in dead:
            self._speaking_rings.remove(b)

        # Oscillating waveform arc
        wave_color = QColor(color)
        wave_color.setAlpha(160)
        p.setPen(QPen(wave_color, 2))
        r = 80
        points = []
        for deg in range(0, 360, 5):
            a = math.radians(deg)
            wave = 8 * math.sin(t * 15 + deg * 0.15)
            rr = r + wave
            points.append(QPointF(cx + rr * math.cos(a), cy + rr * math.sin(a)))
        if points:
            for i in range(len(points) - 1):
                p.drawLine(points[i], points[i + 1])

    def _draw_center(self, p: QPainter, cx, cy, t: float,
                     state: str, color: QColor, glow: float) -> None:
        r = 65

        # Friday swaps the accent color to warm rose
        if self._persona == "friday":
            color = QColor(_FRIDAY_ROSE)

        # Filled dark circle
        bg = QColor(theme.DEEP_SPACE)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(bg))
        p.drawEllipse(QRectF(cx - r, cy - r, r * 2, r * 2))

        # Inner glow
        grad = QRadialGradient(QPointF(cx, cy), r)
        inner_glow = QColor(color)
        inner_glow.setAlphaF(0.08 * glow)
        grad.setColorAt(0, inner_glow)
        grad.setColorAt(1, QColor(0, 0, 0, 0))
        p.setBrush(QBrush(grad))
        p.drawEllipse(QRectF(cx - r, cy - r, r * 2, r * 2))

        # Rim
        rim_color = QColor(color)
        rim_color.setAlphaF(0.60)
        p.setPen(QPen(rim_color, 1))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawEllipse(QRectF(cx - r, cy - r, r * 2, r * 2))

        # Center content
        font = QFont(theme.FONT_FALLBACK, 11)
        p.setFont(font)
        persona_name = "FRIDAY" if self._persona == "friday" else "JARVIS"

        if state == "idle":
            text_c = QColor(color)
            text_c.setAlphaF(0.50)
            p.setPen(text_c)
            p.drawText(QRectF(cx - r, cy - 14, r * 2, 18),
                       Qt.AlignmentFlag.AlignCenter, persona_name)
            date_c = QColor(theme.MUTED_STEEL)
            p.setPen(date_c)
            small_font = QFont(theme.FONT_FALLBACK, 8)
            p.setFont(small_font)
            p.drawText(QRectF(cx - r, cy + 2, r * 2, 14),
                       Qt.AlignmentFlag.AlignCenter,
                       datetime.now().strftime("%b %d"))
            # Mute indicator — small dot below date
            mic_y = cy + 20
            if self._muted:
                mic_c = QColor(theme.CRITICAL_RED)
                mic_c.setAlpha(200)
                p.setPen(mic_c)
                p.setFont(QFont(theme.FONT_FALLBACK, 7))
                p.drawText(QRectF(cx - r, mic_y, r * 2, 12),
                           Qt.AlignmentFlag.AlignCenter, "MIC OFF")
            else:
                mic_c = QColor(color)
                mic_c.setAlpha(80)
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(QBrush(mic_c))
                p.drawEllipse(QRectF(cx - 3, mic_y + 3, 6, 6))

        elif state == "listening":
            text_c = QColor(color)
            blink = (int(t * 2) % 2 == 0)
            text_c.setAlphaF(0.9 if blink else 0.4)
            p.setPen(text_c)
            small_font = QFont(theme.FONT_FALLBACK, 8)
            p.setFont(small_font)
            p.drawText(QRectF(cx - r, cy - 6, r * 2, 14),
                       Qt.AlignmentFlag.AlignCenter, "LISTENING")
            # dB meter arc
            rms = self.rms_value
            db_span = min(int(rms * 3000), 270)
            meter_c = QColor(color)
            meter_c.setAlpha(120)
            p.setPen(QPen(meter_c, 3))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawArc(QRectF(cx - 45, cy - 45, 90, 90),
                      -225 * 16, -db_span * 16)

        elif state == "thinking":
            text_c = QColor(theme.REPULSOR_PURPLE)
            text_c.setAlphaF(0.9)
            p.setPen(text_c)
            small_font = QFont(theme.FONT_FALLBACK, 8)
            p.setFont(small_font)
            # Animated dots
            dots = "." * (int(t * 2) % 4)
            p.drawText(QRectF(cx - r, cy - 10, r * 2, 18),
                       Qt.AlignmentFlag.AlignCenter, f"PROCESSING{dots}")
            # Brain indicator (G=Groq, L=Local)
            # Inject from outside if needed

        elif state == "speaking":
            text_c = QColor(theme.SUIT_GREEN)
            text_c.setAlphaF(0.9)
            p.setPen(text_c)
            small_font = QFont(theme.FONT_FALLBACK, 8)
            p.setFont(small_font)
            p.drawText(QRectF(cx - r, cy - 6, r * 2, 14),
                       Qt.AlignmentFlag.AlignCenter, "SPEAKING")

    def _draw_scan_line(self, p: QPainter, w: int, h: int, t: float) -> None:
        if self._scan_y < 0:
            # Animate scan_y from 0 → 1 over ~1.5 seconds
            if self._scan_y < 0:
                return  # not active
        scan_pos = int(self._scan_y * h)
        scan_c = QColor(theme.ARC_BLUE)
        scan_c.setAlphaF(0.15)
        p.setPen(QPen(scan_c, 1))
        p.drawLine(0, scan_pos, w, scan_pos)

        self._scan_y += 0.015  # advance
        if self._scan_y > 1.05:
            self._scan_y = -0.1  # reset

    def _draw_meeting_dot(self, p: QPainter, w: int) -> None:
        # Small pulsing red dot + "LIVE" text top-right
        t = time.monotonic() - self._t0
        alpha = int(255 * (0.5 + 0.5 * math.sin(t * 3)))
        dot_c = QColor(theme.CRITICAL_RED)
        dot_c.setAlpha(alpha)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(dot_c))
        p.drawEllipse(QRectF(w - 22, 12, 8, 8))
        text_c = QColor(theme.CRITICAL_RED)
        text_c.setAlpha(200)
        p.setPen(text_c)
        p.setFont(QFont(theme.FONT_FALLBACK, 7))
        p.drawText(QRectF(w - 45, 10, 20, 12),
                   Qt.AlignmentFlag.AlignCenter, "LIVE")


class JARVISHud(QWidget):
    """Main HUD window — 380×380, bottom-right corner, always-on-top."""

    def __init__(self):
        super().__init__()
        self.signals = HUDSignals()
        self._dragging = False
        self._drag_offset = QPointF(0, 0)

        # Window setup
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setFixedSize(theme.HUD_SIZE, theme.HUD_SIZE)

        # HUD widget
        self._hud = HUDWidget(self)
        self._hud.setGeometry(0, 0, theme.HUD_SIZE, theme.HUD_SIZE)

        # Position at bottom-right
        self._snap_to_corner()

        # Wire signals
        self.signals.set_state.connect(self._on_set_state)
        self.signals.show_text.connect(self._on_show_text)
        self.signals.show_win.connect(self.show)
        self.signals.hide_win.connect(self.hide)
        self.signals.update_rms.connect(self._on_rms)
        self.signals.meeting_mode.connect(self._on_meeting)
        self.signals.quit_app.connect(QApplication.instance().quit)
        self.signals.set_persona_sig.connect(self._hud.set_persona_state)
        self.signals.set_muted_sig.connect(self._hud.set_muted_state)

        # Circular mask — clicks outside the HUD circle pass through to desktop
        _r = 175
        _s = theme.HUD_SIZE
        self.setMask(QRegion(_s // 2 - _r, _s // 2 - _r, _r * 2, _r * 2,
                             QRegion.RegionType.Ellipse))

        # Conversation panel (created lazily)
        self._panel = None

    def _snap_to_corner(self) -> None:
        screen = QApplication.primaryScreen()
        if screen:
            geo = screen.availableGeometry()
            offset = theme.HUD_EDGE_OFFSET
            x = geo.right() - theme.HUD_SIZE - offset
            y = geo.bottom() - theme.HUD_SIZE - offset
            self.move(x, y)

    # ------------------------------------------------------------------ #
    #  Signal handlers                                                     #
    # ------------------------------------------------------------------ #

    def _on_set_state(self, state: str) -> None:
        self._hud.set_state(state)
        if self._panel:
            self._panel.on_state_change(state)

    def _on_show_text(self, user_text: str, jarvis_text: str) -> None:
        if self._panel is None:
            self._create_panel()
        self._panel.show_exchange(user_text, jarvis_text)

    def _on_rms(self, rms: float) -> None:
        self._hud.rms_value = rms

    def _on_meeting(self, active: bool) -> None:
        self._hud._meeting_live = active

    def set_rms(self, rms: float) -> None:
        self._hud.rms_value = rms

    # ------------------------------------------------------------------ #
    #  Conversation panel                                                  #
    # ------------------------------------------------------------------ #

    def _create_panel(self) -> None:
        try:
            from ui.conversation_panel import ConversationPanel
            self._panel = ConversationPanel(parent_hud=self)
        except Exception as e:
            print(f"[HUD] Panel init error: {e}")
            self._panel = None

    # ------------------------------------------------------------------ #
    #  Dragging                                                            #
    # ------------------------------------------------------------------ #

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            pos = event.position()
            cx  = self.width()  / 2
            cy  = self.height() / 2
            dx  = pos.x() - cx
            dy  = pos.y() - cy
            dist = (dx * dx + dy * dy) ** 0.5
            # Mute indicator: small zone below center text, inside circle
            mute_hit = (abs(dx) <= 22 and (cy + 18) <= pos.y() <= (cy + 34))
            if mute_hit:
                self.signals.toggle_mute.emit()
            elif dist <= 62:
                # Center circle click → toggle persona
                self.signals.toggle_persona.emit()
            else:
                self._dragging = True
                self._drag_offset = pos

    def mouseMoveEvent(self, event) -> None:
        if self._dragging:
            new_pos = self.pos() + (event.position() - self._drag_offset).toPoint()
            self.move(new_pos)
            if self._panel:
                self._panel.follow_hud()

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._dragging = False

    def mouseDoubleClickEvent(self, event) -> None:
        self._snap_to_corner()
        if self._panel:
            self._panel.follow_hud()
