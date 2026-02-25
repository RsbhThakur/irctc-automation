"""
IRCTC Train Ticket Booking Automation - Utility Module
Logging, configuration loading, helper functions, and debugging utilities.
"""

import json
import os
import sys
import re
import traceback
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Optional

from dotenv import load_dotenv
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich import box

console = Console()

#  Logging 

LOG_DIR = Path(__file__).parent.parent / "logs"
LOG_DIR.mkdir(exist_ok=True)

DUMP_DIR = LOG_DIR / "dumps"
DUMP_DIR.mkdir(exist_ok=True)

_session_id = datetime.now().strftime('%Y%m%d_%H%M%S')
_log_file = LOG_DIR / f"booking_{_session_id}.log"
_debug_file = LOG_DIR / f"debug_{_session_id}.log"

# Global debug flag  set via environment variable DEBUG=1
DEBUG_ENABLED = os.environ.get("DEBUG", "1") == "1"
SAVE_LOG_FILES = os.environ.get("SAVE_LOG_FILES", "0").strip().lower() in ("1", "true", "yes", "on")


def _refresh_runtime_toggles():
    """Refresh env-driven runtime flags after .env has been loaded."""
    global DEBUG_ENABLED, SAVE_LOG_FILES
    DEBUG_ENABLED = os.environ.get("DEBUG", "1") == "1"
    SAVE_LOG_FILES = os.environ.get("SAVE_LOG_FILES", "0").strip().lower() in (
        "1", "true", "yes", "on"
    )


def _safe_console_text(text: str) -> str:
    """Prevent Windows cp1252 console crashes on unicode-only glyphs."""
    enc = getattr(sys.stdout, "encoding", None) or "utf-8"
    try:
        return text.encode(enc, errors="replace").decode(enc, errors="replace")
    except Exception:
        return text


def log(message: str, level: str = "INFO"):
    """Log message to console and file."""
    timestamp = datetime.now().strftime("%H:%M:%S.%f")[:-3]
    color_map = {
        "INFO": "cyan",
        "SUCCESS": "green",
        "WARNING": "yellow",
        "ERROR": "red",
        "STEP": "magenta",
        "DEBUG": "dim",
    }
    color = color_map.get(level, "white")

    # Always show DEBUG if DEBUG_ENABLED, otherwise skip console for DEBUG
    if level == "DEBUG" and not DEBUG_ENABLED:
        if SAVE_LOG_FILES:
            with open(_log_file, "a", encoding="utf-8") as f:
                f.write(f"{timestamp} [{level}] {message}\n")
        return

    safe_message = _safe_console_text(message)
    console.print(f"[dim]{timestamp}[/dim] [{color}][{level:^7}][/{color}] {safe_message}")

    if SAVE_LOG_FILES:
        with open(_log_file, "a", encoding="utf-8") as f:
            f.write(f"{timestamp} [{level}] {message}\n")


def debug(message: str):
    """Log a debug-level message."""
    timestamp = datetime.now().strftime("%H:%M:%S.%f")[:-3]
    if SAVE_LOG_FILES:
        with open(_debug_file, "a", encoding="utf-8") as f:
            f.write(f"{timestamp} [DEBUG] {message}\n")
    # Also show in console & main log if DEBUG_ENABLED
    log(message, "DEBUG")


def step(message: str):
    """Log a major step."""
    console.print()
    console.print(Panel(f"[bold magenta]{message}[/bold magenta]", box=box.DOUBLE))
    log(message, "STEP")


def success(message: str):
    log(message, "SUCCESS")


def warn(message: str):
    log(message, "WARNING")


def error(message: str):
    log(message, "ERROR")


def error_with_trace(message: str, exc: Optional[Exception] = None):
    """Log an error with full stack trace."""
    error(message)
    if exc:
        tb = traceback.format_exception(type(exc), exc, exc.__traceback__)
        trace_str = "".join(tb)
        debug(f"TRACEBACK:\n{trace_str}")
    else:
        trace_str = traceback.format_exc()
        if trace_str and trace_str.strip() != "NoneType: None":
            debug(f"TRACEBACK:\n{trace_str}")


def dump_response(label: str, status_code: int, headers: dict, body: str,
                  url: str = "", method: str = ""):
    """Dump a full HTTP response to a file in logs/dumps/ for debugging."""
    if not SAVE_LOG_FILES:
        return
    timestamp = datetime.now().strftime("%H%M%S_%f")
    safe_label = re.sub(r'[^\w\-]', '_', label)[:50]
    dump_path = DUMP_DIR / f"{timestamp}_{safe_label}.txt"
    try:
        with open(dump_path, "w", encoding="utf-8") as f:
            f.write(f"=== {label} ===\n")
            f.write(f"Timestamp: {datetime.now().isoformat()}\n")
            if method and url:
                f.write(f"Request: {method} {url}\n")
            f.write(f"Status: {status_code}\n")
            f.write(f"\n--- Response Headers ---\n")
            for k, v in headers.items():
                f.write(f"  {k}: {v}\n")
            f.write(f"\n--- Response Body ({len(body)} chars) ---\n")
            f.write(body[:50000])  # Cap at 50KB
            if len(body) > 50000:
                f.write(f"\n... (truncated, total {len(body)} chars)")
            f.write("\n")
        debug(f"Response dumped to: {dump_path}")
    except Exception as e:
        debug(f"Failed to dump response: {e}")


#  Configuration 

def load_config() -> dict:
    """Load and validate booking configuration."""
    # Load .env if present
    env_path = Path(__file__).parent.parent / ".env"
    if env_path.exists():
        load_dotenv(env_path)
        _refresh_runtime_toggles()
        log("Loaded .env file")

    config_path = Path(__file__).parent.parent / "config" / "booking_config.json"
    if not config_path.exists():
        error(f"Config file not found: {config_path}")
        sys.exit(1)

    with open(config_path, "r", encoding="utf-8") as f:
        config = json.load(f)

    # Override with environment variables if set
    env_username = os.getenv("IRCTC_USERNAME")
    env_password = os.getenv("IRCTC_PASSWORD")
    env_upi = os.getenv("UPI_ID")
    env_login_time = os.getenv("LOGIN_TIME", "").strip()
    env_login_refresh_secs = os.getenv("LOGIN_REFRESH_SECONDS", "").strip()
    env_book_retry_secs = os.getenv("BOOK_NOW_RETRY_SECONDS", "").strip()
    env_book_start_time = os.getenv("BOOK_NOW_START_TIME", "").strip()
    env_use_master_pax = os.getenv("USE_MASTER_PASSENGER_LIST", "").strip()
    env_headless = os.getenv("HEADLESS", "").strip()
    env_slow_mo = os.getenv("SLOW_MO", "").strip()

    if env_username and env_username != "your_username":
        config["IRCTC_USERNAME"] = env_username
    if env_password and env_password != "your_password":
        config["IRCTC_PASSWORD"] = env_password
    if env_upi:
        config["UPI_ID"] = env_upi
    if env_login_time:
        config["LOGIN_TIME"] = env_login_time
    if env_login_refresh_secs:
        config["LOGIN_REFRESH_SECONDS"] = env_login_refresh_secs
    if env_book_retry_secs:
        config["BOOK_NOW_RETRY_SECONDS"] = env_book_retry_secs
    if env_book_start_time:
        config["BOOK_NOW_START_TIME"] = env_book_start_time
    if env_use_master_pax:
        config["USE_MASTER_PASSENGER_LIST"] = env_use_master_pax
    if env_headless:
        config["HEADLESS"] = env_headless
    if env_slow_mo:
        config["SLOW_MO"] = env_slow_mo

    # Validate required fields
    _validate_config(config)

    return config


def _validate_config(config: dict):
    """Validate configuration values."""
    required = ["IRCTC_USERNAME", "IRCTC_PASSWORD", "TRAIN_NO", "TRAIN_COACH",
                 "TRAVEL_DATE", "SOURCE_STATION", "DESTINATION_STATION"]

    for field in required:
        if not config.get(field) or config[field] in ("your_username", "your_password"):
            error(f"Missing or default value for required field: {field}")
            error("Please update config/booking_config.json or .env file")
            sys.exit(1)

    if config.get("TATKAL") and config.get("PREMIUM_TATKAL"):
        error("Both TATKAL and PREMIUM_TATKAL cannot be true at the same time!")
        sys.exit(1)

    if not config.get("PASSENGER_DETAILS") or len(config["PASSENGER_DETAILS"]) == 0:
        error("At least one passenger is required in PASSENGER_DETAILS!")
        sys.exit(1)

    valid_coaches = {"SL", "2A", "3A", "3E", "1A", "CC", "EC", "2S"}
    if config["TRAIN_COACH"] not in valid_coaches:
        error(f"Invalid TRAIN_COACH: {config['TRAIN_COACH']}. Must be one of: {valid_coaches}")
        sys.exit(1)

    # Validate travel date format and value
    try:
        travel_date = datetime.strptime(config["TRAVEL_DATE"], "%d/%m/%Y")
        today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
        if travel_date < today:
            error(f"Travel date {config['TRAVEL_DATE']} is in the past!")
            sys.exit(1)
    except ValueError:
        error(f"Invalid TRAVEL_DATE format: {config['TRAVEL_DATE']}. Use DD/MM/YYYY")
        sys.exit(1)

    for i, p in enumerate(config["PASSENGER_DETAILS"]):
        for field in ["NAME", "AGE", "GENDER"]:
            if field not in p:
                error(f"Passenger {i+1} missing required field: {field}")
                sys.exit(1)

    # Optional login-time gate (HH:MM or HH:MM:SS)
    login_time = str(config.get("LOGIN_TIME", "")).strip()
    if login_time:
        if not re.match(r"^\d{2}:\d{2}(:\d{2})?$", login_time):
            error(f"Invalid LOGIN_TIME: {login_time}. Use HH:MM or HH:MM:SS")
            sys.exit(1)

    # Optional refresh interval before login
    refresh_secs = config.get("LOGIN_REFRESH_SECONDS", 2)
    try:
        refresh_secs = float(refresh_secs)
        if refresh_secs <= 0:
            raise ValueError("must be > 0")
        config["LOGIN_REFRESH_SECONDS"] = refresh_secs
    except Exception:
        error(f"Invalid LOGIN_REFRESH_SECONDS: {refresh_secs}. Use a positive number.")
        sys.exit(1)

    # Optional retry gap for Book Now re-click loop
    retry_secs = config.get("BOOK_NOW_RETRY_SECONDS", 2)
    try:
        retry_secs = float(retry_secs)
        if retry_secs <= 0:
            raise ValueError("must be > 0")
        config["BOOK_NOW_RETRY_SECONDS"] = retry_secs
    except Exception:
        error(f"Invalid BOOK_NOW_RETRY_SECONDS: {retry_secs}. Use a positive number.")
        sys.exit(1)

    # Optional time gate before first/any Book Now clicks (HH:MM or HH:MM:SS)
    book_start_time = str(config.get("BOOK_NOW_START_TIME", "")).strip()
    if book_start_time:
        if not re.match(r"^\d{2}:\d{2}(:\d{2})?$", book_start_time):
            error(f"Invalid BOOK_NOW_START_TIME: {book_start_time}. Use HH:MM or HH:MM:SS")
            sys.exit(1)

    # Optional toggle to use IRCTC saved-passenger master list autocomplete
    use_master_pax = str(config.get("USE_MASTER_PASSENGER_LIST", "false")).strip().lower()
    if use_master_pax in ("1", "true", "yes", "y", "on"):
        config["USE_MASTER_PASSENGER_LIST"] = True
    elif use_master_pax in ("0", "false", "no", "n", "off"):
        config["USE_MASTER_PASSENGER_LIST"] = False
    else:
        error(
            f"Invalid USE_MASTER_PASSENGER_LIST: {config.get('USE_MASTER_PASSENGER_LIST')}. "
            "Use true/false."
        )
        sys.exit(1)

    # Optional browser mode
    headless_val = str(config.get("HEADLESS", "false")).strip().lower()
    if headless_val in ("1", "true", "yes", "y", "on"):
        config["HEADLESS"] = True
    elif headless_val in ("0", "false", "no", "n", "off"):
        config["HEADLESS"] = False
    else:
        error(f"Invalid HEADLESS: {config.get('HEADLESS')}. Use true/false.")
        sys.exit(1)

    slow_mo_val = config.get("SLOW_MO", 15)
    try:
        slow_mo_val = int(float(slow_mo_val))
        if slow_mo_val < 0:
            raise ValueError("must be >= 0")
        config["SLOW_MO"] = slow_mo_val
    except Exception:
        error(f"Invalid SLOW_MO: {slow_mo_val}. Use a non-negative number.")
        sys.exit(1)

    log("Configuration validated successfully", "SUCCESS")


def print_booking_summary(config: dict):
    """Print a nice summary of the booking configuration."""
    table = Table(title="Booking Configuration", box=box.ROUNDED)
    table.add_column("Field", style="cyan", no_wrap=True)
    table.add_column("Value", style="green")

    table.add_row("Train No", config["TRAIN_NO"])
    table.add_row("Coach", config["TRAIN_COACH"])
    table.add_row("Date", config["TRAVEL_DATE"])
    table.add_row("From", config["SOURCE_STATION"])
    table.add_row("To", config["DESTINATION_STATION"])
    if config.get("BOARDING_STATION"):
        table.add_row("Boarding", config["BOARDING_STATION"])

    quota = "TATKAL" if config.get("TATKAL") else "PREMIUM TATKAL" if config.get("PREMIUM_TATKAL") else "GENERAL"
    table.add_row("Quota", quota)
    table.add_row("Payment", config.get("PAYMENT_METHOD", "UPI"))
    if config.get("UPI_ID"):
        table.add_row("UPI ID", config["UPI_ID"])
    table.add_row("Passengers", str(len(config["PASSENGER_DETAILS"])))

    console.print(table)
    console.print()

    # Passenger details table
    p_table = Table(title="Passenger Details", box=box.ROUNDED)
    p_table.add_column("#", style="dim")
    p_table.add_column("Name", style="cyan")
    p_table.add_column("Age", style="green")
    p_table.add_column("Gender", style="yellow")
    p_table.add_column("Berth", style="magenta")
    p_table.add_column("Food", style="blue")

    for i, p in enumerate(config["PASSENGER_DETAILS"], 1):
        p_table.add_row(
            str(i),
            p["NAME"],
            str(p["AGE"]),
            p["GENDER"],
            p.get("BERTH", "No Preference"),
            p.get("FOOD", "No Food")
        )

    console.print(p_table)
    console.print()


#  Time Helpers 

def get_tatkal_start_time(coach: str) -> datetime:
    """Get Tatkal booking start time based on coach class."""
    today = datetime.now().replace(second=0, microsecond=0)
    if coach in ("2A", "1A", "EC", "CC", "3E"):
        return today.replace(hour=10, minute=0)
    else:  # SL, 2S
        return today.replace(hour=11, minute=0)


def wait_for_tatkal_time(coach: str) -> bool:
    """Check if we need to wait for Tatkal window to open."""
    tatkal_time = get_tatkal_start_time(coach)
    now = datetime.now()
    if now < tatkal_time:
        diff = (tatkal_time - now).total_seconds()
        return diff > 0
    return False


def get_seconds_until_tatkal(coach: str) -> float:
    """Get seconds until Tatkal window opens."""
    tatkal_time = get_tatkal_start_time(coach)
    now = datetime.now()
    return max(0, (tatkal_time - now).total_seconds())


#  Screenshot Helper 

SCREENSHOT_DIR = Path(__file__).parent.parent / "screenshots"
SCREENSHOT_DIR.mkdir(exist_ok=True)


def is_valid_upi(upi_id: str) -> bool:
    """Validate UPI ID format."""
    pattern = r'^[a-zA-Z0-9._-]+@[a-zA-Z0-9]+$'
    return bool(re.match(pattern, upi_id))


