"""ui/boot_sequence.py — Cinematic JARVIS boot  v4.0  (Infinity War arc reactor)

Phases
──────
  0.0 – 0.6 s : Black fade-in
  0.6 – 2.6 s : Arc reactor powers up  (crimson → purple → electric blue)
  2.6 – 6.0 s : System status typewriter on the right
  6.0 – 7.2 s : Fade out → HUD materialises
"""

import math
import random
import time
from typing import Callable, Optional

from PyQt6.QtCore import Qt, QTimer, QRectF, QPointF
from PyQt6.QtGui import (
    QColor, QPainter, QPen, QBrush, QFont,
    QRadialGradient, QLinearGradient, QPolygonF,
)
from PyQt6.QtWidgets import QApplication, QWidget

from ui import theme


# ── Colour palette ────────────────────────────────────────────────── #
_RED   = (220,  20,  40)   # crimson arc
_PURP  = (130,   0, 210)   # mid-charge purple
_BLUE  = (  0, 200, 255)   # full power blue

def _lerp3(a, b, t):
    return tuple(int(a[i] + (b[i] - a[i]) * t) for i in range(3))

def _power_rgb(progress: float) -> tuple:
    """Crimson (0) → Purple (0.5) → Electric-blue (1)."""
    p = max(0.0, min(1.0, progress))
    if p < 0.5:
        return _lerp3(_RED, _PURP, p / 0.5)
    return _lerp3(_PURP, _BLUE, (p - 0.5) / 0.5)

def _pcolor(progress: float, alpha: float = 1.0) -> QColor:
    r, g, b = _power_rgb(progress)
    c = QColor(r, g, b)
    c.setAlphaF(min(1.0, max(0.0, alpha)))
    return c


class BootSequence(QWidget):
    """Full-screen cinematic boot animation — Infinity War arc reactor."""

    _STATUS_LINES = [
        ("NEURAL LANGUAGE ENGINE",    "ONLINE"),
        ("AUDIO SUBSYSTEMS",          "ONLINE"),
        ("ECHO CANCELLATION",         "ACTIVE"),
        ("LANGUAGE MODEL",            "CONNECTED"),
        ("LOCAL FALLBACK",            "STANDBY"),
        ("NOISE CANCELLATION",        "ACTIVE"),
        ("MEMORY ENGINE",             "{memory_facts} FACTS"),
        ("WAKE DETECTION",            "ARMED"),
        ("EMOTION AWARENESS",         "CALIBRATING"),
        ("PREDICTIVE ENGINE",         "{patterns} PATTERNS"),
        ("─" * 42,                    ""),
        ("ALL SYSTEMS",               "NOMINAL"),
    ]

    def __init__(
        self,
        on_complete: Optional[Callable] = None,
        memory_facts: int = 0,
        patterns: int = 0,
    ):
        super().__init__()
        self._on_complete   = on_complete
        self._memory_facts  = memory_facts
        self._patterns      = patterns

        self._phase            = 0
        self._t0               = time.monotonic()
        self._t_global         = 0.0
        self._reactor_progress = 0.0
        self._reactor_rotation = 0.0
        self._bg_opacity       = 0.0
        self._scan_y           = 0.0

        self._line_idx         = 0
        self._char_idx         = 0
        self._completed_lines: list[tuple[str, str]] = []
        self._current_label    = ""
        self._current_value    = ""
        self._current_chars    = 0

        self._particles: list[list] = []
        self._init_particles()

        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        screen = QApplication.primaryScreen()
        if screen:
            self.setGeometry(screen.geometry())
        self.showFullScreen()

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(16)

    def _init_particles(self) -> None:
        for _ in range(60):
            self._particles.append([
                random.uniform(0, 1),
                random.uniform(0, 1),
                random.uniform(0.001, 0.004),
                random.uniform(0.3, 1.0),
            ])

    # ── Tick ──────────────────────────────────────────────────────── #

    def _tick(self) -> None:
        self._t_global = time.monotonic() - self._t0
        t = self._t_global

        if t < 0.6:
            self._bg_opacity = t / 0.6
            self._phase = 0
        elif t < 2.6:
            self._bg_opacity = 1.0
            self._phase = 1
            self._reactor_progress = min(1.0, (t - 0.6) / 2.0)
            self._reactor_rotation = (t - 0.6) * 55
        elif t < 6.0:
            self._phase = 2
            self._reactor_progress = 1.0
            self._reactor_rotation = (t - 0.6) * 55
            self._advance_text(t)
        elif t < 7.2:
            self._phase = 3
            fade = (t - 6.0) / 1.2
            self.setWindowOpacity(max(0.0, 1.0 - fade))
            if fade >= 0.97:
                self._finish()
                return

        for p in self._particles:
            p[1] += p[2]
            if p[1] > 1.1:
                p[1] = -0.05

        self._scan_y = (self._scan_y + 0.003) % 1.05
        self.update()

    def _advance_text(self, t: float) -> None:
        text_elapsed = t - 2.6
        _CHAR_MS = 13
        _LINE_MS = 270
        target_line = min(
            int(text_elapsed * 1000 / _LINE_MS),
            len(self._STATUS_LINES) - 1,
        )
        if target_line > self._line_idx:
            if self._line_idx < len(self._STATUS_LINES):
                lbl, val = self._STATUS_LINES[self._line_idx]
                val = val.replace("{memory_facts}", str(self._memory_facts))
                val = val.replace("{patterns}", str(self._patterns))
                self._completed_lines.append((lbl, val))
            self._line_idx  = target_line
            self._char_idx  = 0
        if self._line_idx < len(self._STATUS_LINES):
            lbl, val = self._STATUS_LINES[self._line_idx]
            val = val.replace("{memory_facts}", str(self._memory_facts))
            val = val.replace("{patterns}", str(self._patterns))
            line_start = text_elapsed * 1000 - self._line_idx * _LINE_MS
            self._char_idx      = min(int(line_start / _CHAR_MS), len(lbl + val))
            self._current_label = lbl
            self._current_value = val
            self._current_chars = self._char_idx

    def _finish(self) -> None:
        self._timer.stop()
        self.close()
        if self._on_complete:
            self._on_complete()

    # ── Paint ─────────────────────────────────────────────────────── #

    def paintEvent(self, event) -> None:
        try:
            self._paint_safe()
        except Exception:
            pass

    def _paint_safe(self) -> None:
        w, h = self.width(), self.height()
        if w < 100 or h < 100:
            return

        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)

        # Background
        bg = QColor(4, 4, 12)
        bg.setAlphaF(self._bg_opacity)
        p.fillRect(0, 0, w, h, bg)
        if self._bg_opacity < 0.05:
            p.end()
            return

        # Particles (tiny falling data bits in current power colour)
        self._draw_particles(p, w, h)

        # Scan line
        sy = int(self._scan_y * h)
        sc = _pcolor(self._reactor_progress, 0.06)
        p.setPen(QPen(sc, 1))
        p.drawLine(0, sy, w, sy)

        # Reactor centre: left 42% of screen
        rx = w * 0.22
        ry = h * 0.50

        if self._phase >= 1:
            self._draw_iw_reactor(p, rx, ry, self._reactor_progress,
                                  self._reactor_rotation)

        # Divider + text: right 58%
        if self._phase >= 2:
            div_x = int(w * 0.42)
            div_c = _pcolor(self._reactor_progress, 0.18)
            p.setPen(QPen(div_c, 1))
            p.drawLine(div_x, int(h * 0.08), div_x, int(h * 0.92))
            self._draw_boot_text(p, w, h)

        p.end()

    # ── Particles ─────────────────────────────────────────────────── #

    def _draw_particles(self, p: QPainter, w: int, h: int) -> None:
        p.setPen(Qt.PenStyle.NoPen)
        for pt in self._particles:
            x = int(pt[0] * w)
            y = int(pt[1] * h)
            col = _pcolor(self._reactor_progress, pt[3] * 0.25)
            p.setBrush(QBrush(col))
            p.drawRect(x, y, 1, 3)

    # ── Infinity War Arc Reactor ───────────────────────────────────── #

    def _draw_iw_reactor(
        self, p: QPainter, cx: float, cy: float,
        progress: float, rotation: float,
    ) -> None:
        t     = self._t_global
        pulse = 0.5 + 0.5 * math.sin(t * 7.0)
        surge = 0.0
        if 0.55 < progress < 0.80:
            surge = math.sin((progress - 0.55) / 0.25 * math.pi)
        if progress >= 1.0:
            surge = 0.18 + 0.12 * pulse

        # ── Ambient glow ──────────────────────────────────────────── #
        glow_r = 200 + 50 * progress + 30 * surge
        grad   = QRadialGradient(QPointF(cx, cy), glow_r)
        r, g, b = _power_rgb(progress)
        grad.setColorAt(0.00, QColor(r, g, b, int(160 * progress)))
        grad.setColorAt(0.30, QColor(r // 2, g // 2, b, int(60 * progress)))
        grad.setColorAt(1.00, QColor(0, 0, 0, 0))
        p.fillRect(int(cx - glow_r), int(cy - glow_r),
                   int(glow_r * 2), int(glow_r * 2), QBrush(grad))

        p.save()
        p.translate(cx, cy)
        p.setBrush(Qt.BrushStyle.NoBrush)

        # ── 6 outer geometric panels (IW hexagonal-ish outer ring) ── #
        # Each panel: trapezoid shape, appears 0.08 apart in progress
        self._draw_outer_panels(p, progress, rotation, surge)

        # ── Outer connector ring (segmented, like circuit traces) ──── #
        self._draw_connector_ring(p, progress, rotation, surge)

        # ── 3 Y-arm spokes from ring to inner triangle ─────────────── #
        self._draw_spokes(p, progress, rotation, surge)

        # ── Inner rings ───────────────────────────────────────────── #
        for radius, width, delay in ((75, 1.4, 0.38), (52, 1.8, 0.50)):
            lp = max(0.0, min(1.0, (progress - delay) / 0.22))
            if lp <= 0:
                continue
            c = _pcolor(progress, 0.35 + 0.50 * lp + surge * 0.15)
            glow = _pcolor(progress, 0.08 * lp)
            p.setPen(QPen(glow, width + 6))
            p.drawEllipse(QRectF(-radius, -radius, radius * 2, radius * 2))
            p.setPen(QPen(c, width))
            p.drawEllipse(QRectF(-radius, -radius, radius * 2, radius * 2))

        # ── Inverted triangle (Infinity War centre detail) ──────────── #
        self._draw_center_triangle(p, progress, rotation, surge)

        # ── Discharge rays (appear when fully powered) ──────────────── #
        self._draw_discharge_rays(p, progress, rotation, surge, t)

        # ── Bright core ───────────────────────────────────────────── #
        core_r = 8 + 14 * min(1.0, progress / 0.5) + 6 * surge
        core   = QRadialGradient(QPointF(0, 0), core_r)
        core.setColorAt(0.00, QColor(255, 255, 255, int(250 * min(1.0, progress * 3))))
        r2, g2, b2 = _power_rgb(progress)
        core.setColorAt(0.45, QColor(r2, g2, b2, int(200 * progress)))
        core.setColorAt(1.00, QColor(r2 // 3, g2 // 3, b2, 0))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(core))
        p.drawEllipse(QRectF(-core_r, -core_r, core_r * 2, core_r * 2))

        # ── Power label + progress bar ─────────────────────────────── #
        p.setBrush(Qt.BrushStyle.NoBrush)
        lbl_c = _pcolor(progress, 0.75)
        p.setPen(lbl_c)
        p.setFont(QFont(theme.FONT_FALLBACK, 9))
        p.drawText(
            QRectF(-76, 158, 152, 18),
            Qt.AlignmentFlag.AlignCenter,
            f"ARC REACTOR  {int(progress * 100):03d}%",
        )
        # Track
        bar_w = 140
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(QColor(18, 8, 28, 180)))
        p.drawRoundedRect(int(-bar_w / 2), 180, bar_w, 4, 2, 2)
        # Fill — red to blue
        fill = _pcolor(progress, 0.90)
        p.setBrush(QBrush(fill))
        p.drawRoundedRect(int(-bar_w / 2), 180, int(bar_w * progress), 4, 2, 2)

        p.restore()

    # ── Sub-draw helpers ──────────────────────────────────────────── #

    def _draw_outer_panels(
        self, p: QPainter, progress: float, rotation: float, surge: float
    ) -> None:
        """6 trapezoidal panels at 0°,60°,...330° — the IW outer ring pieces."""
        n       = 6
        r_outer = 128
        r_inner = 102
        half_a  = math.radians(16)   # half angular width
        for i in range(n):
            panel_p = max(0.0, min(1.0, (progress - i * 0.07) / 0.18))
            if panel_p <= 0:
                continue
            ca = math.radians(rotation + i * 60)
            # Trapezoid: wider at outside, narrower at inside, with angled cuts
            pts = QPolygonF([
                QPointF(math.cos(ca - half_a) * r_inner,
                        math.sin(ca - half_a) * r_inner),
                QPointF(math.cos(ca - half_a * 1.25) * r_outer,
                        math.sin(ca - half_a * 1.25) * r_outer),
                QPointF(math.cos(ca + half_a * 1.25) * r_outer,
                        math.sin(ca + half_a * 1.25) * r_outer),
                QPointF(math.cos(ca + half_a) * r_inner,
                        math.sin(ca + half_a) * r_inner),
            ])
            fill = _pcolor(progress, 0.12 * panel_p + surge * 0.06)
            edge = _pcolor(progress, 0.80 * panel_p + surge * 0.15)
            p.setBrush(QBrush(fill))
            p.setPen(QPen(edge, 1.3))
            p.drawPolygon(pts)

            # Inner accent line on each panel
            acc = _pcolor(progress, 0.50 * panel_p)
            p.setPen(QPen(acc, 0.8))
            r_acc = r_inner + 10
            p.drawArc(
                QRectF(-r_acc, -r_acc, r_acc * 2, r_acc * 2),
                int((math.degrees(ca) - 12) * 16),
                int(24 * 16),
            )

    def _draw_connector_ring(
        self, p: QPainter, progress: float, rotation: float, surge: float
    ) -> None:
        """Segmented ring at r=100, with 6 gaps between panels."""
        r       = 100
        ring_p  = max(0.0, min(1.0, (progress - 0.30) / 0.25))
        if ring_p <= 0:
            return
        # Draw 6 arcs (one per gap between panels), each 20° wide
        for i in range(6):
            ca = rotation + i * 60 + 30     # midpoint of gap
            c  = _pcolor(progress, 0.55 * ring_p + surge * 0.20)
            gw = _pcolor(progress, 0.12 * ring_p)
            p.setPen(QPen(gw, 5))
            p.drawArc(QRectF(-r, -r, r * 2, r * 2),
                      int((ca - 8) * 16), int(16 * 16))
            p.setPen(QPen(c, 1.6))
            p.drawArc(QRectF(-r, -r, r * 2, r * 2),
                      int((ca - 8) * 16), int(16 * 16))

    def _draw_spokes(
        self, p: QPainter, progress: float, rotation: float, surge: float
    ) -> None:
        """3 Y-arms connecting inner ring to the triangle."""
        spoke_p = max(0.0, min(1.0, (progress - 0.42) / 0.22))
        if spoke_p <= 0:
            return
        r_start = 50
        r_end   = 88
        for i in range(3):
            a = math.radians(rotation * 0.5 + i * 120 - 90)
            # Main spoke
            c = _pcolor(progress, 0.65 * spoke_p + surge * 0.20)
            gw = _pcolor(progress, 0.14 * spoke_p)
            x1 = math.cos(a) * r_start
            y1 = math.sin(a) * r_start
            x2 = math.cos(a) * r_end
            y2 = math.sin(a) * r_end
            p.setPen(QPen(gw, 5))
            p.drawLine(QPointF(x1, y1), QPointF(x2, y2))
            p.setPen(QPen(c, 1.8))
            p.drawLine(QPointF(x1, y1), QPointF(x2, y2))
            # Small notch/connector at the end
            p.setPen(QPen(c, 1.2))
            p.drawEllipse(QRectF(x2 - 3, y2 - 3, 6, 6))

    def _draw_center_triangle(
        self, p: QPainter, progress: float, rotation: float, surge: float
    ) -> None:
        """IW-style inverted triangle in the center."""
        tri_p = max(0.0, min(1.0, (progress - 0.55) / 0.25))
        if tri_p <= 0:
            return
        r     = 30 + 4 * surge
        # Inverted (pointing down) — IW signature
        pts   = [
            QPointF(math.cos(math.radians(90  + rotation * 0.15)) * r,
                    math.sin(math.radians(90  + rotation * 0.15)) * r),
            QPointF(math.cos(math.radians(210 + rotation * 0.15)) * r,
                    math.sin(math.radians(210 + rotation * 0.15)) * r),
            QPointF(math.cos(math.radians(330 + rotation * 0.15)) * r,
                    math.sin(math.radians(330 + rotation * 0.15)) * r),
        ]
        # Glow fill
        fill = _pcolor(progress, 0.14 * tri_p + surge * 0.08)
        edge = _pcolor(progress, 0.85 * tri_p + surge * 0.12)
        p.setBrush(QBrush(fill))
        p.setPen(QPen(edge, 1.8))
        p.drawPolygon(QPolygonF(pts))
        # Inner glow fill
        r2 = r * 0.55
        inner_pts = [
            QPointF(math.cos(math.radians(90  + rotation * 0.15)) * r2,
                    math.sin(math.radians(90  + rotation * 0.15)) * r2),
            QPointF(math.cos(math.radians(210 + rotation * 0.15)) * r2,
                    math.sin(math.radians(210 + rotation * 0.15)) * r2),
            QPointF(math.cos(math.radians(330 + rotation * 0.15)) * r2,
                    math.sin(math.radians(330 + rotation * 0.15)) * r2),
        ]
        fill2 = _pcolor(progress, 0.28 * tri_p)
        p.setBrush(QBrush(fill2))
        p.setPen(Qt.PenStyle.NoPen)
        p.drawPolygon(QPolygonF(inner_pts))

    def _draw_discharge_rays(
        self, p: QPainter,
        progress: float, rotation: float, surge: float, t: float,
    ) -> None:
        """Radial energy discharge once past 65% charge."""
        ray_p = max(0.0, min(1.0, (progress - 0.65) / 0.20))
        if ray_p <= 0:
            return
        for i in range(24):
            wobble = math.sin(t * 8.5 + i * 1.61)
            a      = math.radians(i * 15 + rotation * 0.38)
            inner  = 132 + 3 * wobble
            outer  = inner + (18 + 40 * surge + 10 * (0.5 + 0.5 * math.sin(t * 5 + i))) * ray_p
            c      = _pcolor(progress, 0.04 + 0.20 * ray_p * abs(wobble))
            p.setPen(QPen(c, 0.9))
            p.drawLine(
                QPointF(math.cos(a) * inner, math.sin(a) * inner),
                QPointF(math.cos(a) * outer, math.sin(a) * outer),
            )

    # ── Boot text ─────────────────────────────────────────────────── #

    def _draw_boot_text(self, p: QPainter, w: int, h: int) -> None:
        block_x = int(w * 0.46)
        text_w  = int(w * 0.51)
        block_y = int(h * 0.26)

        # Title: J.A.R.V.I.S
        pr = self._reactor_progress
        title_c = _pcolor(pr, 0.92)
        tf = QFont(theme.FONT_FALLBACK, 20)
        tf.setWeight(QFont.Weight.Bold)
        tf.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, 3)
        p.setFont(tf)
        p.setPen(title_c)
        p.drawText(block_x, block_y, "J.A.R.V.I.S")

        # Sub-title
        sub_c = QColor(theme.MUTED_STEEL)
        sub_c.setAlpha(180)
        p.setPen(sub_c)
        p.setFont(QFont(theme.FONT_FALLBACK, 9))
        p.drawText(block_x, block_y + 22,
                   "STARK INDUSTRIES  ·  CORE BOOT SEQUENCE  v4.0")

        # Separator line in power colour
        sep_c = _pcolor(pr, 0.45)
        p.setPen(QPen(sep_c, 1))
        p.drawLine(block_x, block_y + 34, block_x + text_w - 20, block_y + 34)

        # Completed status lines
        p.setFont(QFont(theme.FONT_FALLBACK, 10))
        y = block_y + 54
        for lbl, val in self._completed_lines:
            self._draw_status_line(p, block_x, text_w, y, lbl, val, pr)
            y += 22

        # Currently typing line with blinking cursor
        if self._line_idx < len(self._STATUS_LINES):
            lbl = self._current_label
            val = self._current_value
            full = lbl + (f"  {val}" if val else "")
            shown = full[:min(self._current_chars, len(full))]
            blink = (int(self._t_global * 4) % 2 == 0)
            tc = QColor(theme.GHOST_WHITE)
            tc.setAlpha(210)
            p.setPen(tc)
            p.drawText(block_x, y, shown + ("|" if blink else " "))

    def _draw_status_line(
        self, p: QPainter, x: int, text_w: int, y: int,
        label: str, value: str, progress: float,
    ) -> None:
        if not label:
            return

        if label.startswith("─"):
            sep_c = _pcolor(progress, 0.30)
            p.setPen(QPen(sep_c, 1))
            p.drawLine(x, y - 4, x + text_w - 20, y - 4)
            return

        val_col  = x + int(text_w * 0.60)
        tick_col = x + int(text_w * 0.87)

        lbl_c = QColor(theme.GHOST_WHITE)
        lbl_c.setAlpha(195)
        p.setPen(lbl_c)
        p.drawText(x, y, label)

        if value:
            vc = _pcolor(progress, 0.90)
            p.setPen(vc)
            p.drawText(val_col, y, value)

            ok_c = QColor(theme.SUIT_GREEN)
            ok_c.setAlpha(220)
            p.setPen(QPen(ok_c, 1.5, Qt.PenStyle.SolidLine,
                          Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
            cy = y - 7
            p.drawLine(tick_col,      cy + 4,
                       tick_col + 4,  cy + 8)
            p.drawLine(tick_col + 4,  cy + 8,
                       tick_col + 11, cy)
