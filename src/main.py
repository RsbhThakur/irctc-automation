"""
IRCTC Train Ticket Booking - Main Orchestrator (Browser-based)
Drives the full booking pipeline through a real browser.

Flow:
  1. Load config & validate
  2. Launch real Edge / Chrome browser
  3. Login (with auto-captcha)
  4. Fill search form  Find Trains  Book Now
  5. Fill passenger details  Review  Captcha
  6. Payment (UPI / Wallet)
  7. Confirm booking & report PNR

Tatkal mode: waits until the booking window opens before searching.
"""

import sys
import time

from src.browser_engine import BrowserEngine
from src.login_handler import LoginHandler
from src.train_search import TrainSearch
from src.booking_form import BookingForm
from src.payment_handler import PaymentHandler
from src.utils import (
    log, warn, error, success, step, debug, error_with_trace,
    load_config, print_booking_summary,
    wait_for_tatkal_time, get_seconds_until_tatkal,
    console, LOG_DIR,
)
from rich.panel import Panel
from rich import box


class IRCTCBookingAutomation:
    """Orchestrate the complete IRCTC ticket booking pipeline."""

    def __init__(self):
        self.config: dict = {}
        self.engine = None

    def run(self) -> bool:
        start_time = time.time()

        console.print(Panel(
            "[bold cyan]IRCTC Train Ticket Booking Automation[/bold cyan]\n"
            "[dim]Real-browser approach  Akamai-proof[/dim]",
            box=box.DOUBLE_EDGE,
            title="[bold green]IRCTC Automator v4.0[/bold green]",
            subtitle="[dim]Browser Mode[/dim]",
        ))

        try:
            #  Step 1: Config 
            step("Step 1: Loading Configuration")
            t = time.time()
            self.config = load_config()
            print_booking_summary(self.config)
            debug(f"Config loaded in {time.time() - t:.2f}s")

            # Initialize browser engine after config so runtime mode is env-driven.
            self.engine = BrowserEngine(
                headless=bool(self.config.get("HEADLESS", False)),
                slow_mo=int(self.config.get("SLOW_MO", 15)),
            )
            log(
                f"Browser runtime: headless={self.config.get('HEADLESS', False)}, "
                f"slow_mo={self.config.get('SLOW_MO', 15)}ms"
            )

            #  Step 2: Launch Browser 
            step("Step 2: Launching Browser")
            t = time.time()
            if not self.engine.launch():
                error("Browser launch failed!")
                return False
            debug(f"Browser launched in {time.time() - t:.2f}s")

            #  Step 3: Navigate & Login 
            step("Step 3: Logging In")
            t = time.time()
            login = LoginHandler(
                engine=self.engine,
                username=self.config["IRCTC_USERNAME"],
                password=self.config["IRCTC_PASSWORD"],
                config=self.config,
            )

            if not login.navigate_to_irctc():
                error("Failed to load IRCTC website")
                return False

            if not login.login(max_retries=5):
                error("Login failed!")
                return False
            debug(f"Login completed in {time.time() - t:.2f}s")

            #  Optional: Tatkal wait 
            if self.config.get("TATKAL") or self.config.get("PREMIUM_TATKAL"):
                secs = get_seconds_until_tatkal(self.config["TRAIN_COACH"])
                if secs > 0:
                    log(f"Tatkal window opens in {int(secs)} seconds  waiting...")
                    while get_seconds_until_tatkal(self.config["TRAIN_COACH"]) > 1:
                        remaining = get_seconds_until_tatkal(self.config["TRAIN_COACH"])
                        if remaining > 30:
                            self.engine.wait(10_000)
                        elif remaining > 5:
                            self.engine.wait(1000)
                        else:
                            self.engine.wait(200)
                    log("Tatkal window OPEN  proceeding!")

            #  Step 4: Search & Select Train 
            step("Step 4: Searching & Selecting Train")
            t = time.time()
            search = TrainSearch(engine=self.engine, config=self.config)

            if not search.search_and_select():
                error("Train search / selection failed!")
                return False
            debug(f"Train selected in {time.time() - t:.2f}s")

            #  Step 5: Fill Passengers & Review 
            step("Step 5: Filling Passenger Details")
            t = time.time()
            booking = BookingForm(engine=self.engine, config=self.config)

            if not booking.fill_and_submit():
                error("Booking form / review failed!")
                return False
            debug(f"Booking form submitted in {time.time() - t:.2f}s")

            #  Step 6: Payment 
            step("Step 6: Processing Payment")
            t = time.time()
            payment = PaymentHandler(engine=self.engine, config=self.config)

            paid = payment.process_payment()
            debug(f"Payment handled in {time.time() - t:.2f}s")

            elapsed = time.time() - start_time

            if paid:
                console.print()
                console.print(Panel(
                    f"[bold green]BOOKING COMPLETED![/bold green]\n\n"
                    f"Train: {self.config['TRAIN_NO']} | Class: {self.config['TRAIN_COACH']}\n"
                    f"Date: {self.config['TRAVEL_DATE']}\n"
                    f"Route: {self.config['SOURCE_STATION']}  {self.config['DESTINATION_STATION']}\n"
                    f"Passengers: {len(self.config['PASSENGER_DETAILS'])}\n\n"
                    f"[dim]Total time: {elapsed:.1f} seconds[/dim]\n"
                    f"[yellow]Check your IRCTC account / UPI app for PNR.[/yellow]",
                    box=box.DOUBLE_EDGE,
                    title="[bold green]SUCCESS[/bold green]",
                ))
                return True
            else:
                warn(f"Payment may not have completed ({elapsed:.0f}s elapsed)")
                warn("Check IRCTC portal  My Transactions for status.")
                return False

        except KeyboardInterrupt:
            warn("Cancelled by user (Ctrl+C)")
            return False
        except Exception as e:
            error_with_trace(f"Unexpected error: {e}", e)
            return False
        finally:
            elapsed = time.time() - start_time
            debug(f"Total elapsed: {elapsed:.2f}s")
            debug(f"Logs: {LOG_DIR}")
            # Keep browser open for 10 s so user can see the final state
            try:
                if self.engine:
                    self.engine.wait(10_000)
            except Exception:
                pass
            if self.engine:
                self.engine.close()


def main():
    """Entry point."""
    automation = IRCTCBookingAutomation()
    try:
        ok = automation.run()
        if ok:
            success("Automation completed successfully!")
        else:
            error("Automation finished with issues  check logs.")
    except KeyboardInterrupt:
        log("Exiting...")
    except Exception as e:
        error(f"Fatal error: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    main()



