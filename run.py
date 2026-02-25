"""
IRCTC Train Ticket Booking Automation
Entry point - run this file to start the booking process.

Usage:
    python run.py
"""

import sys
import os

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from src.main import main


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nExiting...")
        sys.exit(0)


