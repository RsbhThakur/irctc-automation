"""
IRCTC Train Ticket Booking - Browser Automation Engine
Uses Playwright with real Microsoft Edge / Chrome browser.
A real browser naturally handles Akamai bot protection  no cookie
hacks or TLS impersonation needed.

Provides helper methods for clicking, typing, screenshots, popup
dismissal, and API response interception.
"""

import time
import os
import random
from pathlib import Path
from typing import Optional, Any

from src.utils import log, warn, error, debug, error_with_trace


class BrowserEngine:
    """
    Manages a real browser instance for IRCTC automation.
    Tries Microsoft Edge  Google Chrome  bundled Chromium in that order.
    """

    BASE_URL = "https://www.irctc.co.in"

    def __init__(self, headless: bool = False, slow_mo: int = 100):
        """
        Args:
            headless: Run browser without a visible window (NOT recommended
                      for IRCTC  headed mode needed to bypass Akamai).
            slow_mo:  Extra milliseconds between every Playwright action
                      (simulates human speed and helps avoid detection).
        """
        self.headless = headless
        self.slow_mo = slow_mo
        self.playwright = None
        self.browser = None
        self.context = None
        self.page = None
        self._page_closed = False
        self._intercepted: dict[str, dict] = {}
        self._screenshots_dir = Path("screenshots")
        self._screenshots_dir.mkdir(exist_ok=True)

    #  Lifecycle 

    def launch(self) -> bool:
        """Launch Edge/Chrome with stealth patches and persistent profile."""
        try:
            from playwright.sync_api import sync_playwright
            from playwright_stealth import Stealth

            # Stealth config  all evasions enabled
            stealth = Stealth(
                navigator_webdriver=True,
                chrome_app=True,
                chrome_csi=True,
                chrome_load_times=True,
                chrome_runtime=True,
                navigator_plugins=True,
                navigator_vendor=True,
                navigator_permissions=True,
                navigator_languages=True,
                navigator_platform=True,
                navigator_hardware_concurrency=True,
                navigator_user_agent=True,
                webgl_vendor=True,
                iframe_content_window=True,
                media_codecs=True,
                hairline=True,
            )

            self.playwright = sync_playwright().start()

            # Apply stealth patches to the Playwright instance
            stealth.hook_playwright_context(self.playwright)

            browser_type = self.playwright.chromium

            launch_args = [
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--disable-infobars",
                "--disable-popup-blocking",
            ]

            # Persistent profile directory keeps Akamai cookies across runs
            # Use local temp path to avoid OneDrive sync conflicts
            local_profile = Path(os.environ.get("LOCALAPPDATA", os.environ.get("TEMP", "."))) / "irctc_browser_profile"
            local_profile.mkdir(parents=True, exist_ok=True)
            profile_dir = str(local_profile)

            # Try persistent context (Edge  Chrome  Chromium)
            for channel, label in [("msedge", "Edge"), ("chrome", "Chrome"), (None, "Chromium")]:
                try:
                    kw = dict(
                        user_data_dir=profile_dir,
                        headless=self.headless,
                        slow_mo=self.slow_mo,
                        args=launch_args,
                        viewport={"width": 1366, "height": 768},
                        locale="en-US",
                        timezone_id="Asia/Kolkata",
                        color_scheme="light",
                    )
                    if channel:
                        kw["channel"] = channel
                    self.context = browser_type.launch_persistent_context(**kw)
                    self.browser = None
                    log(f"Launched {label} browser (stealth + persistent profile)")
                    break
                except Exception as exc:
                    debug(f"{label} not available: {exc}")
            else:
                error("No suitable browser found  install Edge, Chrome, or run: python -m playwright install chromium")
                return False

            # Use the first (default) page or create one
            pages = self.context.pages
            self.page = pages[0] if pages else self.context.new_page()
            self.page.set_default_timeout(20_000)

            # Intercept API responses for data extraction
            self.page.on("response", self._on_response)

            # Track page/tab lifecycle
            self.page.on("close", self._on_page_close)
            self.context.on("page", self._on_new_page)

            log("Browser engine ready")
            return True

        except Exception as e:
            error_with_trace(f"Failed to launch browser: {e}", e)
            return False

    def close(self):
        """Close browser and clean up Playwright."""
        try:
            if self.context:
                self.context.close()
            if self.browser:
                self.browser.close()
            if self.playwright:
                self.playwright.stop()
            log("Browser closed")
        except Exception:
            pass

    #  Response Interception 

    _INTERCEPT_KEYWORDS = [
        "webtoken", "validateUser", "loginCaptcha",
        "altAvlEnq", "avlFarenquiry", "boardingStationEnq",
        "allLapAvlFareEnq", "captchaverify",
        "bookingInitPayment", "verifyPayment", "textToNumber",
    ]

    def _on_response(self, response):
        """Silently capture API responses matching known endpoints."""
        url = response.url
        for kw in self._INTERCEPT_KEYWORDS:
            if kw in url:
                try:
                    body = response.text()
                    self._intercepted[kw] = {
                        "status": response.status,
                        "url": url,
                        "body": body,
                    }
                    debug(f"Intercepted API: {kw}  {response.status}")
                except Exception:
                    pass
                break

    def _on_page_close(self):
        """Called when the tracked page is closed."""
        warn("Page was closed!")
        self._page_closed = True

    def _on_new_page(self, page):
        """Called when a new tab/popup opens in the browser context."""
        debug(f"New page opened: {page.url}")
        # If our main page closed, adopt the new one
        if self._page_closed:
            log("Switching to new page after original closed")
            self.page = page
            self._page_closed = False
            self.page.on("response", self._on_response)
            self.page.on("close", self._on_page_close)

    @property
    def page_alive(self) -> bool:
        """True if the current page reference is still usable."""
        if self._page_closed:
            return False
        try:
            _ = self.page.url
            return True
        except Exception:
            self._page_closed = True
            return False

    def get_intercepted(self, keyword: str) -> Optional[dict]:
        return self._intercepted.get(keyword)

    def clear_intercepted(self, keyword: str = None):
        if keyword:
            self._intercepted.pop(keyword, None)
        else:
            self._intercepted.clear()

    #  Navigation 

    def goto(self, url: str, wait_until: str = "domcontentloaded",
             timeout: int = 60_000) -> bool:
        """Navigate to *url*. Returns True if page is usable."""
        try:
            debug(f"Navigating  {url}")
            self.page.goto(url, wait_until=wait_until, timeout=timeout)
            debug(f"Page loaded: {self.page.url}")
            return True
        except Exception as e:
            warn(f"Navigation issue (page may still work): {e}")
            return self.page.url != "about:blank"

    def wait_for_url(self, fragment: str, timeout: int = 30_000) -> bool:
        """Wait until the URL contains *fragment*."""
        try:
            self.page.wait_for_url(f"**{fragment}**", timeout=timeout)
            return True
        except Exception:
            debug(f"URL fragment not matched: {fragment}")
            return False

    def wait_for_load(self, state: str = "networkidle",
                      timeout: int = 30_000) -> bool:
        try:
            self.page.wait_for_load_state(state, timeout=timeout)
            return True
        except Exception:
            return False

    #  Element Interaction 

    def wait_and_click(self, selector: str, timeout: int = 15_000) -> bool:
        """Wait for element & click. Returns True on success."""
        try:
            self.page.wait_for_selector(selector, state="visible", timeout=timeout)
            self.page.click(selector, timeout=5000)
            debug(f"Clicked: {selector}")
            return True
        except Exception as e:
            debug(f"Click failed [{selector}]: {e}")
            return False

    def click_text(self, text: str, exact: bool = False,
                   timeout: int = 15_000) -> bool:
        """Click the **first visible** element containing *text*."""
        try:
            loc = self.page.get_by_text(text, exact=exact)
            loc.first.click(timeout=timeout)
            debug(f"Clicked text: '{text}'")
            return True
        except Exception as e:
            debug(f"Click-text failed ['{text}']: {e}")
            return False

    def click_role(self, role: str, name: str = None,
                   timeout: int = 15_000) -> bool:
        """Click element by ARIA role (and optional accessible name)."""
        try:
            loc = self.page.get_by_role(role, name=name)
            loc.first.click(timeout=timeout)
            debug(f"Clicked role={role} name='{name}'")
            return True
        except Exception as e:
            debug(f"Click-role failed [role={role}]: {e}")
            return False

    def fill_input(self, selector: str, value: str,
                   timeout: int = 10_000) -> bool:
        """Clear and fill an input."""
        try:
            el = self.page.wait_for_selector(selector, state="visible",
                                              timeout=timeout)
            el.fill("")
            el.fill(value)
            redacted = "***" if "password" in selector.lower() else value[:30]
            debug(f"Filled [{selector}]: '{redacted}'")
            return True
        except Exception as e:
            debug(f"Fill failed [{selector}]: {e}")
            return False

    def type_slowly(self, selector: str, text: str,
                    delay: int = 40) -> bool:
        """Type character-by-character into an input."""
        try:
            el = self.page.wait_for_selector(selector, state="visible",
                                              timeout=10_000)
            el.click()
            el.fill("")
            el.type(text, delay=delay)
            redacted = "***" if "password" in selector.lower() else text
            debug(f"Typed [{selector}]: '{redacted}'")
            return True
        except Exception as e:
            debug(f"Type failed [{selector}]: {e}")
            return False

    def get_text(self, selector: str) -> Optional[str]:
        try:
            return self.page.text_content(selector, timeout=5000)
        except Exception:
            return None

    def get_attribute(self, selector: str, attr: str) -> Optional[str]:
        try:
            return self.page.get_attribute(selector, attr, timeout=5000)
        except Exception:
            return None

    def is_visible(self, selector: str, timeout: int = 2000) -> bool:
        """Non-throwing visibility check."""
        try:
            self.page.wait_for_selector(selector, state="visible",
                                         timeout=timeout)
            return True
        except Exception:
            return False

    def select_dropdown_option(self, trigger_selector: str,
                               option_text: str) -> bool:
        """Open a PrimeNG dropdown and pick an option by visible text."""
        try:
            self.page.click(trigger_selector, timeout=5000)
            self.wait(500)
            # PrimeNG renders option items in an overlay panel
            self.page.get_by_text(option_text, exact=False).first.click(
                timeout=5000)
            debug(f"Dropdown [{trigger_selector}]  '{option_text}'")
            return True
        except Exception as e:
            debug(f"Dropdown failed [{trigger_selector}]: {e}")
            return False

    def wait(self, ms: int):
        """Safe wait  falls back to time.sleep if page is closed."""
        if not self.page_alive:
            time.sleep(ms / 1000)
            return
        try:
            self.page.wait_for_timeout(ms)
        except Exception:
            time.sleep(ms / 1000)

    def evaluate(self, script: str) -> Any:
        return self.page.evaluate(script)

    #  Screenshots & Debug 

    def screenshot(self, name: str = "step") -> str:
        try:
            save_screenshots = os.environ.get("SAVE_SCREENSHOTS", "0").strip().lower() in (
                "1", "true", "yes", "on"
            )
            if not save_screenshots:
                return ""
            ts = int(time.time())
            path = str(self._screenshots_dir / f"{name}_{ts}.png")
            self.page.screenshot(path=path, full_page=False)
            debug(f"Screenshot: {path}")
            return path
        except Exception as e:
            debug(f"Screenshot failed: {e}")
            return ""

    def log_page_info(self):
        try:
            debug(f"Current URL : {self.page.url}")
            debug(f"Page title  : {self.page.title()}")
        except Exception:
            pass

    #  Popup / Overlay Dismissal 

    def dismiss_popups(self):
        """
        Dismiss all common IRCTC overlays in one fast JS pass:
         iZooto push-notification prompt
         Language/alert dialogs (OK button)
         PrimeNG overlay masks
         Chat-bot widget (Disha)
        """
        try:
            removed = self.page.evaluate("""() => {
                let n = 0;
                // iZooto overlay
                const iz = document.getElementById('iz-optin-main-container');
                if (iz) { iz.remove(); n++; }
                // iZooto iframes
                document.querySelectorAll('iframe[src*="izooto"]').forEach(f => { f.remove(); n++; });
                // PrimeNG dialog masks (block pointer events)
                document.querySelectorAll('.ui-dialog-mask, .p-dialog-mask, .cdk-overlay-backdrop').forEach(m => { m.remove(); n++; });
                // Click all visible OK / Got It / CLOSE buttons
                ['OK', 'Got It', 'CLOSE', 'Later', 'Allow'].forEach(txt => {
                    document.querySelectorAll('button, a').forEach(b => {
                        if (b.offsetHeight > 0 && b.innerText && b.innerText.trim() === txt) {
                            try { b.click(); n++; } catch(e) {}
                        }
                    });
                });
                // Close PrimeNG dialog close icons
                document.querySelectorAll('.ui-dialog-titlebar-close, .p-dialog-header-close').forEach(c => {
                    if (c.offsetHeight > 0) { try { c.click(); n++; } catch(e) {} }
                });
                return n;
            }""")
            if removed:
                debug(f"Dismissed {removed} popup(s) via JS")
        except Exception as e:
            debug(f"dismiss_popups JS error: {e}")

    def force_click(self, selector: str, timeout: int = 10_000) -> bool:
        """Click via JS dispatch  bypasses overlay interception."""
        try:
            self.page.wait_for_selector(selector, state="attached", timeout=timeout)
            self.page.eval_on_selector(selector, "el => el.click()")
            debug(f"Force-clicked: {selector}")
            return True
        except Exception as e:
            debug(f"Force-click failed [{selector}]: {e}")
            return False

    #  Convenience 

    def wait_for_selector(self, selector: str, timeout: int = 15_000,
                          state: str = "visible") -> bool:
        """Wait for a selector. Returns True if found."""
        try:
            self.page.wait_for_selector(selector, state=state, timeout=timeout)
            return True
        except Exception:
            return False

    #  Human-like Behavior 

    def human_delay(self, min_ms: int = 300, max_ms: int = 1200):
        """Random human-like pause."""
        ms = random.randint(min_ms, max_ms)
        self.wait(ms)

    def random_mouse_move(self, n: int = 3):
        """Move mouse to random positions on the page to build sensor data."""
        try:
            for _ in range(n):
                x = random.randint(100, 1200)
                y = random.randint(100, 650)
                self.page.mouse.move(x, y)
                time.sleep(random.uniform(0.08, 0.25))
        except Exception:
            pass

    def random_scroll(self):
        """Small random scroll  helps build Akamai sensor data."""
        try:
            dy = random.randint(50, 200) * random.choice([1, -1])
            self.page.mouse.wheel(0, dy)
            time.sleep(random.uniform(0.1, 0.3))
        except Exception:
            pass

    def warm_up(self, seconds: int = 2):
        """Brief human activity for Akamai sensors."""
        debug(f"Warming up {seconds}s...")
        end = time.time() + seconds
        while time.time() < end:
            self.random_mouse_move(1)
            if random.random() < 0.3:
                self.random_scroll()
            time.sleep(random.uniform(0.15, 0.4))

    #  Page Recovery 

    def recover_page(self) -> bool:
        """If page died, open a new one in the existing context."""
        if self.page_alive:
            return True
        try:
            self.page = self.context.new_page()
            self._page_closed = False
            self.page.set_default_timeout(20_000)
            self.page.on("response", self._on_response)
            self.page.on("close", self._on_page_close)
            log("Recovered with new page")
            return True
        except Exception as e:
            error(f"Page recovery failed: {e}")
            return False


