"""tools/browser_control.py — Voice-driven browser control via Playwright.

Natural language → LLM maps to Playwright actions.
Requires: pip install playwright && python -m playwright install chromium
"""

import threading
from typing import Callable, Optional

try:
    from playwright.sync_api import sync_playwright as _sync_playwright
    _HAS_PLAYWRIGHT = True
except Exception:
    _sync_playwright = None
    _HAS_PLAYWRIGHT = False


_BROWSER_TRIGGER_WORDS = (
    "open", "navigate to", "go to", "search for on",
    "read this page", "scroll down", "click", "fill in",
    "download this", "go back", "stop browser",
)


def is_browser_command(text: str) -> bool:
    tl = text.lower()
    return any(w in tl for w in _BROWSER_TRIGGER_WORDS) and (
        "browser" in tl or "web" in tl or "page" in tl
        or "site" in tl or "chrome" in tl or "open" in tl
    )


class BrowserController:
    """Controls a Playwright browser via natural language commands."""

    def __init__(
        self,
        speak_fn: Optional[Callable] = None,
        hud_url_fn: Optional[Callable] = None,
        router=None,
    ):
        self._speak = speak_fn
        self._hud_url = hud_url_fn
        self._router = router

        self._playwright = None
        self._browser = None
        self._page = None
        self._active = False
        self._lock = threading.Lock()

    def start(self) -> bool:
        """Launch browser session. Returns True on success."""
        if not _HAS_PLAYWRIGHT:
            if self._speak:
                self._speak(
                    "Browser control requires Playwright. "
                    "Run: pip install playwright && python -m playwright install chromium"
                )
            return False
        try:
            self._playwright = _sync_playwright().start()
            self._browser = self._playwright.chromium.launch(headless=False)
            self._page = self._browser.new_page()
            self._active = True
            print("[BrowserControl] Playwright browser launched.")
            return True
        except Exception as e:
            print(f"[BrowserControl] Launch error: {e}")
            return False

    def stop(self) -> None:
        try:
            if self._browser:
                self._browser.close()
            if self._playwright:
                self._playwright.stop()
        except Exception:
            pass
        self._active = False
        self._page = None
        self._browser = None
        self._playwright = None

    def execute(self, command: str) -> str:
        """Execute a natural language browser command. Returns result string."""
        if not self._active:
            if not self.start():
                return "Browser control unavailable."

        tl = command.lower().strip()

        # Map common commands directly
        try:
            # Navigate
            if any(p in tl for p in ("open ", "go to ", "navigate to ")):
                url = self._extract_url(command)
                if url:
                    self._page.goto(url)
                    self._update_hud()
                    return f"Navigated to {url}"

            # Read page
            if "read this page" in tl or "read the page" in tl:
                text = self._page.inner_text("body")[:500]
                return text

            # Scroll
            if "scroll down" in tl:
                self._page.mouse.wheel(0, 500)
                return "Scrolled down."
            if "scroll up" in tl:
                self._page.mouse.wheel(0, -500)
                return "Scrolled up."

            # Go back
            if "go back" in tl:
                self._page.go_back()
                self._update_hud()
                return "Went back."

            # Stop
            if "stop browser" in tl or "close browser" in tl:
                self.stop()
                return "Browser control ended."

            # Click first result
            if "click the first result" in tl or "click first" in tl:
                self._page.click("a:first-of-type")
                self._update_hud()
                return "Clicked first result."

            # LLM-based command interpretation
            if self._router:
                return self._llm_execute(command)

        except Exception as e:
            return f"Browser command error: {str(e)[:80]}"

        return "Browser command not understood."

    def _extract_url(self, command: str) -> str:
        """Extract or construct URL from natural language."""
        import re
        # Direct URL
        url_match = re.search(r'https?://\S+', command)
        if url_match:
            return url_match.group()

        # Search intent
        for prefix in ("open ", "go to ", "navigate to "):
            if prefix in command.lower():
                target = command.lower().split(prefix, 1)[1].strip()
                # If it looks like a domain
                if "." in target and " " not in target:
                    return f"https://{target}"
                # Otherwise do a search
                query = target.replace(" ", "+")
                return f"https://www.google.com/search?q={query}"
        return ""

    def _update_hud(self) -> None:
        if self._hud_url and self._page:
            try:
                self._hud_url(self._page.url)
            except Exception:
                pass

    def _llm_execute(self, command: str) -> str:
        """Use LLM to interpret and execute complex browser commands."""
        current_url = self._page.url if self._page else "none"
        messages = [
            {"role": "system",
             "content": (
                 "You are controlling a web browser with Playwright. "
                 "The user gave a browser command. Respond with a Python Playwright "
                 "one-liner to execute it (using 'page' variable). "
                 "E.g.: page.goto('https://example.com') "
                 "Return ONLY the Python code, nothing else."
             )},
            {"role": "user",
             "content": f"Current URL: {current_url}\nCommand: {command}"},
        ]
        try:
            code = self._router.chat_sync(messages, max_tokens=100).strip()
            # Execute the generated code
            page = self._page
            exec(code)  # noqa: S102 — isolated browser context, user-directed
            self._update_hud()
            return f"Done: {code[:60]}"
        except Exception as e:
            return f"Browser action failed: {str(e)[:80]}"
