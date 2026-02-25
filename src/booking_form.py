"""
IRCTC Train Ticket Booking - Passenger & Booking Form (Browser-based)
After clicking "Book Now", the passenger-input page (/psgninput) loads.
This module fills in all passenger details, selects options, checks the
auto-upgrade / insurance / berth preferences, and submits.

Flow:
1. Wait for the passenger-input page
2. Fill each passenger row (name, age, gender, berth, food, nationality)
3. Tick auto-upgrade / travel-insurance / confirm-berth checkboxes
4. Set GST / mobile if needed
5. Click "Continue"  Review page loads
6. Click "I agree to Terms & conditions"
7. Solve booking captcha
8. Click "Make Payment" to proceed
"""

import time
from typing import Optional

from src.browser_engine import BrowserEngine
from src.captcha_solver import solve_captcha
from src.utils import log, warn, error, success, debug, error_with_trace


class BookingForm:
    """Browser-based passenger details + booking-review handler."""

    def __init__(self, engine: BrowserEngine, config: dict):
        self.engine = engine
        self.config = config
        use_master_cfg = config.get("USE_MASTER_PASSENGER_LIST", False)
        if isinstance(use_master_cfg, str):
            self.use_master_passenger_list = use_master_cfg.strip().lower() in (
                "1", "true", "yes", "y", "on"
            )
        else:
            self.use_master_passenger_list = bool(use_master_cfg)

    #  Public 

    def fill_and_submit(self) -> bool:
        """
        Complete the passenger-input + review flow.
        Returns True when user arrives at the payment page.
        """
        if not self._wait_for_passenger_page():
            return False

        if not self._fill_passengers():
            return False

        self._set_options()

        if not self._click_continue():
            return False

        if not self._handle_review_page():
            return False

        return True

    #  Passenger Input Page 

    def _wait_for_passenger_page(self) -> bool:
        log("Waiting for passenger input page...")

        # Wait for URL to contain psgninput (may already be there from dialog handler)
        if "psgninput" not in self.engine.page.url:
            self.engine.wait_for_url("psgninput", timeout=30_000)

        self.engine.wait_for_load("domcontentloaded", timeout=15_000)
        self.engine.dismiss_popups()

        #  Dismiss fare summary sidebar popup 
        self._dismiss_fare_summary()

        # Verify a passenger name input is visible
        name_visible = False
        for sel in [
            'input[placeholder="Name"]',
            'input[formcontrolname="passengerName"]',
        ]:
            if self.engine.is_visible(sel, timeout=3000):
                name_visible = True
                break

        if name_visible:
            log("Passenger input page loaded")
            self.engine.screenshot("passenger_page")
            return True

        warn("Passenger name input not found  page may have different layout")
        self.engine.screenshot("passenger_page_missing")
        return True  # proceed anyway

    def _dismiss_fare_summary(self):
        """Dismiss the fare summary sidebar popup (p-sidebar#app-journey-details).
        
        This popup has an overlay mask that blocks all pointer events.
        Must click its OK button or remove the overlay before filling forms.
        """
        page = self.engine.page

        # Strategy 1: Click the OK button in the sidebar via Playwright
        for sel in [
            '#app-journey-details button.search_btn',
            'p-sidebar#app-journey-details button:has-text("OK")',
            'button.search_btn:has-text("OK")',
        ]:
            try:
                loc = page.locator(sel).first
                if loc.is_visible(timeout=2000):
                    loc.click(force=True, timeout=3000)
                    debug(f"Dismissed fare summary via: {sel}")
                    self.engine.wait(500)
                    return True
            except Exception:
                continue

        # Strategy 2: JavaScript click + remove overlay
        try:
            dismissed = page.evaluate("""() => {
                // Click the OK button
                const btn = document.querySelector('#app-journey-details button.search_btn');
                if (btn) { btn.click(); }
                // Also remove the overlay mask
                const masks = document.querySelectorAll('.ui-widget-overlay.ui-sidebar-mask');
                masks.forEach(m => m.remove());
                return !!btn;
            }""")
            if dismissed:
                debug("Dismissed fare summary via JS")
                self.engine.wait(500)
                return True
        except Exception as e:
            debug(f"JS fare summary dismiss failed: {e}")

        # Strategy 3: Just remove the blocking overlay so inputs become clickable
        try:
            page.evaluate("""() => {
                document.querySelectorAll('.ui-widget-overlay').forEach(m => m.remove());
                const sidebar = document.querySelector('#app-journey-details');
                if (sidebar) sidebar.style.display = 'none';
            }""")
            debug("Removed overlay/sidebar via JS")
        except Exception:
            pass

        return False

    def _fill_passengers(self) -> bool:
        """Fill details for each passenger row."""
        stage_t0 = time.perf_counter()
        passengers = self.config.get("PASSENGER_DETAILS", [])
        if not passengers:
            error("No passengers in config!")
            return False

        log(f"Filling details for {len(passengers)} passenger(s)...")
        log(
            "Passenger fill mode: "
            + ("MASTER_LIST_AUTOCOMPLETE" if self.use_master_passenger_list else "MANUAL_FULL_FILL")
        )

        page = self.engine.page

        #  Ensure enough passenger rows exist 
        # IRCTC starts with 1 row; click "+ Add Passenger" for more
        needed = len(passengers)
        current_count = page.locator('input[placeholder="Name"]').count()
        debug(f"Initial passenger rows: {current_count}, need: {needed}")

        #  Diagnostic: dump all select + p-dropdown elements (once) 
        try:
            dd_info = page.evaluate("""() => {
                const selects = Array.from(document.querySelectorAll('select'));
                const selInfo = selects.filter(s => s.offsetHeight > 0 || s.offsetWidth > 0).map(s => ({
                    type: 'select',
                    fc: s.getAttribute('formcontrolname') || '',
                    name: s.name || '',
                    id: s.id || '',
                    options: Array.from(s.options).slice(0, 5).map(o => o.text),
                }));
                const dds = Array.from(document.querySelectorAll('p-dropdown'));
                const ddInfo = dds.filter(d => d.offsetHeight > 0).map(d => ({
                    type: 'p-dropdown',
                    fc: d.getAttribute('formcontrolname') || '',
                    label: (d.querySelector('.ui-dropdown-label') || {}).innerText || '',
                }));
                return [...selInfo, ...ddInfo];
            }""")
            debug(f"Dropdowns/selects: {dd_info}")
        except Exception as e:
            debug(f"Dropdown diagnostic failed: {e}")

        #  Fill passengers one-by-one: fill current row, then add next row 
        # This is faster because the master-list dropdown is already visible
        # when a row is created, so we fill immediately before adding the next.
        for idx, pax in enumerate(passengers):
            name   = pax.get("NAME", "")
            age    = str(pax.get("AGE", ""))
            gender = pax.get("GENDER", "Male")
            berth  = pax.get("BERTH", "No Preference")
            food   = pax.get("FOOD", "No Food")

            debug(f"Passenger {idx+1}: {name}, {age}, {gender}, {berth}")

            #  Ensure row exists (row 0 already exists; add rows 1+) 
            if idx > 0:
                current_count = page.locator('input[placeholder="Name"]').count()
                if current_count <= idx:
                    self._click_add_passenger()
                    self.engine.wait(800)  # wait for Angular to render new row

            #  Remove readonly from this row's name input if set 
            try:
                page.evaluate(f"""(idx) => {{
                    const inputs = document.querySelectorAll('input[placeholder="Name"]');
                    if (inputs[idx]) inputs[idx].removeAttribute('readonly');
                }}""", idx)
            except Exception:
                pass

            #  Name: Try master-list autocomplete first (if enabled), then manual fill 
            master_used = False
            if self.use_master_passenger_list:
                master_used = self._select_from_master_list(idx, name)

            if master_used:
                debug(f"Passenger {idx+1} filled via master list (skipping age/gender/berth)")
                if berth and berth != "No Preference":
                    try:
                        self._select_nth_native('passengerBerthChoice', idx, berth)
                    except Exception:
                        pass
                debug(f"Passenger {idx+1} filled")
                continue

            # Manual fill path
            name_filled = self._fill_nth_input(
                'input[placeholder="Name"]', idx, name
            )
            if not name_filled:
                # Try JS-based fill that bypasses readonly
                try:
                    page.evaluate(f"""(args) => {{
                        const [idx, name] = args;
                        const inputs = document.querySelectorAll('input[placeholder="Name"]');
                        if (inputs[idx]) {{
                            inputs[idx].removeAttribute('readonly');
                            inputs[idx].value = name;
                            inputs[idx].dispatchEvent(new Event('input', {{bubbles: true}}));
                            inputs[idx].dispatchEvent(new Event('change', {{bubbles: true}}));
                        }}
                    }}""", [idx, name])
                    name_filled = True
                    debug(f"Filled name #{idx} via JS: '{name}'")
                except Exception as e:
                    debug(f"JS name fill #{idx} error: {e}")

            if not name_filled:
                try:
                    loc = page.locator('input[placeholder="Name"]').nth(idx)
                    loc.fill(name, timeout=3000)
                    name_filled = True
                    debug(f"Filled name #{idx} via locator.fill: '{name}'")
                except Exception as e:
                    debug(f"Name locator #{idx} fill error: {e}")

            if not name_filled:
                error(f"Cannot fill name for passenger {idx+1}")
                self.engine.screenshot(f"pax_name_fail_{idx}")
                return False

            self.engine.wait(200)

            #  Age 
            age_filled = self._fill_nth_input(
                'input[formcontrolname="passengerAge"]', idx, age
            )
            if not age_filled:
                self._fill_nth_input('input[placeholder="Age"]', idx, age)

            #  Gender 
            if not self._select_nth_native('passengerGender', idx, gender):
                self._select_pax_dropdown(idx, 'passengerGender', gender)

            #  Berth preference 
            if berth and berth != "No Preference":
                if not self._select_nth_native('passengerBerthChoice', idx, berth):
                    self._select_pax_dropdown(idx, 'passengerBerthChoice', berth)

            #  Food preference 
            if food and food != "No Food":
                if not self._select_nth_native('passengerFoodChoice', idx, food):
                    self._select_pax_dropdown(idx, 'passengerFoodChoice', food)

            self.engine.wait(200)
            debug(f"Passenger {idx+1} filled")

        stage_elapsed = time.perf_counter() - stage_t0
        log(f"Passenger fill stage time: {stage_elapsed:.3f}s")
        log("Passenger details filled")
        self.engine.screenshot("passengers_filled")
        return True

    def _click_add_passenger(self):
        """Click the '+ Add Passenger' span/link to create a new row."""
        page = self.engine.page

        # Best approach: JS click on the parent <a> of the "+ Add Passenger" span
        # The DOM has: <a>  <span class="prenext">+ Add Passenger</span>
        #                   <span class="prenext">/ Add Infant With Berth</span> </a>
        # We must click the parent <a> to trigger Angular event properly.
        try:
            clicked = page.evaluate("""() => {
                const spans = Array.from(document.querySelectorAll('span.prenext'));
                const addBtn = spans.find(s => s.innerText.trim().startsWith('+ Add Passenger'));
                if (addBtn) {
                    const parent = addBtn.closest('a') || addBtn;
                    parent.click();
                    return true;
                }
                const links = Array.from(document.querySelectorAll('a'));
                const addLink = links.find(a => {
                    const t = a.innerText || '';
                    return t.includes('Add Passenger') && !t.includes('Infant Without');
                });
                if (addLink) { addLink.click(); return true; }
                return false;
            }""")
            if clicked:
                debug("Clicked Add Passenger via JS")
                return True
        except Exception as e:
            debug(f"JS Add Passenger click failed: {e}")

        # Fallback: Playwright locator with force click
        for sel in [
            'span.prenext:has-text("+ Add Passenger")',
            'a:has-text("Add Passenger")',
        ]:
            try:
                loc = page.locator(sel).first
                if loc.is_visible(timeout=2000):
                    loc.click(force=True, timeout=2000)
                    debug(f"Clicked Add Passenger via: {sel}")
                    return True
            except Exception:
                continue

        debug("Add Passenger button not found")
        return False

    def _fill_nth_input(self, selector: str, n: int, value: str) -> bool:
        """Fill the n-th (0-based) matching input."""
        try:
            page = self.engine.page
            # Remove readonly/disabled before filling
            page.evaluate(f"""(args) => {{
                const [selector, n] = args;
                const inputs = document.querySelectorAll(selector);
                if (inputs[n]) {{
                    inputs[n].removeAttribute('readonly');
                    inputs[n].removeAttribute('disabled');
                }}
            }}""", [selector, n])
            inputs = page.query_selector_all(selector)
            if len(inputs) > n:
                inputs[n].fill(value)
                debug(f"Filled input #{n}: '{value}'")
                return True
        except Exception as e:
            debug(f"fill_nth_input [{selector}][{n}] error: {e}")
        return False

    def _select_nth_native(self, fc_name: str, n: int, text: str) -> bool:
        """Select an option in the n-th native <select> by formcontrolname.
        
        Returns True if a matching <select> was found and filled.
        """
        page = self.engine.page
        selector = f'select[formcontrolname="{fc_name}"]'
        try:
            selects = page.query_selector_all(selector)
            if len(selects) > n:
                # Use Playwright's select_option which handles <select> properly
                page.locator(selector).nth(n).select_option(label=text, timeout=2000)
                debug(f"Native select [{fc_name}] #{n}  '{text}'")
                return True
        except Exception as e:
            debug(f"Native select [{fc_name}] #{n} error: {e}")

        # Also try by ID pattern (IRCTC sometimes uses id like "passengerGender0")
        try:
            sel_by_id = f'select[id*="{fc_name}"], select[name*="{fc_name}"]'
            elements = page.query_selector_all(sel_by_id)
            if len(elements) > n:
                page.locator(sel_by_id).nth(n).select_option(label=text, timeout=2000)
                debug(f"Native select (by id/name) [{fc_name}] #{n}  '{text}'")
                return True
        except Exception:
            pass

        return False

    def _select_nth_dropdown(self, selector: str, n: int, text: str) -> bool:
        """Open the n-th PrimeNG dropdown and select an option by text."""
        try:
            dropdowns = self.engine.page.query_selector_all(selector)
            if len(dropdowns) > n:
                dropdowns[n].click()
                self.engine.wait(400)
                # Pick option from the open dropdown panel
                opt = self.engine.page.locator(
                    f'.ui-dropdown-item:has-text("{text}"), '
                    f'li:has-text("{text}")'
                ).first
                opt.click(timeout=3000)
                debug(f"Dropdown #{n}  '{text}'")
                return True
        except Exception as e:
            debug(f"select_nth_dropdown [{selector}][{n}] error: {e}")
        return False

    def _select_pax_dropdown(self, pax_idx: int, fc_name: str, text: str) -> bool:
        """Select a value in a PrimeNG p-dropdown by formcontrolname for the nth passenger.
        
        Uses multiple strategies:
        1. Standard p-dropdown[formcontrolname] selector
        2. JavaScript-based approach for stubborn dropdowns
        """
        page = self.engine.page
        selector = f'p-dropdown[formcontrolname="{fc_name}"]'

        # Strategy 1: Playwright locator approach
        try:
            loc = page.locator(selector).nth(pax_idx)
            if loc.is_visible(timeout=2000):
                loc.click(timeout=2000)
                self.engine.wait(400)
                # Click matching option in the dropdown panel
                opt = page.locator(
                    f'.ui-dropdown-item:has-text("{text}"), '
                    f'.ui-dropdown-items li:has-text("{text}")'
                ).first
                if opt.is_visible(timeout=2000):
                    opt.click(timeout=2000)
                    debug(f"Dropdown [{fc_name}] #{pax_idx}  '{text}'")
                    return True
                else:
                    # Try pressing Escape to close and retry
                    page.keyboard.press("Escape")
        except Exception as e:
            debug(f"Dropdown [{fc_name}] #{pax_idx} strategy 1 error: {e}")

        # Strategy 2: JavaScript  find all p-dropdown with matching fc, click nth,
        # then click the matching li in the open panel
        try:
            clicked = page.evaluate(f"""(args) => {{
                const [fcName, idx, optText] = args;
                const dds = Array.from(document.querySelectorAll(
                    'p-dropdown[formcontrolname="' + fcName + '"]'
                ));
                if (dds.length <= idx) return 'no_dropdown';
                const dd = dds[idx];
                // Click to open
                const trigger = dd.querySelector('.ui-dropdown-trigger') || dd;
                trigger.click();
                return 'opened';
            }}""", [fc_name, pax_idx, text])

            if clicked == 'opened':
                self.engine.wait(500)
                # Now click the option
                opt = page.locator(
                    f'.ui-dropdown-item:has-text("{text}"), '
                    f'.ui-dropdown-items li:has-text("{text}")'
                ).first
                if opt.is_visible(timeout=2000):
                    opt.click(timeout=2000)
                    debug(f"Dropdown [{fc_name}] #{pax_idx}  '{text}' (JS open)")
                    return True
                else:
                    page.keyboard.press("Escape")
                    debug(f"Dropdown [{fc_name}] #{pax_idx}: option '{text}' not found")
            else:
                debug(f"Dropdown [{fc_name}] #{pax_idx}: {clicked}")
        except Exception as e:
            debug(f"Dropdown [{fc_name}] #{pax_idx} strategy 2 error: {e}")

        return False

    def _select_from_master_list(self, idx: int, name: str) -> bool:
        """Try to select a passenger from the IRCTC master-list autocomplete.
        
        The name input is a PrimeNG p-autocomplete. Typing a few chars shows
        saved passengers as suggestions. Click the matching one to auto-fill
        name, age, gender, etc.
        """
        page = self.engine.page
        try:
            # Remove readonly attribute first (master selection on pax 0 can set
            # readonly on subsequent rows)
            page.evaluate(f"""(idx) => {{
                const inputs = document.querySelectorAll('input[placeholder="Name"]');
                if (inputs[idx]) {{
                    inputs[idx].removeAttribute('readonly');
                    inputs[idx].removeAttribute('disabled');
                }}
            }}""", idx)

            loc = page.locator('input[placeholder="Name"]').nth(idx)
            if not loc.is_visible(timeout=2000):
                return False

            # Click first to focus and potentially dismiss readonly state
            try:
                loc.click(timeout=1500)
                self.engine.wait(200)
            except Exception:
                pass

            # Type first few chars to trigger autocomplete
            search_text = name[:4] if len(name) >= 4 else name
            try:
                loc.fill(search_text, timeout=2000)
            except Exception:
                # JS fallback for fill
                page.evaluate(f"""(args) => {{
                    const [idx, text] = args;
                    const inputs = document.querySelectorAll('input[placeholder="Name"]');
                    if (inputs[idx]) {{
                        inputs[idx].value = text;
                        inputs[idx].dispatchEvent(new Event('input', {{bubbles: true}}));
                    }}
                }}""", [idx, search_text])
            self.engine.wait(1000)  # wait for autocomplete suggestions

            # Look for matching suggestion in the dropdown
            suggestion = page.locator(
                f'.ui-autocomplete-panel li:has-text("{name}"), '
                f'.ui-autocomplete-list-item:has-text("{name}"), '
                f'ul[role="listbox"] li:has-text("{name}")'
            ).first

            if suggestion.is_visible(timeout=2000):
                suggestion.click(timeout=2000)
                debug(f"Selected '{name}' from master list for passenger #{idx+1}")
                self.engine.wait(500)
                return True
            else:
                # No matching suggestion  clear and let manual fill happen
                try:
                    loc.fill("", timeout=1000)
                except Exception:
                    page.evaluate(f"""(idx) => {{
                        const inputs = document.querySelectorAll('input[placeholder="Name"]');
                        if (inputs[idx]) inputs[idx].value = '';
                    }}""", idx)
                self.engine.wait(200)
                debug(f"No master list match for '{name}'  will fill manually")
        except Exception as e:
            debug(f"Master list selection #{idx} error: {e}")
        return False

    def _set_options(self):
        """Set auto-upgrade, confirm-berth-only, insurance, and payment method on passenger page."""
        page = self.engine.page

        #  Auto Upgrade checkbox 
        self._try_check(
            'input[formcontrolname="autoUpgradationSelected"], '
            'input[name="autoUpgradation"]',
            "Auto Upgrade"
        )

        #  Book only if confirmed berths 
        self._try_check(
            'input[formcontrolname="bookOnlyIfCnf"], '
            'input[name="confirmberths"]',
            "Book Only If Confirmed"
        )

        #  Travel insurance opt-out 
        try:
            no_insurance = page.locator('input[name^="travelInsuranceOpted"][value="false"]').first
            if no_insurance.is_visible(timeout=2000):
                no_insurance.check(force=True)
                debug("Selected No for travel insurance")
        except Exception:
            debug("Travel insurance opt-out not found")

        #  Select "Pay through BHIM/UPI" on passenger page 
        self._select_bhim_upi_on_passenger_page()

    def _select_bhim_upi_on_passenger_page(self):
        """Click 'Pay through BHIM/UPI' radio button on the passenger details page.
        
        The passenger page has a "Payment Mode" section with radio buttons:
          - paymentType value="3"  Credit Cards / Net Banking / etc. (default)
          - paymentType value="2"  Pay through BHIM/UPI
        This must be selected BEFORE clicking Continue.
        """
        page = self.engine.page
        payment_method = self.config.get("PAYMENT_METHOD", "UPI").upper()
        if payment_method not in ("UPI", "BHIM"):
            return

        bhim_clicked = False

        # Strategy 1 (primary): Click the radio button directly
        try:
            clicked = page.evaluate("""() => {
                // The BHIM/UPI radio is: input[name="paymentType"][value="2"]
                const radio = document.querySelector('input[name="paymentType"][value="2"]');
                if (radio) {
                    radio.scrollIntoView({block: 'center'});
                    radio.click();
                    radio.checked = true;
                    radio.dispatchEvent(new Event('change', {bubbles: true}));
                    radio.dispatchEvent(new Event('input', {bubbles: true}));
                    // Also try Angular ngModel update
                    const ev = new Event('change', {bubbles: true});
                    radio.dispatchEvent(ev);
                    return 'radio_value2_clicked';
                }
                return null;
            }""")
            if clicked:
                bhim_clicked = True
                debug(f"BHIM/UPI radio: {clicked}")
        except Exception as e:
            debug(f"BHIM/UPI radio click error: {e}")

        # Strategy 2: Playwright locator for the radio
        if not bhim_clicked:
            try:
                radio = page.locator('input[name="paymentType"][value="2"]')
                if radio.count() > 0:
                    radio.first.scroll_into_view_if_needed(timeout=3000)
                    radio.first.check(force=True, timeout=3000)
                    bhim_clicked = True
                    debug("BHIM/UPI radio checked via Playwright")
            except Exception as e:
                debug(f"BHIM/UPI Playwright radio error: {e}")

        # Strategy 3: Click the label/text near the radio
        if not bhim_clicked:
            try:
                clicked = page.evaluate("""() => {
                    const labels = document.querySelectorAll('label, span, div');
                    for (const el of labels) {
                        const text = (el.innerText || '').trim();
                        if (text.includes('Pay through BHIM') || text === 'Pay through BHIM/UPI') {
                            if (el.offsetHeight > 0 && el.innerText.length < 100) {
                                el.click();
                                return 'label: ' + text.substring(0, 50);
                            }
                        }
                    }
                    return null;
                }""")
                if clicked:
                    bhim_clicked = True
                    debug(f"BHIM/UPI label: {clicked}")
            except Exception as e:
                debug(f"BHIM/UPI label click error: {e}")

        if bhim_clicked:
            log("Selected 'Pay through BHIM/UPI' on passenger page")
            self.engine.wait(1000)
            self.engine.screenshot("bhim_upi_selected_passenger")
        else:
            warn("'Pay through BHIM/UPI' radio not found on passenger page")

    def _try_check(self, selector: str, label: str):
        try:
            el = self.engine.page.query_selector(selector)
            if el and not el.is_checked():
                el.check(force=True)
                debug(f"Checked: {label}")
            elif el:
                debug(f"Already checked: {label}")
        except Exception:
            # Try JS click fallback
            try:
                self.engine.page.evaluate(f"""(sel) => {{
                    const el = document.querySelector(sel);
                    if (el && !el.checked) el.click();
                }}""", selector.split(",")[0].strip())
                debug(f"Checked via JS: {label}")
            except Exception:
                debug(f"Could not check: {label}")

    def _try_uncheck(self, selector: str, label: str):
        try:
            el = self.engine.page.query_selector(selector)
            if el and el.is_checked():
                el.uncheck()
                debug(f"Unchecked: {label}")
        except Exception:
            try:
                loc = self.engine.page.get_by_label(label, exact=False).first
                if loc.is_checked():
                    loc.uncheck()
            except Exception:
                debug(f"Could not uncheck: {label}")

    def _click_continue(self) -> bool:
        """Click Continue to proceed to the review page."""
        for sel in [
            'button:has-text("Continue")',
            'button[type="submit"]:has-text("Continue")',
            'button:has-text("Review Booking")',
        ]:
            if self.engine.wait_and_click(sel, timeout=5000):
                debug(f"Continue clicked via {sel}")
                self.engine.wait(2000)
                return True

        error("Continue button not found")
        self.engine.screenshot("continue_missing")
        return False

    #  Review & Captcha Page 

    def _handle_review_page(self) -> bool:
        """Handle the review-booking page: Terms checkbox, captcha, Pay."""
        log("Handling booking review page...")

        page = self.engine.page

        # Wait for review page URL  use shorter timeout, skip networkidle
        try:
            self.engine.wait_for_url("reviewBooking", timeout=30_000)
        except Exception:
            # May already be on review page
            if "reviewbooking" not in page.url.lower():
                error("Review page not reached")
                return False

        self.engine.wait_for_load("domcontentloaded", timeout=10_000)
        self.engine.wait(2000)
        self.engine.dismiss_popups()

        # Dismiss fare-summary overlay if it reappeared
        try:
            page.evaluate("""() => {
                // Remove fare summary sidebar overlay
                const mask = document.querySelector('.ui-widget-overlay.ui-sidebar-mask');
                if (mask) mask.remove();
                const sidebar = document.querySelector('p-sidebar#app-journey-details');
                if (sidebar) sidebar.style.display = 'none';
                // Click OK if fare summary is visible
                const ok = document.querySelector('p-sidebar .search_btn, #app-journey-details .search_btn');
                if (ok) ok.click();
            }""")
        except Exception:
            pass

        # Scroll down to reveal captcha and payment button
        try:
            page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            self.engine.wait(500)
            page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        except Exception:
            pass

        self.engine.screenshot("review_page")

        #  Diagnostic: dump review page elements 
        try:
            review_info = page.evaluate("""() => {
                const checks = Array.from(document.querySelectorAll('input[type="checkbox"]'));
                const chkInfo = checks.map(c => ({
                    fc: c.getAttribute('formcontrolname') || '',
                    name: c.name || '',
                    id: c.id || '',
                    checked: c.checked,
                    visible: c.offsetHeight > 0,
                }));
                const imgs = Array.from(document.querySelectorAll('img'));
                const captchaImgs = imgs.filter(i => {
                    const src = i.src || '';
                    return (src.startsWith('data:image') && src.length > 200) ||
                           i.className.includes('captcha');
                }).map(i => ({
                    cls: (i.className || '').substring(0, 40),
                    srcLen: (i.src || '').length,
                    isData: (i.src || '').startsWith('data:image'),
                }));
                const inputs = Array.from(document.querySelectorAll('input[type="text"]'));
                const captchaInputs = inputs.filter(i => {
                    const ph = (i.placeholder || '').toLowerCase();
                    const fc = (i.getAttribute('formcontrolname') || '').toLowerCase();
                    const nm = (i.name || '').toLowerCase();
                    return ph.includes('captcha') || fc.includes('captcha') || nm.includes('captcha') ||
                           ph.includes('enter the text') || ph.includes('security');
                }).map(i => ({
                    fc: i.getAttribute('formcontrolname') || '',
                    ph: i.placeholder || '',
                    name: i.name || '',
                }));
                const btns = Array.from(document.querySelectorAll('button'));
                const payBtns = btns.filter(b => b.offsetHeight > 0).map(b => ({
                    text: (b.innerText || '').substring(0, 40).trim(),
                    cls: (b.className || '').substring(0, 50),
                    disabled: b.disabled,
                    type: b.type || '',
                }));
                return { checkboxes: chkInfo, captchaImages: captchaImgs, captchaInputs: captchaInputs, allButtons: payBtns };
            }""")
            debug(f"Review checkboxes: {review_info.get('checkboxes', [])}")
            debug(f"Review captcha imgs: {review_info.get('captchaImages', [])}")
            debug(f"Review captcha inputs: {review_info.get('captchaInputs', [])}")
            debug(f"Review ALL buttons: {review_info.get('allButtons', [])}")
        except Exception as e:
            debug(f"Review diagnostic failed: {e}")

        #  Accept terms & conditions 
        terms_accepted = False
        for sel in [
            'input[type="checkbox"][formcontrolname="tnc"]',
            'input[type="checkbox"]#tnc',
            'input[type="checkbox"][name*="agree" i]',
            'input[type="checkbox"][name*="term" i]',
        ]:
            try:
                el = page.query_selector(sel)
                if el:
                    if not el.is_checked():
                        el.check(force=True)
                    terms_accepted = True
                    debug(f"Terms accepted via: {sel}")
                    break
            except Exception:
                continue

        if not terms_accepted:
            # JS fallback: check all visible checkboxes on review page
            try:
                page.evaluate("""() => {
                    const cbs = document.querySelectorAll('input[type="checkbox"]');
                    cbs.forEach(cb => { if (!cb.checked) cb.click(); });
                }""")
                debug("Terms accepted via JS (checked all checkboxes)")
            except Exception:
                warn("Could not check Terms checkbox")

        self.engine.wait(500)

        #  Select payment type (UPI) on the review page 
        self._select_payment_type_on_review()

        self.engine.wait(500)

        #  Solve booking captcha 
        ok = self._solve_booking_captcha(max_retries=5)
        if not ok:
            error("Booking captcha failed")
            return False

        return True

    def _select_payment_type_on_review(self):
        """Select UPI payment type on the review/booking page.
        
        The review page has payment gateway tabs (e.g., 'IRCTC-iPAY Payment Gateway')
        and payment type options (UPI, Net Banking, etc.) under the selected tab.
        """
        page = self.engine.page
        payment_method = self.config.get("PAYMENT_METHOD", "UPI").upper()

        # Diagnostic: dump ALL buttons, radio buttons, and tabs on the page
        try:
            pay_dom = page.evaluate("""() => {
                const allBtns = Array.from(document.querySelectorAll('button, .btn_Tab, [class*="btn"]'));
                const btnInfo = allBtns.filter(b => b.offsetHeight > 0).slice(0, 20).map(b => ({
                    tag: b.tagName,
                    text: (b.innerText || '').substring(0, 50).trim(),
                    cls: (b.className || '').substring(0, 60),
                    type: b.type || '',
                }));
                const radios = Array.from(document.querySelectorAll('input[type="radio"]'));
                const radioInfo = radios.map(r => ({
                    name: r.name || '',
                    value: r.value || '',
                    id: r.id || '',
                    fc: r.getAttribute('formcontrolname') || '',
                    checked: r.checked,
                    labelText: r.parentElement ? (r.parentElement.innerText || '').substring(0, 40).trim() : '',
                }));
                const tabs = Array.from(document.querySelectorAll('.bank-type, .pay-type, [class*="payment"], [class*="gateway"]'));
                const tabInfo = tabs.slice(0, 10).map(t => ({
                    tag: t.tagName,
                    cls: (t.className || '').substring(0, 60),
                    text: (t.innerText || '').substring(0, 60).trim(),
                }));
                return { buttons: btnInfo, radios: radioInfo, paymentTabs: tabInfo };
            }""")
            debug(f"Review page ALL buttons: {pay_dom.get('buttons', [])}")
            debug(f"Review page radios: {pay_dom.get('radios', [])}")
            debug(f"Review page payment tabs: {pay_dom.get('paymentTabs', [])}")
        except Exception as e:
            debug(f"Payment DOM diagnostic failed: {e}")

        if payment_method != "UPI":
            return

        # Strategy 1: Click UPI radio button (name="paymentType" value="3" or similar)
        for sel in [
            'input[name="paymentType"][value="3"]',
            'input[type="radio"][value*="upi" i]',
            'input[type="radio"][value*="UPI"]',
        ]:
            try:
                el = page.query_selector(sel)
                if el:
                    el.check(force=True)
                    debug(f"Selected UPI radio via: {sel}")
                    return
            except Exception:
                continue

        # Strategy 2: Click any element with UPI/BHIM text
        for sel in [
            'label:has-text("UPI")',
            'label:has-text("BHIM")',
            'span:has-text("UPI")',
            'div:has-text("UPI"):not(:has(div:has-text("UPI")))',
            'button:has-text("UPI")',
        ]:
            try:
                loc = page.locator(sel).first
                if loc.is_visible(timeout=1000):
                    loc.click(force=True, timeout=1500)
                    debug(f"Selected UPI via: {sel}")
                    return
            except Exception:
                continue

        # Strategy 3: JS  find and click any radio/label/div with UPI text
        try:
            clicked = page.evaluate("""() => {
                // Check radio buttons first
                const radios = document.querySelectorAll('input[type="radio"]');
                for (const r of radios) {
                    const parent = r.parentElement;
                    const txt = parent ? (parent.innerText || '').toUpperCase() : '';
                    if (txt.includes('UPI') || txt.includes('BHIM') || r.value === '3') {
                        r.click();
                        return 'radio_' + r.value;
                    }
                }
                // Try labels
                const labels = document.querySelectorAll('label, .bank-type span, .pay-type span');
                for (const l of labels) {
                    if ((l.innerText || '').toUpperCase().includes('UPI')) {
                        l.click();
                        return 'label';
                    }
                }
                return null;
            }""")
            if clicked:
                debug(f"Selected UPI via JS: {clicked}")
                return
        except Exception as e:
            debug(f"UPI JS selection error: {e}")

        debug("UPI payment type selection: no UPI element found on review page")

    def _solve_booking_captcha(self, max_retries: int = 5) -> bool:
        """Solve the captcha on the review page and click Make Payment."""
        page = self.engine.page

        for attempt in range(1, max_retries + 1):
            debug(f"Booking captcha attempt {attempt}/{max_retries}")

            # Find captcha image
            b64 = self._get_review_captcha_b64()
            if not b64:
                warn("Booking captcha image not found  maybe not required")
                # Try clicking Make Payment directly
                if self._click_make_payment():
                    return True
                continue

            answer = solve_captcha(b64)
            if not answer:
                self._refresh_booking_captcha()
                continue

            debug(f"Captcha answer: '{answer}'")

            # Fill answer using multiple selectors
            filled = False
            for sel in [
                'input[formcontrolname="captcha"]',
                'input[placeholder*="Captcha" i]',
                'input[placeholder*="enter the text" i]',
                'input[placeholder*="security" i]',
                'input[name="captcha"]',
                '#captcha',
                'input[id*="captcha" i]',
                '#nlpAnswer',
                'input[name="nlpAnswer"]',
                'input[formcontrolname="nlpAnswer"]',
            ]:
                if self.engine.fill_input(sel, answer, timeout=2000):
                    filled = True
                    debug(f"Captcha filled via: {sel}")
                    break

            if not filled:
                # JS fallback: find any text input near the captcha image
                try:
                    filled = page.evaluate(f"""(answer) => {{
                        const imgs = document.querySelectorAll('img');
                        for (const img of imgs) {{
                            if ((img.src || '').startsWith('data:image') && img.src.length > 200) {{
                                // Find nearest text input
                                const parent = img.closest('div, form, app-captcha');
                                if (parent) {{
                                    const inp = parent.querySelector('input[type="text"]');
                                    if (inp) {{
                                        inp.value = answer;
                                        inp.dispatchEvent(new Event('input', {{bubbles: true}}));
                                        inp.dispatchEvent(new Event('change', {{bubbles: true}}));
                                        return true;
                                    }}
                                }}
                            }}
                        }}
                        return false;
                    }}""", answer)
                    if filled:
                        debug("Captcha filled via JS (nearest input to captcha img)")
                except Exception as e:
                    debug(f"JS captcha fill error: {e}")

            if not filled:
                warn("Captcha input not found")
                continue

            self.engine.screenshot(f"review_captcha_{attempt}")

            # Click Make Payment / Continue
            if not self._click_make_payment():
                continue

            # Check result
            self.engine.wait(3000)

            # If we navigate to payment page  success
            try:
                url = page.url.lower()
            except Exception:
                debug("Page closed during captcha check")
                return False

            if "payment" in url or "bkgpayment" in url:
                log("Proceeding to payment page!")
                return True

            # Check if we're still on the review page or moved forward
            if "reviewbooking" not in url:
                debug(f"Moved past review page (url: {url})  assuming success")
                return True

            # Still on review page  captcha was likely wrong
            warn("Still on review page after submit  captcha may be wrong, refreshing")
            self._refresh_booking_captcha()
            continue

        error("Booking captcha failed after all retries")
        return False

    def _get_review_captcha_b64(self) -> Optional[str]:
        """Get the base64 captcha from the review page."""
        page = self.engine.page

        # Try common selectors first
        for sel in [
            "app-captcha img",
            ".captcha-img",
            "img.captcha-img",
            '.captcha-container img',
            'img[alt*="captcha" i]',
            'img[src^="data:image"]',
        ]:
            try:
                src = self.engine.get_attribute(sel, "src")
                if src and src.startswith("data:image") and len(src) > 200:
                    b64 = src.split(",", 1)[-1] if "," in src else src
                    debug(f"Review captcha found via {sel} ({len(b64)} chars)")
                    return b64
            except Exception:
                continue

        # JS fallback: scan all visible imgs for data:image with reasonable size
        try:
            result = page.evaluate("""() => {
                const imgs = Array.from(document.querySelectorAll('img'));
                for (const img of imgs) {
                    const src = img.src || '';
                    if (src.startsWith('data:image') && src.length > 200 && img.offsetHeight > 10) {
                        return src;
                    }
                }
                return null;
            }""")
            if result:
                b64 = result.split(",", 1)[-1] if "," in result else result
                debug(f"Review captcha found via JS scan ({len(b64)} chars)")
                return b64
        except Exception:
            pass

        return None

    def _refresh_booking_captcha(self):
        for sel in [".captcha-img", 'a:has-text("Refresh")', ".fa-refresh"]:
            if self.engine.wait_and_click(sel, timeout=2000):
                self.engine.wait(1500)
                return

    def _click_make_payment(self) -> bool:
        page = self.engine.page

        # Scroll to bottom first to ensure button is visible
        try:
            page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            self.engine.wait(300)
        except Exception:
            pass

        # Try specific selectors with short timeouts  prioritize Pay & Book,
        # then fall back to the Continue/submit button on the review page
        for sel in [
            'button.mob-bot-btn:has-text("Pay")',
            'button:has-text("Pay & Book")',
            'button:has-text("Pay and Book")',
            'button:has-text("Make Payment")',
            'button.train_Search',
        ]:
            try:
                loc = page.locator(sel).first
                if loc.is_visible(timeout=1500):
                    loc.click(force=True, timeout=2000)
                    debug(f"Make Payment clicked via {sel}")
                    return True
            except Exception:
                continue

        # JS fallback: find the correct submit button (NOT the fare summary OK)
        try:
            clicked = page.evaluate("""() => {
                const btns = Array.from(document.querySelectorAll('button'));
                // Priority 1: button with pay/book text
                for (const b of btns) {
                    const t = (b.innerText || '').trim().toLowerCase();
                    if ((t.includes('pay') && t.includes('book')) || t.includes('make payment')) {
                        b.click();
                        return 'pay_' + b.innerText.trim().substring(0, 30);
                    }
                }
                // Priority 2: submit button with train_Search class (review page Continue)
                const trainSearch = document.querySelector('button.train_Search[type="submit"]');
                if (trainSearch) {
                    trainSearch.click();
                    return 'submit_' + trainSearch.innerText.trim().substring(0, 30);
                }
                // Priority 3: any type=submit button that is NOT in footer/nav
                for (const b of btns) {
                    const t = (b.innerText || '').trim().toLowerCase();
                    const cls = (b.className || '').toLowerCase();
                    if (b.type === 'submit' && !cls.includes('btn_tab') && t !== 'ok' && b.offsetHeight > 0) {
                        b.click();
                        return 'generic_submit_' + b.innerText.trim().substring(0, 30);
                    }
                }
                return null;
            }""")
            if clicked:
                debug(f"Make Payment clicked via JS: '{clicked}'")
                return True
        except Exception as e:
            debug(f"JS Make Payment click error: {e}")

        error("Make Payment button not found")
        self.engine.screenshot("make_payment_missing")
        return False


