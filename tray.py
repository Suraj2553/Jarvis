"""System tray icon using pystray.  Runs in its own daemon thread."""

import threading

from PIL import Image, ImageDraw, ImageFont
import pystray
from pystray import Menu, MenuItem


def _build_icon(size: int = 64) -> Image.Image:
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    # Dark circle
    draw.ellipse(
        [2, 2, size - 2, size - 2],
        fill=(8, 8, 24, 255),
        outline=(0, 212, 255, 200),
        width=2,
    )

    # Cyan "J" centred — try system fonts, fall back to default
    font = None
    for candidate in ("consola.ttf", "arial.ttf", "cour.ttf"):
        try:
            font = ImageFont.truetype(candidate, int(size * 0.44))
            break
        except Exception:
            continue
    if font is None:
        font = ImageFont.load_default(size=int(size * 0.44))

    draw.text(
        (size // 2, size // 2),
        "J",
        fill=(0, 212, 255, 255),
        font=font,
        anchor="mm",
    )
    return img


class SystemTray:
    def __init__(
        self,
        show_fn,
        hide_fn,
        settings_fn,
        quit_fn,
    ):
        self._show = show_fn
        self._hide = hide_fn
        self._settings = settings_fn
        self._quit = quit_fn
        self._icon: pystray.Icon | None = None

    def start(self) -> None:
        menu = Menu(
            MenuItem("Show JARVIS", lambda *_: self._show()),
            MenuItem("Hide",       lambda *_: self._hide()),
            MenuItem("Settings",   lambda *_: self._settings()),
            Menu.SEPARATOR,
            MenuItem("Quit",       lambda *_: self._quit()),
        )
        self._icon = pystray.Icon("JARVIS", _build_icon(), "JARVIS Assistant", menu)
        t = threading.Thread(target=self._icon.run, daemon=True, name="SystemTray")
        t.start()

    def stop(self) -> None:
        if self._icon:
            try:
                self._icon.stop()
            except Exception:
                pass
