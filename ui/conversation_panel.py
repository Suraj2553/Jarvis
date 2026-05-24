"""ui/conversation_panel.py — Sliding conversation panel below the HUD.

Materializes below the HUD when JARVIS speaks or listens.
Shows user transcript (live) and JARVIS response (types in at speech speed).
Auto-disappears 18 seconds after JARVIS finishes speaking.
"""

import threading
import time
from typing import Optional

from PyQt6.QtCore import Qt, QTimer, QPropertyAnimation, QEasingCurve, QRectF
from PyQt6.QtGui import QColor, QPainter, QPen, QBrush, QFont, QLinearGradient
from PyQt6.QtWidgets import QWidget, QApplication

from ui import theme


class ConversationPanel(QWidget):
    """Glassmorphism conversation panel that slides from HUD."""

    _TYPE_SPEED_MS = 45  # ~14 chars/sec @ 165wpm
    _AUTO_HIDE_S = 18

    def __init__(self, parent_hud=None):
        super().__init__(None)  # no parent — separate window
        self._hud = parent_hud
        self._user_text = ""
        self._jarvis_text = ""
        self._displayed_jarvis = ""  # currently typed out portion

        self._visible = False
        self._type_idx = 0
        self._hide_timer = QTimer(self)
        self._hide_timer.setSingleShot(True)
        self._hide_timer.timeout.connect(self._slide_hide)

        self._type_timer = QTimer(self)
        self._type_timer.timeout.connect(self._type_next_char)
        self._type_timer.setInterval(self._TYPE_SPEED_MS)

        # Window flags: frameless, always on top, no taskbar entry
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)

        self.setFixedWidth(theme.PANEL_WIDTH)
        self._update_height()
        self._reposition()

    # ------------------------------------------------------------------ #
    #  Public API                                                          #
    # ------------------------------------------------------------------ #

    def show_exchange(self, user_text: str, jarvis_text: str) -> None:
        """Update the panel with new exchange. Slides in if hidden."""
        self._user_text = user_text[:120]
        self._jarvis_text = jarvis_text
        self._displayed_jarvis = ""
        self._type_idx = 0

        # Cancel any pending hide
        self._hide_timer.stop()

        # Start typewriter effect
        self._type_timer.start()

        if not self._visible:
            self._slide_show()

        self.update()

    def on_state_change(self, state: str) -> None:
        if state == "idle":
            # Schedule auto-hide
            self._hide_timer.start(self._AUTO_HIDE_S * 1000)

    def follow_hud(self) -> None:
        """Reposition when HUD moves."""
        self._reposition()

    # ------------------------------------------------------------------ #
    #  Typewriter effect                                                   #
    # ------------------------------------------------------------------ #

    def _type_next_char(self) -> None:
        if self._type_idx < len(self._jarvis_text):
            self._type_idx += 1
            self._displayed_jarvis = self._jarvis_text[:self._type_idx]
            self._update_height()
            self.update()
        else:
            self._type_timer.stop()

    # ------------------------------------------------------------------ #
    #  Visibility animations                                               #
    # ------------------------------------------------------------------ #

    def _slide_show(self) -> None:
        self._visible = True
        self._reposition()
        self.show()
        self.setWindowOpacity(0)
        anim = QPropertyAnimation(self, b"windowOpacity", self)
        anim.setDuration(250)
        anim.setStartValue(0.0)
        anim.setEndValue(1.0)
        anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        anim.start()

    def _slide_hide(self) -> None:
        anim = QPropertyAnimation(self, b"windowOpacity", self)
        anim.setDuration(600)
        anim.setStartValue(1.0)
        anim.setEndValue(0.0)
        anim.setEasingCurve(QEasingCurve.Type.InCubic)
        anim.finished.connect(self.hide)
        anim.start()
        self._visible = False

    # ------------------------------------------------------------------ #
    #  Layout helpers                                                      #
    # ------------------------------------------------------------------ #

    def _reposition(self) -> None:
        if not self._hud:
            return
        hud_pos = self._hud.pos()
        x = hud_pos.x() + (self._hud.width() - theme.PANEL_WIDTH) // 2
        y = hud_pos.y() + self._hud.height() + 8
        self.move(x, y)

    def _update_height(self) -> None:
        # Calculate needed height
        lines_jarvis = max(1, len(self._displayed_jarvis) // 40 + 1)
        lines_user = max(1, len(self._user_text) // 40 + 1)
        height = min(
            theme.PANEL_MAX_HEIGHT,
            24 + lines_user * 16 + 12 + lines_jarvis * 16 + 12,
        )
        self.setFixedHeight(height)

    # ------------------------------------------------------------------ #
    #  Rendering                                                           #
    # ------------------------------------------------------------------ #

    def paintEvent(self, event) -> None:
        if not self._user_text and not self._displayed_jarvis:
            return

        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)

        w = self.width()
        h = self.height()

        # Background — subtle top-to-bottom gradient (slightly lighter at top)
        bg_grad = QLinearGradient(0, 0, 0, h)
        bg_grad.setColorAt(0.0, QColor(6, 8, 22, 240))
        bg_grad.setColorAt(1.0, QColor(2, 2, 10, 245))
        p.setBrush(QBrush(bg_grad))
        border_c = QColor(theme.ARC_BLUE)
        border_c.setAlpha(100)
        p.setPen(QPen(border_c, 1))
        p.drawRoundedRect(QRectF(1, 1, w - 2, h - 2), 12, 12)

        # Left accent bar (2px)
        accent_c = QColor(theme.ARC_BLUE)
        accent_c.setAlpha(180)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(accent_c))
        p.drawRoundedRect(QRectF(1, 12, 2, h - 24), 1, 1)

        # "YOU" label
        label_c = QColor(theme.MUTED_STEEL)
        p.setPen(label_c)
        label_font = QFont(theme.FONT_FALLBACK, 7)
        label_font.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, 1.5)
        p.setFont(label_font)
        p.drawText(12, 14, "YOU")

        # User text
        user_c = QColor(theme.GHOST_WHITE)
        p.setPen(user_c)
        user_font = QFont(theme.FONT_FALLBACK, 11)
        p.setFont(user_font)
        p.drawText(QRectF(12, 16, w - 20, h // 2 - 16),
                   Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop,
                   self._user_text)

        # Divider
        mid_y = h // 2
        div_c = QColor(theme.ARC_BLUE)
        div_c.setAlpha(40)
        p.setPen(QPen(div_c, 1))
        p.drawLine(12, mid_y, w - 12, mid_y)

        # "JARVIS" label
        jarvis_label_c = QColor(theme.ARC_BLUE)
        jarvis_label_c.setAlpha(200)
        p.setPen(jarvis_label_c)
        p.setFont(label_font)
        p.drawText(12, mid_y + 12, "JARVIS")

        # JARVIS response (typewriter)
        resp_c = QColor(theme.GHOST_WHITE)
        p.setPen(resp_c)
        resp_font = QFont(theme.FONT_FALLBACK, 12)
        p.setFont(resp_font)
        p.drawText(
            QRectF(12, mid_y + 14, w - 20, h - mid_y - 16),
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop,
            self._displayed_jarvis,
        )

        p.end()
