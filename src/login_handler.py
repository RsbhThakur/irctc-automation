"""
IRCTC Train Ticket Booking - Login Handler (Browser-based)
Uses the real browser to interact with the IRCTC Angular SPA login dialog.

Current IRCTC login flow (as of Feb 2026):
1. Navigate to https://www.irctc.co.in/nget/train-search
2. Dismiss iZooto notification + language popups
3. Click the LOGIN button in the header
4. Fill username + password
5. Click SIGN IN  (there is NO image captcha on the initial form 
   Google reCAPTCHA v3 runs invisibly in the background)
6. If reCAPTCHA v2 challenge pops up, wait for the user to solve it
7. Verify login success
"""

import time
import json
import re
from datetime import datetime
from typing import Optional

from src.browser_engine import BrowserEngine
from src.captcha_solver import solve_captcha          # kept for booking-review captcha
from src.utils import log, warn, error, success, debug, error_with_trace


class LoginHandler:
    """Handles browser-based IRCTC login."""

    IRCTC_URL = "https://www.irctc.co.in/nget/train-search"

    #  Selectors 

    LOGIN_BTN = [
        "a.search_btn.loginText",
        'a:has-text("LOGIN")',
        'button:has-text("LOGIN")',
        "a.loginText",
    ]

    USER_INPUT = [
        'input[formcontrolname="userid"]',
        'input[placeholder*="User Name" i]',
    ]

    PASS_INPUT = [
        'input[formcontrolname="password"]',
        'input[type="password"]',
    ]

    SIGNIN_BTN = [
        'app-login button.search_btn.train_Search',
        '.ui-dialog-content button.search_btn',
        'app-login button[type="submit"]',
        'button:has-text("SIGN IN")',
    ]

    # Optional: image captcha selectors (some IRCTC versions still show it)
    CAPTCHA_IMG = [
        ".captcha-img",
        "img.captcha-img",
        "app-captcha img",
        '.ui-dialog img[src^="data:image"]',
    ]

    CAPTCHA_INPUT = [
        'input[formcontrolname="captcha"]',
        'input[placeholder*="Captcha" i]',
        'input[name="captcha"]',
    ]

    #  Public 

    def __init__(self, engine: BrowserEngine, username: str, password: str, config: Optional[dict] = None):
        self.engine = engine
        self.username = username
        self.password = password
        self.config = config or {}

    def navigate_to_irctc(self) -> bool:
        """Open IRCTC and wait for the Angular SPA to boot."""
        log("Opening IRCTC website...")
        ok = self.engine.goto(self.IRCTC_URL, wait_until="domcontentloaded",
                              timeout=60_000)
        if not ok:
            self.engine.screenshot("nav_fail")
            return False

        # Inject auto-popup-killer that runs every 500ms
        # IMPORTANT: Skips dialogs that contain the login form (app-login)
        try:
            self.engine.page.evaluate("""() => {
                if (window._popupKiller) return;
                window._popupKiller = setInterval(() => {
                    try {
                        // iZooto
                        const iz = document.getElementById('iz-optin-main-container');
                        if (iz) iz.remove();
                        document.querySelectorAll('iframe[src*="izooto"]').forEach(f => f.remove());
                        // PrimeNG masks  only remove if NOT backing a login dialog
                        document.querySelectorAll('.ui-dialog-mask, .p-dialog-mask').forEach(m => {
                            // Check if any sibling dialog contains login form
                            const parent = m.parentElement;
                            if (parent) {
                                const loginDlg = parent.querySelector('app-login, [formcontrolname="userid"]');
                                if (loginDlg) return; // skip  this mask belongs to login
                            }
                            m.remove();
                        });
                        // OK/Close buttons  skip if inside login dialog
                        ['OK', 'Got It', 'CLOSE', 'Later', 'Allow'].forEach(txt => {
                            document.querySelectorAll('button, a').forEach(b => {
                                if (b.offsetHeight > 0 && b.innerText && b.innerText.trim() === txt) {
                                    if (b.closest('app-login, .login-container')) return;
                                    try { b.click(); } catch(e) {}
                                }
                            });
                        });
                        // Close icons  skip login dialog close buttons
                        document.querySelectorAll('.ui-dialog-titlebar-close, .p-dialog-header-close').forEach(c => {
                            if (c.offsetHeight > 0) {
                                const dlg = c.closest('.ui-dialog, .p-dialog');
                                if (dlg && dlg.querySelector('app-login, [formcontrolname="userid"]')) return;
                                try { c.click(); } catch(e) {}
                            }
                        });
                    } catch(e) {}
                }, 500);
            }""")
            debug("Injected auto-popup-killer")
        except Exception:
            pass

        # Wait briefly for Angular SPA to boot
        self.engine.wait(2000)

        # Brief warm-up for Akamai sensors
        self.engine.warm_up(seconds=2)

        self.engine.log_page_info()
        log("IRCTC page loaded")
        return True

    def login(self, max_retries: int = 5) -> bool:
        """
        Full login flow.
        The current IRCTC login form only has username + password + SIGN IN.
        Google reCAPTCHA v3 runs silentlyif a v2 challenge pops up,
        we give the user 90 s to solve it manually.
        If an image captcha appears instead, we solve it with EasyOCR.
        """
        self._wait_for_login_time_if_configured()

        if not self._open_login_dialog():
            error("Could not open login dialog")
            return False

        for attempt in range(1, max_retries + 1):
            log(f"Login attempt {attempt}/{max_retries}")
            try:
                # Recover page if previous attempt killed it
                if not self.engine.page_alive:
                    if not self.engine.recover_page():
                        error("Cannot recover browser page")
                        return False
                    # Navigate back to IRCTC and reopen login
                    if not self.navigate_to_irctc() or not self._open_login_dialog():
                        continue

                # 1. Fill username + password
                if not self._fill_credentials():
                    self.engine.screenshot(f"cred_fail_{attempt}")
                    continue

                # 2. Handle optional image captcha (some IRCTC versions)
                self._handle_image_captcha_if_present()

                self.engine.screenshot(f"pre_submit_{attempt}")

                # 3. Click SIGN IN
                if not self._click_signin():
                    continue

                # 4. Wait for reCAPTCHA / response
                self.engine.wait(1500)

                # 5. Check if page closed (IRCTC sometimes opens new tab)
                if not self.engine.page_alive:
                    warn("Page closed after SIGN IN  checking for new tab")
                    self.engine.wait(2000)
                    # page_alive may now be True if _on_new_page adopted one
                    if self.engine.page_alive:
                        log("Switched to new page")
                    else:
                        error("Browser page died after SIGN IN")
                        return False

                # 6. Check if a reCAPTCHA challenge appeared
                if self._recaptcha_challenge_visible():
                    log("reCAPTCHA challenge detected  please solve it in the browser!")
                    self._wait_for_recaptcha_completion(timeout=90)

                # 7. Wait briefly for server to process
                self.engine.wait(300)

                # 8. Evaluate result (polls webtoken API first  fast path)
                result = self._check_login_result()

                if result == "success":
                    success("Login successful!")
                    self.engine.screenshot("login_success")
                    return True
                elif result == "invalid_captcha":
                    warn("Invalid captcha  retrying")
                    continue
                elif result == "bad_credentials":
                    error("Invalid username or password!")
                    self.engine.screenshot("bad_creds")
                    return False
                else:
                    warn(f"Login issue: {result}")
                    self.engine.screenshot(f"login_issue_{attempt}")
                    self.engine.dismiss_popups()
                    continue

            except Exception as e:
                error_with_trace(f"Login attempt {attempt} error: {e}", e)
                self.engine.screenshot(f"login_err_{attempt}")
                continue

        error(f"Login failed after {max_retries} attempts")
        return False

    def _wait_for_login_time_if_configured(self):
        """If LOGIN_TIME is configured, keep refreshing and wait for IRCTC time to reach it."""
        login_time_str = str(self.config.get("LOGIN_TIME", "")).strip()
        if not login_time_str:
            return

        target = self._parse_target_time(login_time_str)
        if not target:
            warn(f"Skipping login-time gate due to invalid LOGIN_TIME='{login_time_str}'")
            return

        refresh_secs = float(self.config.get("LOGIN_REFRESH_SECONDS", 2))
        log(f"LOGIN_TIME enabled: waiting for IRCTC time {target.strftime('%H:%M:%S')}")
        refreshed_while_waiting = False

        while True:
            now_dt = self._get_irctc_screen_time()
            if now_dt:
                if now_dt.time() >= target.time():
                    log(f"IRCTC time reached target ({now_dt.strftime('%H:%M:%S')})")
                    break
                debug(f"IRCTC time {now_dt.strftime('%H:%M:%S')} < target {target.strftime('%H:%M:%S')}")
            else:
                warn("Could not read IRCTC screen time; retrying with refresh")

            # Keep the page fresh while waiting for booking window
            try:
                self.engine.page.reload(wait_until="domcontentloaded", timeout=30_000)
                refreshed_while_waiting = True
                self.engine.wait(500)
                self.engine.dismiss_popups()
            except Exception as e:
                debug(f"Refresh while waiting LOGIN_TIME failed: {e}")
                if not self.engine.page_alive:
                    self.engine.recover_page()
                    self.engine.goto(self.IRCTC_URL, wait_until="domcontentloaded", timeout=60_000)

            self.engine.wait(int(refresh_secs * 1000))

        # Final refresh only if we actually waited/reloaded in this gate.
        if refreshed_while_waiting:
            try:
                self.engine.page.reload(wait_until="domcontentloaded", timeout=30_000)
                self.engine.wait(500)
                self.engine.dismiss_popups()
            except Exception:
                pass

    def _parse_target_time(self, s: str) -> Optional[datetime]:
        s = s.strip()
        for fmt in ("%H:%M:%S", "%H:%M"):
            try:
                t = datetime.strptime(s, fmt).time()
                return datetime.now().replace(
                    hour=t.hour, minute=t.minute, second=t.second, microsecond=0
                )
            except ValueError:
                continue
        return None

    def _get_irctc_screen_time(self) -> Optional[datetime]:
        """
        Read the current IRCTC time from:
        1) visible page clock text (preferred)
        2) intercepted textToNumber API payload
        3) fallback to local clock
        """
        # 1) Try visible clock-like text
        try:
            time_str = self.engine.page.evaluate("""() => {
                const candidates = [];
                const selectors = [
                    '#clock', '.clock', '[id*="time" i]', '[class*="time" i]',
                    'span', 'div', 'p'
                ];
                for (const sel of selectors) {
                    for (const el of document.querySelectorAll(sel)) {
                        if (!el || el.offsetHeight <= 0) continue;
                        const txt = (el.innerText || '').trim();
                        if (txt && /\\b\\d{2}:\\d{2}(:\\d{2})?\\b/.test(txt)) {
                            candidates.push(txt);
                        }
                    }
                }
                return candidates.length ? candidates[0] : null;
            }""")
            if time_str:
                m = re.search(r"\b(\d{2}:\d{2}(?::\d{2})?)\b", time_str)
                if m:
                    return self._parse_target_time(m.group(1))
        except Exception:
            pass

        # 2) Try intercepted API payload
        try:
            txt = self.engine.get_intercepted("textToNumber")
            body = (txt or {}).get("body", "")
            if body:
                # JSON body path
                try:
                    data = json.loads(body)
                    for key in ("time", "serverTime", "currentTime"):
                        v = data.get(key)
                        if isinstance(v, str):
                            parsed = self._parse_target_time(v)
                            if parsed:
                                return parsed
                        if isinstance(v, (int, float)) and v > 1_000_000_000:
                            # unix timestamp in seconds or milliseconds
                            ts = float(v)
                            if ts > 1_000_000_000_000:
                                ts = ts / 1000.0
                            return datetime.fromtimestamp(ts)
                except Exception:
                    pass

                # Raw text regex path
                m = re.search(r"\b(\d{2}:\d{2}(?::\d{2})?)\b", body)
                if m:
                    parsed = self._parse_target_time(m.group(1))
                    if parsed:
                        return parsed
        except Exception:
            pass

        # 3) Fallback
        return datetime.now()

    #  Private helpers 

    def _first_visible(self, selectors: list[str],
                       timeout: int = 3000) -> Optional[str]:
        for sel in selectors:
            if self.engine.is_visible(sel, timeout=min(timeout, 2000)):
                return sel
        return None

    # -- Open dialog -------------------------------------------------------

    def _open_login_dialog(self) -> bool:
        log("Opening login dialog...")
        self.engine.dismiss_popups()

        # Try normal click first; if overlay blocks, use force_click
        for sel in self.LOGIN_BTN:
            if self.engine.wait_and_click(sel, timeout=3000):
                debug(f"Login trigger clicked: {sel}")
                if self._first_visible(self.USER_INPUT, timeout=3000):
                    return True

        # Overlay may still block  dismiss again + force-click
        self.engine.dismiss_popups()
        for sel in self.LOGIN_BTN:
            if self.engine.force_click(sel, timeout=3000):
                debug(f"Login trigger force-clicked: {sel}")
                self.engine.wait(1000)
                if self._first_visible(self.USER_INPUT, timeout=3000):
                    return True

        self.engine.screenshot("login_dialog_fail")
        error("Could not open login dialog")
        return False

    # -- Credentials -------------------------------------------------------

    def _fill_credentials(self) -> bool:
        # Use character-by-character typing (fill() is a bot signal)
        for sel in self.USER_INPUT:
            if self.engine.type_slowly(sel, self.username, delay=35):
                break
        else:
            error("Cannot find username input")
            return False

        # Quick pause between fields
        self.engine.human_delay(200, 400)

        for sel in self.PASS_INPUT:
            if self.engine.type_slowly(sel, self.password, delay=30):
                break
        else:
            error("Cannot find password input")
            return False

        debug("Credentials filled")
        return True

    # -- Image captcha (optional, only some IRCTC builds) ------------------

    def _handle_image_captcha_if_present(self):
        """If an image captcha is visible, solve and fill it.
        Uses instant DOM query (no waiting) to avoid 20s delays."""
        try:
            imgs = self.engine.page.query_selector_all("img")
            for img in imgs:
                src = (img.get_attribute("src") or "")
                if src.startswith("data:image") and len(src) > 200:
                    b64 = src.split(",", 1)[-1] if "," in src else src
                    debug(f"Image captcha found ({len(b64)} chars)")
                    answer = solve_captcha(b64)
                    if answer:
                        for inp in self.CAPTCHA_INPUT:
                            if self.engine.fill_input(inp, answer, timeout=2000):
                                log(f"Image captcha solved: {answer}")
                                return
                    return
        except Exception as e:
            debug(f"Captcha check error: {e}")

        debug("No image captcha in login dialog (expected for reCAPTCHA flow)")

    # -- SIGN IN -----------------------------------------------------------

    def _click_signin(self) -> bool:
        """Click SIGN IN using JS click to bypass overlay interception."""
        # Force-click first (overlays like Google Ads iframes block normal clicks)
        for sel in self.SIGNIN_BTN:
            if self.engine.force_click(sel, timeout=5000):
                debug(f"SIGN IN force-clicked via {sel}")
                return True
        # Fallback: normal click
        for sel in self.SIGNIN_BTN:
            if self.engine.wait_and_click(sel, timeout=5000):
                debug(f"SIGN IN clicked via {sel}")
                return True
        error("SIGN IN button not found")
        self.engine.screenshot("signin_missing")
        return False

    # -- reCAPTCHA handling ------------------------------------------------

    def _recaptcha_challenge_visible(self) -> bool:
        """Check if a visible reCAPTCHA v2 challenge iframe is present."""
        try:
            return self.engine.page.evaluate(r"""() => {
                const frames = document.querySelectorAll('iframe');
                for (const f of frames) {
                    const src = f.src || '';
                    if ((src.includes('recaptcha') && src.includes('bframe'))
                        || src.includes('recaptcha/api2/anchor')) {
                        if (f.offsetHeight > 60) return true;
                    }
                }
                return false;
            }""")
        except Exception:
            return False

    def _wait_for_recaptcha_completion(self, timeout: int = 90):
        """Poll until the reCAPTCHA challenge iframe disappears or we time out."""
        log(f"Waiting up to {timeout}s for reCAPTCHA to be solved...")
        start = time.time()
        while time.time() - start < timeout:
            if not self._recaptcha_challenge_visible():
                debug("reCAPTCHA challenge resolved")
                return
            self.engine.wait(2000)
        warn(f"reCAPTCHA wait timed out after {timeout}s")

    # -- Result check ------------------------------------------------------

    def _check_login_result(self) -> str:
        """Determine outcome after clicking SIGN IN.
        Checks the intercepted API response FIRST (instant) before
        falling back to slow UI-visibility checks.
        """
        import time as _time

        #  Fast path: check intercepted API (available immediately) 
        # Poll for up to 3 seconds for the webtoken response
        deadline = _time.time() + 3.0
        while _time.time() < deadline:
            wt = self.engine.get_intercepted("webtoken")
            if wt:
                body = wt.get("body", "")
                status = wt.get("status")
                if status == 200 and "access_token" in body:
                    # Confirm the login dialog actually closed
                    self.engine.wait(500)
                    debug("Logged in  webtoken API returned access_token")
                    return "success"
                if "Invalid Captcha" in body:
                    return "invalid_captcha"
                if "Bad credentials" in body:
                    return "bad_credentials"
                debug(f"webtoken  {status}: {body[:200]}")
                break
            self.engine.wait(200)

        #  Check for error text in the page (fast) 
        try:
            body_text = self.engine.page.inner_text("body").lower()
        except Exception:
            body_text = ""

        if "invalid captcha" in body_text or "incorrect captcha" in body_text:
            return "invalid_captcha"
        if "bad credentials" in body_text or "invalid credentials" in body_text:
            return "bad_credentials"
        if "account locked" in body_text:
            return "Account locked"
        if "too many" in body_text:
            return "Too many attempts"

        #  Check for loginError div 
        try:
            err_el = self.engine.page.query_selector(".loginError")
            if err_el:
                err_text = (err_el.inner_text() or "").strip()
                if err_text:
                    debug(f"loginError div: {err_text}")
                    if "captcha" in err_text.lower():
                        return "invalid_captcha"
                    if "credential" in err_text.lower():
                        return "bad_credentials"
                    return err_text
        except Exception:
            pass

        #  Slow path: check UI indicators 
        # If the login dialog closed, confirm via page indicators
        dialog_still_open = bool(
            self._first_visible(self.USER_INPUT, timeout=1000)
        )

        if not dialog_still_open:
            # Quick check for common success indicators (short timeout)
            for ind in [
                ".post-login", ".welcometext",
                'a:has-text("My Profile")', 'a:has-text("My Account")',
                ':has-text("Last Transaction")',
            ]:
                if self.engine.is_visible(ind, timeout=1000):
                    debug(f"Logged in  indicator: {ind}")
                    return "success"

            url = self.engine.page.url
            if "train-search" in url or "train-list" in url:
                debug("Dialog closed + on correct URL  success")
                return "success"

        self.engine.screenshot("login_unknown")
        return "Unknown result"


