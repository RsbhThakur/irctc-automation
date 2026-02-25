"""
IRCTC Train Ticket Booking - Payment Handler (Browser-based)
Handles:
  A) UPI payment  enter VPA, confirm on phone, poll until success
  B) Wallet/IRCTC Wallet  click through
  C) Banks / Net Banking / Credit-Debit Card  prompt user to complete

The payment page may redirect through IRCTC WPS and Paytm/other gateways.
We wait up to 10 minutes for user to complete any manual steps (e.g., UPI
approval on phone).
"""

import time
from datetime import datetime
from pathlib import Path
from typing import Optional

from src.browser_engine import BrowserEngine
from src.utils import log, warn, error, success, debug, error_with_trace


class PaymentHandler:
    """Browser-based payment processing."""

    PAYMENT_TIMEOUT = 600  # 10 minutes max to complete payment

    def __init__(self, engine: BrowserEngine, config: dict):
        self.engine = engine
        self.config = config

    #  Public 

    def process_payment(self) -> bool:
        """
        Handle the full payment flow  select method, fill details,
        wait for completion.
        """
        log("Processing payment...")

        # Wait for payment page  use domcontentloaded (fast) not networkidle (slow)
        self.engine.wait_for_load("domcontentloaded", timeout=10_000)
        self.engine.wait(500)
        self.engine.dismiss_popups()
        self.engine.screenshot("payment_page")
        self.engine.log_page_info()

        method = self.config.get("PAYMENT_METHOD", "UPI").upper()

        if method == "UPI":
            return self._handle_upi()
        elif method == "WALLET":
            return self._handle_wallet()
        else:
            log(f"Payment method '{method}'  please complete payment in the browser window.")
            return self._wait_for_payment_completion()

    #  UPI Payment 

    def _handle_upi(self) -> bool:
        """IRCTC iPay flow: click Pay & Book on payment page,
        then click 'click here to pay through QR' on the irctcipay.com gateway."""
        page = self.engine.page
        log("Using IRCTC iPay  QR payment flow...")

        #  Diagnostic: log payment options 
        try:
            pay_diag = page.evaluate("""() => {
                const bankTypes = Array.from(document.querySelectorAll('.bank-type')).filter(e => e.offsetHeight > 0).map(e => ({
                    text: (e.innerText || '').trim().substring(0, 60),
                    active: e.classList.contains('bank-type-active'),
                }));
                return bankTypes;
            }""")
            debug(f"Payment options: {pay_diag}")
        except Exception as e:
            debug(f"Payment diagnostic error: {e}")

        #  Step 1: Ensure IRCTC iPay is the active tab (it's the default/first) 
        # If BHIM/UPI was pre-selected on passenger page, iPay might not be active.
        # Click iPay if it's not already active.
        try:
            ipay_status = page.evaluate("""() => {
                const bankTypes = document.querySelectorAll('.bank-type');
                for (const el of bankTypes) {
                    const text = (el.innerText || '').toUpperCase();
                    if (text.includes('IRCTC') && text.includes('IPAY')) {
                        if (!el.classList.contains('bank-type-active')) {
                            el.click();
                            return 'clicked_ipay';
                        }
                        return 'already_active';
                    }
                }
                return 'not_found';
            }""")
            debug(f"IRCTC iPay status: {ipay_status}")
            if ipay_status == 'clicked_ipay':
                self.engine.wait(1000)
        except Exception as e:
            debug(f"iPay tab error: {e}")

        #  Step 2: Click Pay & Book button (FAST  no extra waits) 
        pay_clicked = False
        try:
            clicked = page.evaluate("""() => {
                const btns = document.querySelectorAll('button, input[type="submit"]');
                for (const b of btns) {
                    const t = (b.innerText || b.value || '').trim();
                    const cls = b.className || '';
                    if (cls.includes('btn_Tab') || t === 'Back') continue;
                    if (t.toLowerCase().includes('pay & book') ||
                        t.toLowerCase().includes('pay and book') ||
                        t.toLowerCase().includes('make payment')) {
                        b.click();
                        return t.substring(0, 30);
                    }
                }
                return null;
            }""")
            if clicked:
                pay_clicked = True
                debug(f"Pay & Book clicked: {clicked}")
        except Exception as e:
            debug(f"Pay & Book JS error: {e}")

        if not pay_clicked:
            for sel in [
                'button:has-text("Pay & Book")',
                'button:has-text("Make Payment")',
                'button:has-text("Pay"):not(.btn_Tab)',
            ]:
                try:
                    loc = page.locator(sel).first
                    if loc.is_visible(timeout=2000):
                        loc.click(force=True, timeout=3000)
                        pay_clicked = True
                        debug(f"Pay & Book clicked via: {sel}")
                        break
                except Exception:
                    continue

        if pay_clicked:
            log("Pay & Book clicked  waiting for IRCTC iPay gateway...")
        else:
            warn("Pay & Book button not found  please click it manually")

        #  Step 3: Wait for irctcipay.com gateway redirect 
        # Poll quickly instead of a fixed wait
        import time as _t
        deadline = _t.time() + 15.0
        on_gateway = False
        while _t.time() < deadline:
            try:
                cur_url = page.url
                if 'irctcipay' in cur_url:
                    on_gateway = True
                    debug(f"Reached gateway: {cur_url}")
                    break
            except Exception:
                pass
            self.engine.wait(500)

        if on_gateway:
            self._click_qr_on_gateway()
        else:
            try:
                debug(f"Not on irctcipay  URL: {page.url}")
            except Exception:
                pass

        return self._wait_for_payment_completion()

    def _click_qr_on_gateway(self):
        """On the irctcipay.com surcharge/payment page, click
        'click here to pay through QR' link using real mouse-like actions."""
        page = self.engine.page
        log("On IRCTC iPay gateway  looking for QR payment link...")

        self.engine.wait_for_load("domcontentloaded", timeout=10_000)
        self.engine.wait(800)
        self._dump_qr_debug_html()

        qr_clicked = False

        # Direct method works in browser console for this page; use it first.
        if self._invoke_submit_upi_qr_form():
            self.engine.wait(600)
            if self._qr_page_activated():
                qr_clicked = True
                debug("QR link activated via direct submitUpiQrForm() call")

        for attempt in range(1, 7):
            if qr_clicked:
                break
            try:
                found = page.evaluate("""() => {
                    const explicit = document.querySelector('#PayByQrButton span[onclick*="submitUpiQrForm"]')
                        || document.querySelector('#PayByQrButton')
                        || document.querySelector('#qrUpiZone span[onclick*="submitUpiQrForm"]')
                        || document.querySelector('span[onclick*="submitUpiQrForm"]');
                    if (explicit && explicit.offsetHeight > 0) {
                        explicit.setAttribute('data-irctc-qr-target', 'true');
                        explicit.scrollIntoView({behavior: 'auto', block: 'center'});
                        return true;
                    }
                    return false;
                }""")
                if not found:
                    self.engine.wait(500)
                    continue

                loc = page.locator('[data-irctc-qr-target="true"]').first
                if loc.is_visible(timeout=1500):
                    try:
                        loc.hover(timeout=1500)
                        loc.click(timeout=2500)
                    except Exception:
                        pass
                    self.engine.wait(500)
                    if self._qr_page_activated():
                        qr_clicked = True
                        debug(f"QR link activated via hover+click (attempt {attempt})")
                        break

                    try:
                        page.evaluate("""() => {
                            const el = document.querySelector('[data-irctc-qr-target="true"]');
                            if (!el) return false;
                            ['mouseover','mousedown','mouseup','click'].forEach(t => {
                                el.dispatchEvent(new MouseEvent(t, {bubbles: true, cancelable: true, view: window}));
                            });
                            if (typeof submitUpiQrForm === 'function') {
                                submitUpiQrForm();
                            }
                            return true;
                        }""")
                    except Exception:
                        pass
                    self.engine.wait(500)
                    if self._qr_page_activated():
                        qr_clicked = True
                        debug(f"QR link activated via JS mouse events (attempt {attempt})")
                        break

                    if self._invoke_submit_upi_qr_form():
                        self.engine.wait(500)
                        if self._qr_page_activated():
                            qr_clicked = True
                            debug(f"QR link activated via direct submitUpiQrForm() (attempt {attempt})")
                            break

                    box = loc.bounding_box()
                    if box:
                        cx = box["x"] + box["width"] / 2
                        cy = box["y"] + box["height"] / 2
                        page.mouse.move(cx, cy)
                        page.mouse.down()
                        page.mouse.up()
                        self.engine.wait(500)
                        if self._qr_page_activated():
                            qr_clicked = True
                            debug(f"QR link activated via coordinate click (attempt {attempt})")
                            break

            except Exception as e:
                debug(f"QR click attempt {attempt} failed: {e}")

            self.engine.wait(700)

        if not qr_clicked:
            if self._invoke_submit_upi_qr_form():
                self.engine.wait(700)
                if self._qr_page_activated():
                    qr_clicked = True
                    debug("QR link activated via final direct submitUpiQrForm() call")

        if not qr_clicked:
            for sel in [
                '#PayByQrButton span[onclick*="submitUpiQrForm"]',
                '#PayByQrButton',
                'span[onclick*="submitUpiQrForm"]',
                'text="Click here to pay through QR"',
                'div:has-text("Click here to pay through QR")',
                'span:has-text("Click here to pay through QR")',
                'a:has-text("QR")',
            ]:
                try:
                    loc = page.locator(sel).first
                    if loc.is_visible(timeout=1200):
                        loc.hover(timeout=1500)
                        loc.click(timeout=2500)
                        self.engine.wait(500)
                        if self._qr_page_activated():
                            qr_clicked = True
                            debug(f"QR link activated via fallback selector: {sel}")
                            break
                except Exception:
                    continue

        if qr_clicked:
            log("Clicked 'pay through QR'  waiting for QR code...")
            self.engine.wait(2000)
            self.engine.screenshot("qr_code_page")
            if bool(self.config.get("HEADLESS", False)):
                self._print_qr_for_console_payment()
        else:
            warn("QR payment link not activated  please click it manually")
            self.engine.screenshot("qr_link_not_found")

    def _invoke_submit_upi_qr_form(self) -> bool:
        """Invoke gateway's own QR submit function directly if available."""
        try:
            return bool(self.engine.page.evaluate("""() => {
                if (typeof submitUpiQrForm === 'function') {
                    submitUpiQrForm();
                    return true;
                }
                // Some pages attach on window explicitly.
                if (window && typeof window.submitUpiQrForm === 'function') {
                    window.submitUpiQrForm();
                    return true;
                }
                return false;
            }"""))
        except Exception:
            return False

    def _qr_page_activated(self) -> bool:
        """Heuristic: detect QR view/modal after click."""
        try:
            return bool(self.engine.page.evaluate("""() => {
                const upiCc = document.querySelector('#upiCC');
                const qrImg = document.querySelector('#qrimage, #upiQrImage, img[src*="qr" i], img[alt*="qr" i]');
                if (upiCc && upiCc.offsetHeight > 0) return true;
                if (qrImg && qrImg.offsetHeight > 0) return true;
                const body = (document.body && document.body.innerText || '').toLowerCase();
                if (body.includes('checking payment status') && body.includes('scan') && body.includes('qr')) return true;
                return false;
            }"""))
        except Exception:
            return False

    def _dump_qr_debug_html(self):
        """Capture gateway HTML and candidate elements for QR troubleshooting."""
        try:
            page = self.engine.page
            dump_dir = Path("logs") / "dumps"
            dump_dir.mkdir(parents=True, exist_ok=True)
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")

            html_path = dump_dir / f"qr_gateway_{ts}.html"
            html = page.content()
            html_path.write_text(html, encoding="utf-8", errors="ignore")
            debug(f"Saved QR gateway HTML: {html_path}")

            diag = page.evaluate("""() => {
                const out = {};
                out.url = location.href;
                out.iframes = Array.from(document.querySelectorAll('iframe')).map(f => ({
                    src: f.src || '',
                    id: f.id || '',
                    name: f.name || '',
                    visible: f.offsetHeight > 0
                }));
                const nodes = [];
                const all = document.querySelectorAll('a,button,div,span,label,input[type=\"button\"],input[type=\"submit\"]');
                for (const el of all) {
                    const txt = (el.innerText || el.value || '').trim();
                    if (!txt) continue;
                    if (txt.toLowerCase().includes('click here') || txt.toLowerCase().includes('qr')) {
                        nodes.push({
                            tag: el.tagName,
                            text: txt.substring(0, 120),
                            id: el.id || '',
                            cls: el.className || '',
                            href: el.getAttribute('href') || '',
                            onclick: el.getAttribute('onclick') || '',
                            visible: el.offsetHeight > 0
                        });
                    }
                }
                out.candidates = nodes.slice(0, 30);
                return out;
            }""")
            debug(f"QR gateway diagnostic: {diag}")
        except Exception as e:
            debug(f"QR debug HTML dump failed: {e}")

    def _print_qr_for_console_payment(self):
        """Extract UPI payload/QR data and print a terminal-friendly QR."""
        payload = self._extract_upi_payload()
        if not payload:
            warn("Could not extract UPI QR payload from gateway page.")
            return

        log("UPI payment payload detected (use this if QR image is not visible):")
        log(payload)

        try:
            import qrcode
            qr = qrcode.QRCode(border=1)
            qr.add_data(payload)
            qr.make(fit=True)
            print()
            qr.print_ascii(invert=True)
            print()
            log("ASCII QR printed in console. Scan it with any UPI app.")
        except Exception as e:
            debug(f"ASCII QR render unavailable: {e}")
            warn("ASCII QR could not be rendered. Use the UPI payload above.")

    def _extract_upi_payload(self) -> Optional[str]:
        """Try multiple selectors/attributes to get a UPI URI from gateway page."""
        page = self.engine.page
        try:
            payload = page.evaluate("""() => {
                const pick = (v) => (typeof v === 'string' && v.trim()) ? v.trim() : null;
                const isUpi = (v) => v && v.toLowerCase().includes('upi://pay');
                const candidates = [];
                const attrs = ['value', 'href', 'src', 'data', 'data-upi', 'data-qr', 'data-url', 'content'];

                const direct = document.querySelectorAll(
                    '#upiCC, #upiQrString, #upiIntent, #qrValue, #upiData, #qrData, input, textarea, a, img, meta'
                );
                direct.forEach((el) => {
                    attrs.forEach((a) => {
                        const v = pick(el.getAttribute && el.getAttribute(a));
                        if (v) candidates.push(v);
                    });
                    const txt = pick(el.textContent);
                    if (txt) candidates.push(txt);
                });

                const scripts = Array.from(document.querySelectorAll('script')).map(s => s.textContent || '');
                scripts.forEach((s) => {
                    if (!s) return;
                    const m = s.match(/upi:\\/\\/pay[^"'\\s<)]+/i);
                    if (m && m[0]) candidates.push(m[0]);
                });

                const globals = ['upiURL', 'upiUrl', 'upiIntent', 'qrData', 'qrString', 'upiString'];
                globals.forEach((k) => {
                    try {
                        const v = pick(window[k]);
                        if (v) candidates.push(v);
                    } catch (e) {}
                });

                for (const c of candidates) {
                    if (isUpi(c)) return c;
                }
                return null;
            }""")
            if payload:
                return str(payload).strip()
        except Exception as e:
            debug(f"UPI payload extraction failed: {e}")
        return None

    def _handle_wallet(self) -> bool:
        """Select IRCTC eWallet payment."""
        log("Selecting Wallet payment method...")

        for sel in [
            'div:has-text("IRCTC eWallet")',
            'label:has-text("eWallet")',
            'label:has-text("Wallet")',
            'input[value*="wallet" i]',
        ]:
            if self.engine.wait_and_click(sel, timeout=5000):
                debug(f"Wallet clicked via {sel}")
                break

        self.engine.wait(2000)

        # Click Pay
        for sel in [
            'button:has-text("Pay")',
            'button:has-text("Submit")',
            'button[type="submit"]',
        ]:
            if self.engine.wait_and_click(sel, timeout=5000):
                break

        self.engine.screenshot("wallet_submitted")
        return self._wait_for_payment_completion()

    #  Wait for Completion 

    def _wait_for_payment_completion(self) -> bool:
        """
        Poll until the booking confirmation page appears,
        a success message is shown, or we time out.
        """
        log(f"Waiting up to {self.PAYMENT_TIMEOUT // 60} minutes for payment completion...")
        log("If using UPI, approve the payment request on your phone NOW.")

        start = time.time()
        last_screenshot = 0

        while time.time() - start < self.PAYMENT_TIMEOUT:
            if not self.engine.page_alive:
                if self._recover_payment_page():
                    debug("Recovered payment page context after close")
                    self.engine.wait(1000)
                else:
                    warn("Payment page was closed before confirmation")
                    return False

            url = self.engine.page.url.lower()

            #  Check for success indicators 
            if "booking-confirm" in url or "bookingconfirm" in url:
                success("Booking confirmation page detected!")
                self.engine.screenshot("booking_confirmed")
                self._log_booking_details()
                return True

            try:
                body = self.engine.page.inner_text("body")[:5000].lower()
            except Exception:
                body = ""

            if "booking confirmed" in body or "pnr" in body and "confirmed" in body:
                success("Booking CONFIRMED!")
                self.engine.screenshot("booking_confirmed")
                self._log_booking_details()
                return True

            if "payment successful" in body:
                log("Payment successful - waiting for confirmation...")

            if "payment failed" in body or "transaction failed" in body:
                error("Payment FAILED!")
                self.engine.screenshot("payment_failed")
                return False

            if "session expired" in body or "session timeout" in body:
                error("Session expired during payment!")
                self.engine.screenshot("session_expired")
                return False

            # Take periodic screenshots
            elapsed = time.time() - start
            if elapsed - last_screenshot > 30:
                self.engine.screenshot(f"payment_wait_{int(elapsed)}")
                last_screenshot = elapsed
                debug(f"Still waiting for payment completion... ({int(elapsed)}s)")

            self.engine.wait(3000)

        error(f"Payment timed out after {self.PAYMENT_TIMEOUT}s")
        self.engine.screenshot("payment_timeout")
        return False

    def _recover_payment_page(self) -> bool:
        """Try to recover an active page if payment tab closed unexpectedly."""
        try:
            # If another page exists in the context, switch to the newest one.
            if self.engine.context:
                pages = [p for p in self.engine.context.pages if not p.is_closed()]
                if pages:
                    self.engine.page = pages[-1]
                    self.engine.page.set_default_timeout(20_000)
                    self.engine.page.on("response", self.engine._on_response)
                    self.engine.page.on("close", self.engine._on_page_close)
                    return True
        except Exception:
            pass

        # Fallback: open a fresh page and try checking IRCTC transaction status manually.
        try:
            if self.engine.recover_page():
                self.engine.goto("https://www.irctc.co.in/nget/profile/my-transactions", wait_until="domcontentloaded", timeout=60_000)
                return True
        except Exception:
            pass
        return False

    def _log_booking_details(self):
        """Try to extract and log PNR / booking details from the page."""
        try:
            body = self.engine.page.inner_text("body")

            # Look for PNR number (10-digit number)
            import re
            pnr_match = re.search(r'PNR[:\s]*(\d{10})', body, re.IGNORECASE)
            if pnr_match:
                log(f"PNR Number: {pnr_match.group(1)}", "SUCCESS")

            # Look for booking status
            status_match = re.search(r'Status[:\s]*(CNF|RAC|WL)[\s/]*\d*', body)
            if status_match:
                log(f"Booking Status: {status_match.group(0)}", "SUCCESS")

        except Exception as e:
            debug(f"Could not extract booking details: {e}")



