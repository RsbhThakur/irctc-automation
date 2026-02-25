"""
IRCTC Train Ticket Booking - Train Search & Selection (Browser-based)
Fills the search form on the IRCTC Angular SPA, submits it,
finds the target train in the results, and clicks Book Now.

Flow:
1. Fill From station (PrimeNG AutoComplete)
2. Fill To station
3. Set journey date (PrimeNG Calendar)
4. Set coach class (PrimeNG Dropdown)
5. Set quota (General / Tatkal / )
6. Click Search
7. Wait for results page
8. Locate target train by number
9. Click "Book Now" on the matching class
"""

import json
import re
import time
from datetime import datetime
from typing import Optional

from src.browser_engine import BrowserEngine
from src.utils import log, warn, error, success, debug, error_with_trace


class TrainSearch:
    """Browser-based train search and selection."""

    def __init__(self, engine: BrowserEngine, config: dict):
        self.engine = engine
        self.config = config
        self.book_now_retry_seconds = float(config.get("BOOK_NOW_RETRY_SECONDS", 2))
        self.book_now_start_time_str = str(config.get("BOOK_NOW_START_TIME", "")).strip()
        self.book_now_start_time = self._parse_time_hms(self.book_now_start_time_str)
        self._last_target_card_idx: Optional[int] = None
        self._last_target_coach: Optional[str] = None

    #  Public 

    def search_and_select(self) -> bool:
        """
        Fill the search form, submit, find the target train,
        and click Book Now.  Returns True on success.
        """
        log(f"Searching train {self.config['TRAIN_NO']}...")
        log(f"  Route : {self.config['SOURCE_STATION']}  {self.config['DESTINATION_STATION']}")
        log(f"  Date  : {self.config['TRAVEL_DATE']} | Class: {self.config['TRAIN_COACH']}")

        if not self._fill_search_form():
            return False

        # If form auto-submitted (persistent profile), skip clicking Search
        if "train-list" not in self.engine.page.url:
            if not self._click_search():
                return False

        if not self._wait_for_results():
            return False

        if not self._select_train():
            return False

        # Handle any post-Book-Now dialogs (WL confirmation, etc.)
        self._handle_post_book_dialogs()

        return True

    #  Form Filling 

    def _fill_search_form(self) -> bool:
        """Fill all fields on the train search form."""
        engine = self.engine

        # Ensure we're on the search page
        url = engine.page.url
        if "train-list" in url:
            engine.goto("https://www.irctc.co.in/nget/train-search")
            engine.wait(1000)
        elif "train-search" not in url:
            engine.goto("https://www.irctc.co.in/nget/train-search")
            engine.wait(1000)

        engine.dismiss_popups()

        #  Clear pre-filled form values (persistent profile caching) 
        try:
            for sel in [
                "p-autocomplete#origin input",
                "p-autocomplete#destination input",
            ]:
                el = engine.page.query_selector(sel)
                if el and el.is_visible():
                    val = el.input_value() or ""
                    if val.strip():
                        el.fill("")
                        debug(f"Cleared pre-filled value in {sel}")
        except Exception:
            pass

        #  Journey date (set BEFORE stations to prevent auto-submit) 
        if not self._set_date(self.config["TRAVEL_DATE"]):
            error("Failed to set journey date")
            engine.screenshot("date_fail")
            return False

        #  Class 
        if not self._set_class(self.config["TRAIN_COACH"]):
            warn("Could not set class via dropdown  may use default")

        #  From station 
        if not self._select_station("from", self.config["SOURCE_STATION"]):
            error("Failed to select From station")
            engine.screenshot("from_fail")
            return False

        #  To station 
        if not self._select_station("to", self.config["DESTINATION_STATION"]):
            error("Failed to select To station")
            engine.screenshot("to_fail")
            return False

        #  Quota 
        quota_text = self._quota_display_name()
        if quota_text != "GENERAL":
            if not self._set_quota(quota_text):
                warn(f"Could not set quota to {quota_text}")

        # Check if form auto-submitted (persistent profile issue)
        url = engine.page.url
        if "train-list" in url:
            debug("Form auto-submitted  proceeding to results directly")
            return True

        engine.screenshot("search_form_filled")
        log("Search form filled")
        return True

    def _select_station(self, which: str, code: str) -> bool:
        """
        Fill a PrimeNG AutoComplete station input and pick the first match.
        *which* is "from" or "to".
        """
        debug(f"Selecting {which} station: {code}")

        # The IRCTC search form has two autocompletes.
        # Strategy: target by placeholder or by position.
        selectors = {
            "from": [
                'p-autocomplete#origin input',
                'p-autocomplete:nth-of-type(1) input[role="searchbox"]',
                'input[aria-label*="From" i]',
                'input[placeholder*="From" i]',
                # Positional fallback: first autocomplete input on the page
                'p-autocomplete input',
            ],
            "to": [
                'p-autocomplete#destination input',
                'p-autocomplete#destStn input',
                'p-autocomplete:nth-of-type(2) input[role="searchbox"]',
                'input[aria-label*="To" i]',
                'input[placeholder*="To" i]',
            ],
        }

        # -- Find and type into the correct input --
        typed = False
        for sel in selectors.get(which, []):
            try:
                el = self.engine.page.query_selector(sel)
                if el and el.is_visible():
                    el.click()
                    el.fill("")
                    el.type(code, delay=40)
                    debug(f"Typed '{code}' into {sel}")
                    typed = True
                    break
            except Exception:
                continue

        if not typed:
            # Absolute fallback: use nth autocomplete (0 = from, 1 = to)
            idx = 0 if which == "from" else 1
            try:
                inputs = self.engine.page.query_selector_all(
                    'p-autocomplete input[role="searchbox"], p-autocomplete input[type="text"]'
                )
                if len(inputs) > idx:
                    inputs[idx].click()
                    inputs[idx].fill("")
                    inputs[idx].type(code, delay=40)
                    debug(f"Typed '{code}' into autocomplete #{idx}")
                    typed = True
            except Exception as e:
                debug(f"Autocomplete fallback failed: {e}")

        if not typed:
            return False

        # -- Wait for and click the suggestion dropdown --
        self.engine.wait(300)  # let the API fetch suggestions

        option_selectors = [
            '[role="option"]',
            ".ui-autocomplete-list-item",
            "p-autocomplete-item",
            ".p-autocomplete-item",
            "li.p-autocomplete-item",
        ]

        for osel in option_selectors:
            try:
                items = self.engine.page.query_selector_all(osel)
                for item in items:
                    if item.is_visible():
                        txt = item.inner_text()
                        if code.upper() in txt.upper():
                            item.click()
                            debug(f"Selected '{txt.strip()}' for {which} station")
                            return True
                # If code wasn't in text, just click the first visible option
                for item in items:
                    if item.is_visible():
                        item.click()
                        debug(f"Selected first visible option for {which}")
                        return True
            except Exception:
                continue

        warn(f"No autocomplete suggestion found for {code}")
        return False

    def _set_date(self, date_str: str) -> bool:
        """
        Set travel date. Optimized: type date directly into the input
        instead of clicking arrows to navigate month-by-month.
        PrimeNG p-calendar accepts typed dates, then Tab commits them.
        """
        debug(f"Setting date: {date_str}")
        try:
            dt = datetime.strptime(date_str, "%d/%m/%Y")
        except ValueError:
            error(f"Invalid date format: {date_str}")
            return False

        cal_selectors = [
            "p-calendar input",
            'input[formcontrolname="jDate"]',
            'input[placeholder*="Date" i]',
            'input[name="jDate"]',
        ]

        for sel in cal_selectors:
            try:
                el = self.engine.page.query_selector(sel)
                if el and el.is_visible():
                    # Strategy 1 (fastest): Clear + type date + Tab
                    # IRCTC p-calendar accepts DD/MM/YYYY or similar format
                    el.click()
                    self.engine.wait(200)

                    # Select all existing text and replace
                    el.press("Control+a")
                    self.engine.wait(50)

                    # Type the date string directly
                    el.type(date_str, delay=15)
                    self.engine.wait(200)

                    # Press Escape to close calendar popup, then Tab to commit
                    try:
                        self.engine.page.keyboard.press("Escape")
                    except Exception:
                        pass
                    self.engine.wait(100)
                    el.press("Tab")
                    self.engine.wait(200)

                    # Verify the value was accepted
                    val = el.input_value()
                    if val and len(val) >= 8:
                        debug(f"Date typed directly: {date_str} (input shows: {val})")
                        return True

                    # Strategy 2: If direct type failed, try JS-based approach
                    debug(f"Direct type gave: '{val}'  trying JS setValue")
                    try:
                        # Angular date format might be different, use JS to set
                        self.engine.page.evaluate(f"""(dateStr) => {{
                            const inp = document.querySelector('{sel}');
                            if (inp) {{
                                inp.value = dateStr;
                                inp.dispatchEvent(new Event('input', {{bubbles: true}}));
                                inp.dispatchEvent(new Event('change', {{bubbles: true}}));
                            }}
                        }}""", date_str)
                        self.engine.wait(200)
                        debug(f"Date set via JS: {date_str}")
                    except Exception:
                        pass

                    # Strategy 3: Fall back to calendar picker navigation
                    el.click()
                    self.engine.wait(300)
                    if self._pick_date_from_calendar(dt):
                        debug(f"Date set via calendar picker: {date_str}")
                        return True

                    return True
            except Exception as e:
                debug(f"Date set failed for {sel}: {e}")
                continue

        warn("Date input not found")
        return False

    def _pick_date_from_calendar(self, dt: datetime) -> bool:
        """Navigate the PrimeNG popup calendar to pick a date."""
        try:
            max_clicks = 24
            for _ in range(max_clicks):
                # Read current month/year from the header
                title = None
                # Try JS first (fastest)
                try:
                    title = self.engine.page.evaluate("""() => {
                        const el = document.querySelector('.ui-datepicker-title, .p-datepicker-title');
                        return el ? el.innerText : null;
                    }""")
                except Exception:
                    pass

                if not title:
                    for hsel in [
                        ".ui-datepicker-title",
                        ".p-datepicker-title",
                        ".ui-datepicker-header .ui-datepicker-title",
                    ]:
                        title = self.engine.get_text(hsel)
                        if title and title.strip():
                            break

                if not title:
                    debug("Calendar title not found")
                    return False

                debug(f"Calendar title: {title.strip()}")

                cal_month, cal_year = self._parse_calendar_title(title.strip())
                if cal_month and cal_year:
                    if cal_year == dt.year and cal_month == dt.month:
                        # Correct month  click the day
                        result = self._click_day(dt.day)
                        if result:
                            # Close calendar by clicking outside or pressing Escape
                            self.engine.wait(500)
                            try:
                                self.engine.page.keyboard.press("Escape")
                            except Exception:
                                pass
                            self.engine.wait(500)
                        return result

                    # Navigate forward or backward
                    target = dt.year * 12 + dt.month
                    current = cal_year * 12 + cal_month
                    if target > current:
                        self._click_calendar_next()
                    else:
                        self._click_calendar_prev()
                    self.engine.wait(200)
                else:
                    # Can't parse  just try clicking the day
                    return self._click_day(dt.day)

            warn("Exceeded max calendar navigation clicks")
            return False

        except Exception as e:
            debug(f"Calendar navigation error: {e}")
            return False

    def _parse_calendar_title(self, title: str):
        """Parse calendar header like 'April 2026', 'April2026', 'February 2026'.
        Returns (month_int, year_int)."""
        import re
        months = {
            "january": 1, "february": 2, "march": 3, "april": 4,
            "may": 5, "june": 6, "july": 7, "august": 8,
            "september": 9, "october": 10, "november": 11, "december": 12,
        }
        t = title.lower().strip()
        month_num = None
        year_num = None

        # Try regex for concatenated "February2026" or spaced "February 2026"
        m = re.match(r'([a-z]+)\s*(\d{4})', t)
        if m:
            mon_str = m.group(1)
            yr_str = m.group(2)
            month_num = months.get(mon_str)
            year_num = int(yr_str)
        else:
            # Fallback: split on whitespace
            parts = t.split()
            for p in parts:
                p_clean = p.strip().rstrip(",")
                if p_clean in months:
                    month_num = months[p_clean]
                elif p_clean.isdigit() and len(p_clean) == 4:
                    year_num = int(p_clean)
        return month_num, year_num

    def _click_day(self, day: int) -> bool:
        """Click a specific day number in the open calendar."""
        # PrimeNG day cells: <td><span class="p-ripple ...">23</span></td>
        for sel in [
            f'td:not(.p-datepicker-other-month) span:text-is("{day}")',
            f'td:not(.p-disabled) a:text-is("{day}")',
            f'td:not(.ui-datepicker-other-month) a:text-is("{day}")',
            f'td span:text-is("{day}")',
        ]:
            try:
                el = self.engine.page.query_selector(sel)
                if el and el.is_visible():
                    el.click()
                    debug(f"Clicked day {day}")
                    return True
            except Exception:
                continue

        # Fallback: find all visible spans/anchors inside table cells
        try:
            cells = self.engine.page.query_selector_all("td span, td a")
            for c in cells:
                txt = (c.inner_text() or "").strip()
                if txt == str(day) and c.is_visible():
                    c.click()
                    debug(f"Clicked day {day} (fallback)")
                    return True
        except Exception:
            pass

        warn(f"Day {day} not found in calendar")
        return False

    def _click_calendar_next(self):
        for sel in [
            '.ui-datepicker-next',
            'a.ui-datepicker-next',
            'button.p-datepicker-next',
            '.p-datepicker-next-icon',
            'button[aria-label="Next Month"]',
        ]:
            if self.engine.wait_and_click(sel, timeout=1000):
                return

    def _click_calendar_prev(self):
        for sel in [
            '.ui-datepicker-prev',
            'a.ui-datepicker-prev',
            'button.p-datepicker-prev',
            '.p-datepicker-prev-icon',
            'button[aria-label="Previous Month"]',
        ]:
            if self.engine.wait_and_click(sel, timeout=1000):
                return

    def _set_class(self, coach_code: str) -> bool:
        """Select journey class from the dropdown."""
        debug(f"Setting class: {coach_code}")

        class_labels = {
            "SL": "Sleeper",    "2A": "AC 2 Tier",   "3A": "AC 3 Tier",
            "3E": "AC 3 Economy", "1A": "AC First Class", "CC": "AC Chair Car",
            "EC": "Exec Chair Car", "2S": "Second Sitting",
        }
        label = class_labels.get(coach_code, coach_code)

        # Click the class dropdown
        dropdown_selectors = [
            'p-dropdown[formcontrolname="journeyClass"]',
            'p-dropdown[formcontrolname="jClass"]',
            'p-dropdown:has-text("All Classes")',
            'p-dropdown:has-text("Class")',
        ]

        for dsel in dropdown_selectors:
            try:
                el = self.engine.page.query_selector(dsel)
                if el and el.is_visible():
                    el.click()
                    self.engine.wait(300)
                    # Now select the option
                    for search_text in [label, coach_code, f"({coach_code})"]:
                        try:
                            opt = self.engine.page.get_by_text(search_text, exact=False)
                            opt.first.click(timeout=3000)
                            debug(f"Class selected: {search_text}")
                            return True
                        except Exception:
                            continue
            except Exception:
                continue

        return False

    def _set_quota(self, quota_text: str) -> bool:
        """Select the quota (TATKAL / PREMIUM TATKAL / etc.)."""
        debug(f"Setting quota: {quota_text}")

        # Quota might be a dropdown or radio button group
        dropdown_selectors = [
            'p-dropdown[formcontrolname="quotaCode"]',
            'p-dropdown[formcontrolname="quota"]',
            'p-dropdown:has-text("GENERAL")',
        ]

        for dsel in dropdown_selectors:
            try:
                el = self.engine.page.query_selector(dsel)
                if el and el.is_visible():
                    el.click()
                    self.engine.wait(500)
                    self.engine.page.get_by_text(quota_text, exact=False).first.click(
                        timeout=3000)
                    debug(f"Quota selected: {quota_text}")
                    return True
            except Exception:
                continue

        # Try radio buttons
        try:
            self.engine.page.get_by_label(quota_text, exact=False).first.click(
                timeout=3000)
            return True
        except Exception:
            pass

        return False

    def _quota_display_name(self) -> str:
        if self.config.get("TATKAL"):
            return "TATKAL"
        if self.config.get("PREMIUM_TATKAL"):
            return "PREMIUM TATKAL"
        return self.config.get("QUOTA", "GENERAL")

    #  Search & Results 

    def _click_search(self) -> bool:
        """Click the search / find-trains button."""
        # Dismiss any overlay that might block the button
        self.engine.dismiss_popups()
        self.engine.wait(500)

        # Use force_click (JS) first since overlays often block normal clicks
        for sel in [
            'button[type="submit"]:has-text("Search")',
            'button:has-text("Find Trains")',
            'button:has-text("Search")',
            'button.search_btn',
            'button[type="submit"]',
        ]:
            if self.engine.force_click(sel, timeout=5000):
                debug(f"Search force-clicked via {sel}")
                self.engine.wait(1000)
                return True

        # Fallback: try Enter key
        try:
            self.engine.page.keyboard.press("Enter")
            self.engine.wait(1000)
            return True
        except Exception:
            pass

        error("Search button not found")
        self.engine.screenshot("search_btn_fail")
        return False

    def _wait_for_results(self) -> bool:
        """Wait for the train results page to load."""
        log("Waiting for search results...")

        if self.engine.wait_for_url("train-list", timeout=30_000):
            debug("URL now contains train-list")
        else:
            debug("URL did not change to train-list  checking for results anyway")

        self.engine.dismiss_popups()

        # Wait for train cards to render  poll quickly, skip networkidle
        result_indicators = [
            ".bull-back",             # train card (fastest to appear)
            ".train-heading",         # train name/number header
            'button:has-text("Book Now")',
        ]
        import time as _t
        deadline = _t.time() + 12.0
        while _t.time() < deadline:
            for ind in result_indicators:
                if self.engine.is_visible(ind, timeout=500):
                    log("Search results loaded")
                    return True
            self.engine.wait(300)

        # Check for "no trains found" message
        try:
            body = self.engine.page.inner_text("body").lower()
            if "no train" in body or "not found" in body:
                error("No trains found for the given route/date!")
                return False
        except Exception:
            pass

        warn("Could not confirm results loaded  proceeding anyway")
        return True

    def _select_train(self) -> bool:
        """Find the target train and click Book Now."""
        train_no = self.config["TRAIN_NO"]
        coach = self.config["TRAIN_COACH"]
        log(f"Looking for train {train_no} class {coach}...")

        self.engine.dismiss_popups()
        page = self.engine.page

        #  Find the exact app-train-avl-enq card that contains our train number 
        target_card_idx = None
        try:
            card_count = page.evaluate("""(trainNo) => {
                const cards = document.querySelectorAll('app-train-avl-enq');
                const results = [];
                for (let i = 0; i < cards.length; i++) {
                    const text = cards[i].innerText || '';
                    results.push({ idx: i, hasTrainNo: text.includes(trainNo), preview: text.substring(0, 80) });
                }
                return results;
            }""", train_no)
            debug(f"Train cards found: {len(card_count)}")
            for info in card_count:
                debug(f"  Card {info['idx']}: hasTrainNo={info['hasTrainNo']}, preview='{info['preview']}'")
                if info['hasTrainNo'] and target_card_idx is None:
                    target_card_idx = info['idx']
        except Exception as e:
            debug(f"Card scan error: {e}")

        if target_card_idx is None:
            # Fallback: try .bull-back cards
            try:
                cards = page.query_selector_all('.bull-back')
                for i, card in enumerate(cards):
                    text = card.inner_text() or ""
                    if train_no in text:
                        debug(f"Found train {train_no} in .bull-back card #{i}")
                        target_card_idx = i
                        break
            except Exception as e:
                debug(f"Bull-back card scan error: {e}")

        if target_card_idx is None:
            error(f"Train {train_no} not found in search results")
            self.engine.screenshot("train_not_found")
            return False

        self._last_target_card_idx = target_card_idx
        self._last_target_coach = coach
        debug(f"Target train {train_no} found at card index {target_card_idx}")
        self._wait_for_book_now_start_time_if_configured()
        return self._click_book_now_in_card(target_card_idx, coach)

    def _handle_post_book_dialogs(self):
        """Handle dialogs that appear after clicking Book Now AND wait
        for navigation to the passenger-input page (/psgninput).

        Polls in a loop for up to 60s:
        - Clicks any confirmation dialogs (Yes / OK / Confirm / Proceed)
        - Checks URL for 'psgninput'
        - Checks for boardingStationEnq API intercept
        """
        import time as _time
        page = self.engine.page
        deadline = _time.time() + 60.0
        loop_count = 0
        retry_book_now_mode = False
        last_book_retry_ts = 0.0

        while _time.time() < deadline:
            loop_count += 1

            #  Check if already on passenger page 
            try:
                url = page.url
                if "psgninput" in url:
                    debug("Navigated to passenger input page")
                    return
            except Exception:
                pass

            #  Check for boardingStationEnq API (confirms navigation) 
            bse = self.engine.get_intercepted("boardingStationEnq")
            if bse and bse.get("status") == 200:
                debug("boardingStationEnq  200, passenger page loading")
                self.engine.wait(1000)
                return

            #  Log current state periodically 
            if loop_count % 10 == 0:
                try:
                    debug(f"Post-book poll #{loop_count}: URL={page.url.split('/')[-1]}")
                    # Take screenshot every 20 iterations
                    if loop_count % 20 == 0:
                        self.engine.screenshot(f"post_book_wait_{loop_count}")
                except Exception:
                    pass

            #  Click any confirmation dialogs 
            dialog_found = False
            for btn_text in ['Yes', 'OK', 'Confirm', 'Proceed', 'Continue',
                             'BOOK NOW', 'Book Now']:
                try:
                    btn = page.locator(
                        f'.ui-dialog button:has-text("{btn_text}"), '
                        f'.p-dialog button:has-text("{btn_text}"), '
                        f'.cdk-overlay-pane button:has-text("{btn_text}"), '
                        f'.ui-dialog-content button:has-text("{btn_text}")'
                    )
                    if btn.count() > 0 and btn.first.is_visible():
                        btn.first.click(timeout=2000)
                        debug(f"Clicked post-book dialog: {btn_text}")
                        dialog_found = True
                        self.engine.wait(1000)
                        break
                except Exception:
                    continue

            if not dialog_found:
                # Also try clicking any visible button inside a dialog overlay
                try:
                    overlays = page.locator(
                        '.ui-dialog, .p-dialog, .cdk-overlay-pane'
                    )
                    for i in range(overlays.count()):
                        overlay = overlays.nth(i)
                        if overlay.is_visible():
                            btns = overlay.locator('button')
                            for j in range(btns.count()):
                                b = btns.nth(j)
                                if b.is_visible():
                                    text = (b.inner_text() or "").strip()
                                    if text and text.upper() not in ['CLOSE', 'CANCEL', 'X', 'NO']:
                                        b.click(timeout=2000)
                                        debug(f"Clicked overlay button: {text}")
                                        dialog_found = True
                                        self.engine.wait(1000)
                                        break
                            if dialog_found:
                                break
                except Exception:
                    pass

            dialog_text = self._get_visible_dialog_text()
            if dialog_text and self._looks_like_booking_not_started(dialog_text):
                retry_book_now_mode = True
                debug("Detected booking-not-started popup; enabling periodic Book Now retries")
            elif dialog_text and self._looks_like_select_class_prompt(dialog_text):
                retry_book_now_mode = True
                debug("Detected 'Please select class' popup; enabling class-aware Book Now retries")

            # If Book Now is still visible on train-list page (even disabled),
            # keep periodic retries active.
            if not retry_book_now_mode and self._has_visible_book_now_button():
                retry_book_now_mode = True
                debug("Book Now still visible on results page; enabling periodic retry mode")

            now_ts = _time.time()
            if retry_book_now_mode and (now_ts - last_book_retry_ts) >= self.book_now_retry_seconds:
                if self._book_now_time_reached() and self._retry_click_book_now():
                    debug(f"Re-clicked Book Now after popup (every {self.book_now_retry_seconds:.1f}s)")
                last_book_retry_ts = now_ts

            self.engine.wait(500)

        debug("Post-book dialog handling timed out (60s)")
        self.engine.screenshot("post_book_timeout")

    def _get_visible_dialog_text(self) -> str:
        try:
            txt = self.engine.page.evaluate("""() => {
                const dialogs = document.querySelectorAll('.ui-dialog, .p-dialog, .cdk-overlay-pane');
                const parts = [];
                dialogs.forEach(d => {
                    if (d.offsetHeight > 0) {
                        const t = (d.innerText || '').trim();
                        if (t) parts.push(t);
                    }
                });
                return parts.join('\\n');
            }""")
            return (txt or "").strip().lower()
        except Exception:
            return ""

    def _looks_like_booking_not_started(self, text: str) -> bool:
        phrases = [
            "booking not started",
            "booking is not started",
            "not started yet",
            "reservation not started",
            "booking has not started",
            "booking will start",
            "not opened yet",
        ]
        return any(p in text for p in phrases)

    def _looks_like_select_class_prompt(self, text: str) -> bool:
        phrases = [
            "please select class",
            "select class",
            "select quota",
            "selected quota and class",
        ]
        return any(p in text for p in phrases)

    def _retry_click_book_now(self) -> bool:
        page = self.engine.page

        # Best retry path: re-prime the same train card/class and click Book Now.
        if self._last_target_card_idx is not None and self._last_target_coach:
            try:
                return self._click_book_now_in_card(
                    self._last_target_card_idx,
                    self._last_target_coach,
                )
            except Exception:
                pass

        try:
            # First try regular visible Book Now buttons.
            btns = page.locator('button:has-text("Book Now"):visible')
            count = btns.count()
            for i in range(count):
                btn = btns.nth(i)
                if btn.is_visible():
                    btn.click(force=True, timeout=2000)
                    return True
        except Exception:
            pass

        # Then force-trigger disabled Book Now buttons via JS.
        try:
            clicked = page.evaluate("""() => {
                const candidates = Array.from(document.querySelectorAll('button'))
                    .filter(b => (b.innerText || '').toUpperCase().includes('BOOK NOW'));
                for (const b of candidates) {
                    b.disabled = false;
                    b.removeAttribute('disabled');
                    b.classList.remove('disable-book');
                    b.click();
                    return true;
                }
                return false;
            }""")
            return bool(clicked)
        except Exception:
            return False

    def _has_visible_book_now_button(self) -> bool:
        """True if train-list still shows any Book Now button (enabled or disabled)."""
        page = self.engine.page
        try:
            return bool(page.evaluate("""() => {
                const btns = Array.from(document.querySelectorAll('button'))
                    .filter(b => (b.innerText || '').toUpperCase().includes('BOOK NOW'));
                return btns.some(b => b.offsetHeight > 0 && b.offsetWidth > 0);
            }"""))
        except Exception:
            return False

    def _parse_time_hms(self, s: str):
        s = (s or "").strip()
        if not s:
            return None
        for fmt in ("%H:%M:%S", "%H:%M"):
            try:
                return datetime.strptime(s, fmt).time()
            except ValueError:
                continue
        return None

    def _book_now_time_reached(self) -> bool:
        """Best-effort time gate using IRCTC page clock, fallback to local time."""
        if not self.book_now_start_time:
            return True
        now_dt = self._get_irctc_screen_time()
        return now_dt.time() >= self.book_now_start_time

    def _wait_for_book_now_start_time_if_configured(self):
        """Block until BOOK_NOW_START_TIME before any Book Now click attempts."""
        if not self.book_now_start_time:
            return
        log(f"BOOK_NOW_START_TIME enabled: waiting for {self.book_now_start_time.strftime('%H:%M:%S')}")
        while True:
            now_dt = self._get_irctc_screen_time()
            if now_dt.time() >= self.book_now_start_time:
                log(f"Book Now gate opened at {now_dt.strftime('%H:%M:%S')}")
                return
            debug(
                f"Book Now gated: current {now_dt.strftime('%H:%M:%S')} "
                f"< target {self.book_now_start_time.strftime('%H:%M:%S')}"
            )
            self.engine.wait(300)

    def _get_irctc_screen_time(self) -> datetime:
        """Read IRCTC server/screen time; fallback to local clock."""
        # 1) Prefer intercepted IRCTC server-time API payload.
        try:
            txt = self.engine.get_intercepted("textToNumber")
            body = (txt or {}).get("body", "")
            if body:
                try:
                    data = json.loads(body)
                    for key in ("time", "serverTime", "currentTime"):
                        v = data.get(key)
                        if isinstance(v, str):
                            parsed = self._parse_time_hms(v)
                            if parsed:
                                return datetime.now().replace(
                                    hour=parsed.hour,
                                    minute=parsed.minute,
                                    second=parsed.second,
                                    microsecond=0
                                )
                        if isinstance(v, (int, float)) and v > 1_000_000_000:
                            ts = float(v)
                            if ts > 1_000_000_000_000:
                                ts = ts / 1000.0
                            return datetime.fromtimestamp(ts)
                except Exception:
                    pass

                m = re.search(r"\b(\d{2}:\d{2}(?::\d{2})?)\b", body)
                if m:
                    parsed = self._parse_time_hms(m.group(1))
                    if parsed:
                        return datetime.now().replace(
                            hour=parsed.hour,
                            minute=parsed.minute,
                            second=parsed.second,
                            microsecond=0
                        )
        except Exception:
            pass

        # 2) Fallback to strict clock-like selectors only (avoid train schedule times).
        try:
            time_str = self.engine.page.evaluate("""() => {
                const selectors = [
                    '#clock', '.clock', '[id*="clock" i]', '[class*="clock" i]',
                    '[data-testid*="clock" i]'
                ];
                for (const sel of selectors) {
                    for (const el of document.querySelectorAll(sel)) {
                        if (!el || el.offsetHeight <= 0) continue;
                        const txt = (el.innerText || '').trim();
                        const m = txt.match(/\\b\\d{2}:\\d{2}(:\\d{2})?\\b/);
                        if (m) return m[0];
                    }
                }
                return null;
            }""")
            if time_str:
                parsed = self._parse_time_hms(str(time_str))
                if parsed:
                    return datetime.now().replace(
                        hour=parsed.hour,
                        minute=parsed.minute,
                        second=parsed.second,
                        microsecond=0
                    )
        except Exception:
            pass
        return datetime.now()

    def _find_parent_card(self, element):
        """Walk up the DOM to find the train card container."""
        try:
            # Use JS to walk up
            return self.engine.page.evaluate_handle("""
                (el) => {
                    let node = el;
                    for (let i = 0; i < 15; i++) {
                        node = node.parentElement;
                        if (!node) return null;
                        // Look for common card container classes
                        const cls = node.className || '';
                        if (cls.includes('bull-back') || cls.includes('train-listing')
                            || cls.includes('trainComponent') || node.tagName === 'APP-TRAIN-AVL-ENQ') {
                            return node;
                        }
                    }
                    return node;  // return whatever we walked to
                }
            """, element)
        except Exception:
            return None

    def _click_book_now_in_card(self, card_idx: int, coach: str) -> bool:
        """
        Within a specific train card (by index), use Playwright native clicks:
        1. Click the class 'Refresh' cell for the desired coach
        2. Wait for avlFarenquiry API
        3. Click the tab for the class
        4. Click first bookable availability cell
        5. Click Book Now

        All queries are scoped to the nth app-train-avl-enq card.
        """
        try:
            label_map = {
                "SL": "Sleeper (SL)", "2A": "AC 2 Tier (2A)",
                "3A": "AC 3 Tier (3A)", "3E": "AC 3 Economy (3E)",
                "1A": "AC First Class (1A)", "CC": "Chair Car (CC)",
                "EC": "Exec. Chair Car (EC)", "2S": "Second Sitting (2S)",
            }
            full_label = label_map.get(coach, coach)
            short_label = coach
            page = self.engine.page

            # Get a locator scoped to this specific train card
            card = page.locator('app-train-avl-enq').nth(card_idx)

            #  Step 1: Click the class cell containing "Refresh" 
            refresh_clicked = False
            try:
                pre_avls = card.locator('div.pre-avl')
                count = pre_avls.count()
                for i in range(count):
                    pa = pre_avls.nth(i)
                    text = pa.inner_text() or ""
                    if (full_label in text or f"({short_label})" in text) and "Refresh" in text:
                        pa.click(timeout=3000)
                        debug(f"Clicked class cell: {text.strip()[:30]}")
                        refresh_clicked = True
                        break
            except Exception as e:
                debug(f"Step 1 class click: {e}")

            if not refresh_clicked:
                debug(f"No 'Refresh' cell for {short_label}  may already be expanded")

            # Wait for avlFarenquiry API response
            self.engine.wait(800)

            #  Step 2: Click the tab for this class 
            # Tabs are scoped within this card
            try:
                tabs = card.locator('p-tabmenu li[role="tab"] a, .ui-tabmenu li a')
                tab_count = tabs.count()
                for i in range(tab_count):
                    tab = tabs.nth(i)
                    text = tab.inner_text() or ""
                    if short_label in text:
                        tab.click(timeout=2000)
                        debug(f"Clicked tab: {text.strip()[:30]}")
                        break
            except Exception as e:
                debug(f"Step 2 tab click: {e}")

            self.engine.wait(500)

            #  Step 3: Click first bookable availability cell 
            # Poll until availability cells appear (API may be slow)
            SKIP = ['REGRET', 'TRAIN CANCELLED']
            BOOK = ['WL', 'RAC', 'AVAILABLE', 'GNWL', 'RLWL', 'PQWL']
            DAYS = ['MON', 'TUE', 'WED', 'THU', 'FRI', 'SAT', 'SUN']
            avail_clicked = False

            import time as _t
            avail_deadline = _t.time() + 6.0
            td_count = 0
            while _t.time() < avail_deadline:
                try:
                    tds = card.locator('td.link')
                    td_count = tds.count()
                    if td_count > 0:
                        break
                except Exception:
                    pass
                self.engine.wait(400)

            try:
                tds = card.locator('td.link')
                td_count = tds.count()

                # First pass: bookable statuses
                for i in range(td_count):
                    td = tds.nth(i)
                    try:
                        text = (td.inner_text() or "").upper()
                    except Exception:
                        continue
                    if any(k in text for k in SKIP):
                        continue
                    if any(k in text for k in BOOK):
                        inner = td.locator('div.pre-avl')
                        if inner.count() > 0:
                            inner.first.click(timeout=3000)
                        else:
                            td.click(timeout=3000)
                        debug(f"Clicked availability: {text.strip()[:30]}")
                        avail_clicked = True
                        break

                # Second pass: click first date cell
                if not avail_clicked:
                    for i in range(td_count):
                        td = tds.nth(i)
                        try:
                            text = (td.inner_text() or "").upper()
                        except Exception:
                            continue
                        if any(k in text for k in SKIP):
                            continue
                        if any(d in text for d in DAYS):
                            inner = td.locator('div.pre-avl')
                            if inner.count() > 0:
                                inner.first.click(timeout=3000)
                            else:
                                td.click(timeout=3000)
                            debug(f"Clicked first date cell: {text.strip()[:30]}")
                            avail_clicked = True
                            break
            except Exception as e:
                debug(f"Step 3 availability click: {e}")

            if not avail_clicked:
                warn("No availability cell found to click")

            self.engine.wait(800)  # Give Angular time to process availability

            #  Pause the auto-popup-killer BEFORE Book Now 
            # It interferes by closing confirmation dialogs via X/Close buttons
            try:
                page.evaluate("""() => {
                    if (window._popupKiller) {
                        clearInterval(window._popupKiller);
                        window._popupKiller = null;
                    }
                }""")
                debug("Paused auto-popup-killer before Book Now")
            except Exception:
                pass

            #  Step 4: Click Book Now (scoped to this card, then fallback to page) 
            for attempt in range(6):
                # On attempt 2+, force-remove disable-book class
                if attempt >= 1:
                    try:
                        page.evaluate(f"""
                            (idx) => {{
                                const cards = document.querySelectorAll('app-train-avl-enq');
                                if (cards[idx]) {{
                                    cards[idx].querySelectorAll('button.disable-book').forEach(btn => {{
                                        const t = (btn.innerText || '').toUpperCase();
                                        if (t.includes('BOOK NOW')) {{
                                            btn.classList.remove('disable-book');
                                            btn.disabled = false;
                                        }}
                                    }});
                                }}
                                // Also global disable-book removal
                                document.querySelectorAll('button.disable-book').forEach(btn => {{
                                    const t = (btn.innerText || '').toUpperCase();
                                    if (t.includes('BOOK NOW')) {{
                                        btn.classList.remove('disable-book');
                                        btn.disabled = false;
                                    }}
                                }});
                            }}
                        """, card_idx)
                    except Exception:
                        pass

                # Diagnostic on first attempt
                if attempt == 0:
                    try:
                        btn_diag = page.evaluate(f"""(idx) => {{
                            const cards = document.querySelectorAll('app-train-avl-enq');
                            const card = cards[idx];
                            const cardBtns = card ? Array.from(card.querySelectorAll('button')).map(b => ({{
                                text: (b.innerText||'').trim().substring(0,30),
                                cls: (b.className||'').substring(0,50),
                                disabled: b.disabled,
                                visible: b.offsetHeight > 0,
                            }})) : [];
                            const allBookBtns = Array.from(document.querySelectorAll('button')).filter(b => 
                                (b.innerText||'').toUpperCase().includes('BOOK NOW')
                            ).map(b => ({{
                                text: (b.innerText||'').trim().substring(0,30), 
                                cls: (b.className||'').substring(0,50),
                                disabled: b.disabled,
                                visible: b.offsetHeight > 0,
                            }}));
                            return {{ cardButtons: cardBtns, allBookNowButtons: allBookBtns }};
                        }}""", card_idx)
                        debug(f"Card buttons: {btn_diag.get('cardButtons', [])}")
                        debug(f"All Book Now buttons on page: {btn_diag.get('allBookNowButtons', [])}")
                    except Exception as e:
                        debug(f"Button diagnostic error: {e}")

                # Try 1: Scoped to card
                try:
                    book_btns = card.locator('button:has-text("Book Now")')
                    count = book_btns.count()
                    for i in range(count):
                        btn = book_btns.nth(i)
                        if btn.is_visible():
                            btn.click(force=True, timeout=3000)
                            log(f"Clicked Book Now for train (class {coach})")
                            return True
                except Exception as e:
                    debug(f"Book Now (scoped) attempt {attempt+1}: {e}")

                # Try 2: Find Book Now globally but ensure it's for the right train
                # After clicking availability, usually only one Book Now is visible
                try:
                    all_book = page.locator('button:has-text("Book Now"):visible')
                    if all_book.count() > 0:
                        all_book.first.click(force=True, timeout=3000)
                        log(f"Clicked Book Now for train (class {coach}) [global visible]")
                        return True
                except Exception as e:
                    debug(f"Book Now (global) attempt {attempt+1}: {e}")

                debug(f"Book Now not clickable (attempt {attempt+1}/6)")
                self.engine.wait(300)

            warn("Book Now could not be clicked after 6 attempts")
            self.engine.screenshot("book_now_fail")
            return False

        except Exception as e:
            debug(f"Book Now flow failed: {e}")
            self.engine.screenshot("book_now_fail")
            return False


