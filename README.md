# IRCTC Browser Booking Automation

Automates IRCTC ticket booking through a real browser using Playwright (Edge/Chrome).  
The flow includes login, train search, Book Now retry logic, passenger form fill, captcha solve, and payment handoff.

## Important
- Use responsibly and only in ways allowed by IRCTC.
- Keep credentials private.
- This project interacts with live booking/payment pages, so always verify your configuration before running.

## What It Automates
- Open IRCTC train search page.
- Login with configured credentials.
- Wait for configured login/book-now times (optional).
- Search train and class.
- Click Book Now with retry strategy.
- Fill passenger form.
- Handle review page and booking captcha.
- Proceed to payment flow (UPI/other configured mode).

## Project Layout
```text
irctc/
  run.py
  .env                    # local, private (ignored by git)
  .env.example            # template
  .gitignore
  README.md
  requirements.txt
  config/
    booking_config.json           # local, private (ignored by git)
    booking_config_example.json   # template
  src/
    main.py
    utils.py
    browser_engine.py
    login_handler.py
    train_search.py
    booking_form.py
    captcha_solver.py
    payment_handler.py
```

## Setup (Beginner Friendly)

### 1. Install Python
1. Download Python 3.10+ from: https://www.python.org/downloads/
2. During install, enable `Add Python to PATH`.

### 2. Get the project code
If you have Git:
```powershell
git clone https://github.com/RsbhThakur/irctc-automation.git
cd irctc-automation
```

If you do not have Git:
1. Scroll to the top of the page
2. Click `Code` -> `Download ZIP`
3. Extract the ZIP
4. Open PowerShell inside the extracted `irctc-automation` folder

### 3. Open PowerShell (if not already open in project folder)
1. Press `Win + X` and open `Terminal`.
2. `cd` into the `irctc-automation` project folder.

### 4. Create virtual environment
```powershell
python -m venv .venv
```

### 5. Activate virtual environment
```powershell
.\.venv\Scripts\Activate.ps1
```

If activation is blocked once, run:
```powershell
Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
```
Then activate again.

### 6. Install dependencies
```powershell
pip install -r requirements.txt
python -m playwright install chromium
```

### 7. Create your real config files from examples
Run these commands once:
```powershell
Copy-Item .env.example .env
Copy-Item config\booking_config_example.json config\booking_config.json
```

Then edit:
- `.env`
- `config/booking_config.json`

### 8. Run
```powershell
python run.py
```

### 9. First-run requirement (important)
- Run the script at least once before actual booking day.
- This creates the local browser profile used for session stability and faster page behavior.
- The profile folder is local-only and should not be committed to Git.
- It is already ignored in `.gitignore` (`browser_profile/`, `irctc_browser_profile/`).

### 10. Always activate `.venv` before every run
Each time you open a new PowerShell window, run:
```powershell
.\.venv\Scripts\Activate.ps1
```
Then run:
```powershell
python run.py
```

---

## `booking_config.json` Setup

This file defines the travel and passenger data.

### Required fields
- `TRAIN_NO` (string): 5-digit train number.
- `TRAIN_COACH` (string): one of `SL`, `2A`, `3A`, `3E`, `1A`, `CC`, `EC`, `2S`.
- `TRAVEL_DATE` (string): format `DD/MM/YYYY`.
- `SOURCE_STATION` (string): station code (example `NDLS`).
- `DESTINATION_STATION` (string): station code (example `GHY`).
- `PASSENGER_DETAILS` (array): at least one passenger.

### Common optional fields
- `QUOTA`: usually `GENERAL`, can be Tatkal/Premium Tatkal per your setup.
- `PAYMENT_METHOD`: `UPI`.
- `AUTO_UPGRADE`: `true`/`false`.
- `BOOK_ONLY_IF_CNF`: `true`/`false`.

### Passenger object fields
- `NAME`: full name.
- `AGE`: integer.
- `GENDER`: `Male`, `Female`, or `Transgender`.
- `BERTH`: e.g. `Lower`, `Upper`, `No Preference`.
- `FOOD`: e.g. `No Food`, `Veg`, `Non Veg`.

### Recommended starting template
Use `config/booking_config_example.json` as base.

### Example
```json
{
  "TRAIN_NO": "12506",
  "TRAIN_COACH": "1A",
  "TRAVEL_DATE": "23/04/2026",
  "SOURCE_STATION": "NDLS",
  "DESTINATION_STATION": "GHY",
  "QUOTA": "GENERAL",
  "PAYMENT_METHOD": "UPI",
  "AUTO_UPGRADE": true,
  "BOOK_ONLY_IF_CNF": true,
  "PASSENGER_DETAILS": [
    {
      "NAME": "Passenger One",
      "AGE": 30,
      "GENDER": "Male",
      "BERTH": "Lower",
      "FOOD": "No Food"
    }
  ]
}
```

---

## `.env` Setup and Recommended Defaults

Use `.env.example` as base.

### Required
- `IRCTC_USERNAME`
- `IRCTC_PASSWORD`

### Recommended defaults (safe starting point)
```env
CAPTCHA_API_URL=http://localhost:5001/extract-text
MANUAL_CAPTCHA=false

LOGIN_REFRESH_SECONDS=2
BOOK_NOW_RETRY_SECONDS=5

USE_MASTER_PASSENGER_LIST=false

HEADLESS=false
SLOW_MO=2

SAVE_SCREENSHOTS=false
SAVE_LOG_FILES=false
```

### Recommended default timings
These depend on your booking window.

For a **10:00:00** booking window:
- `LOGIN_TIME=09:59:20`
- `BOOK_NOW_START_TIME=09:59:47`

For an **11:00:00** booking window:
- `LOGIN_TIME=10:59:20`
- `BOOK_NOW_START_TIME=10:59:47`

Why these are recommended:
- Login starts early enough to clear captcha/session delays.
- Book Now starts a few seconds before opening, then retries (`BOOK_NOW_RETRY_SECONDS`).

---

## Timing Controls Explained

### `LOGIN_TIME`
- Format: `HH:MM` or `HH:MM:SS`.
- If set, script waits and refreshes until IRCTC time reaches this value, then starts login.

### `LOGIN_REFRESH_SECONDS`
- Refresh interval while waiting for `LOGIN_TIME`.
- Recommended: `2`.

### `BOOK_NOW_START_TIME`
- No Book Now clicks are attempted before this time.
- Use this to synchronize with booking opening time.

### `BOOK_NOW_RETRY_SECONDS`
- Retry gap for Book Now click loop when booking is not open yet.
- Recommended: `5`.

---

## Captcha Modes

### Auto mode (`MANUAL_CAPTCHA=false`)
Order:
1. EasyOCR
2. Remote API (`CAPTCHA_API_URL`)
3. Google Vision (if `GCLOUD_CREDENTIALS` set)
4. Manual fallback

### Manual mode (`MANUAL_CAPTCHA=true`)
- Always prompts you in terminal to type captcha text.

---

## Troubleshooting

### Browser blocked in headless mode
- Symptom: early navigation failures.
- Fix: `HEADLESS=false`.

### Book Now clicks too early/late
- Verify:
  - `BOOK_NOW_START_TIME`
  - `BOOK_NOW_RETRY_SECONDS`
  - train/date/class/quota correctness

### Captcha not solving
- Set `MANUAL_CAPTCHA=true` and retry.
- Keep browser visible and stable.

### Payment gateway tab closes
- This can happen due to external gateway redirects.
- Verify status in IRCTC transaction history if run exits after payment handoff.

---

## Privacy and Git Behavior

Tracked templates:
- `.env.example`
- `config/booking_config_example.json`

Ignored local files:
- `.env`
- `config/booking_config.json`

So your real credentials and booking data stay local by default.
