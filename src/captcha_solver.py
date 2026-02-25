"""
IRCTC Train Ticket Booking - Captcha Solver
Multi-strategy captcha solving for direct API approach.
Receives base64 captcha image from IRCTC API and solves it.
Strategies: EasyOCR (local ML)  Remote API  Google Vision  Manual input.
"""

import base64
import io
import os
from pathlib import Path
from typing import Optional, List, Tuple

import httpx
from PIL import Image

from src.utils import log, warn, error, debug, error_with_trace

# Try importing EasyOCR (optional, falls back to API/manual)
try:
    import easyocr
    EASYOCR_AVAILABLE = True
except ImportError:
    EASYOCR_AVAILABLE = False
    warn("EasyOCR not installed. Will use API or manual captcha solving.")

_reader = None
_easyocr_failed = False  # Set True if init fails to avoid retrying

SCREENSHOTS_DIR = Path(__file__).parent.parent / "screenshots"
SCREENSHOTS_DIR.mkdir(exist_ok=True)


def _get_ocr_reader():
    """Lazy-initialize EasyOCR reader."""
    global _reader, _easyocr_failed
    if _easyocr_failed:
        debug("EasyOCR previously failed to initialize, skipping")
        return None
    if _reader is None and EASYOCR_AVAILABLE:
        try:
            log("Initializing EasyOCR reader (first time may download models)...")
            _reader = easyocr.Reader(['en'], gpu=False)
            log("EasyOCR reader initialized", "SUCCESS")
        except Exception as e:
            _easyocr_failed = True
            warn(f"EasyOCR initialization failed: {e}")
            debug(f"EasyOCR init error: {type(e).__name__}: {e}")
            debug("Will fall back to other captcha strategies")
            
            # Try to clean up corrupted model files
            try:
                import shutil
                easyocr_model_dir = Path.home() / ".EasyOCR" / "model"
                if easyocr_model_dir.exists():
                    for f in easyocr_model_dir.glob("temp*"):
                        f.unlink(missing_ok=True)
                        debug(f"Cleaned up: {f}")
            except Exception:
                pass
            
            return None
    return _reader


def solve_captcha(captcha_base64: str) -> Optional[str]:
    """
    Solve a captcha given its base64-encoded image data.
    Tries multiple strategies in order:
    1. Local EasyOCR
    2. Remote captcha API server
    3. Google Cloud Vision API (if gcloud credentials configured)
    4. Manual input from terminal

    Args:
        captcha_base64: Base64-encoded captcha image from IRCTC API

    Returns:
        The captcha solution text, or None if all strategies fail.
    """
    if not captcha_base64:
        error("Empty captcha data received")
        return None

    debug(f"Captcha base64 length: {len(captcha_base64)} chars")

    manual_mode = os.getenv("MANUAL_CAPTCHA", "false").strip().lower() in (
        "1", "true", "yes", "on"
    )

    # Save captcha image for reference
    try:
        image_bytes = base64.b64decode(captcha_base64)
        captcha_path = SCREENSHOTS_DIR / "current_captcha.png"
        with open(captcha_path, "wb") as f:
            f.write(image_bytes)
        debug(f"Captcha image saved: {captcha_path} ({len(image_bytes)} bytes)")
    except Exception as e:
        debug(f"Failed to save captcha image: {e}")

    # Optional manual mode toggle via .env
    if manual_mode:
        log("MANUAL_CAPTCHA is enabled  waiting for manual captcha input.")
        solution = _solve_manually(captcha_base64)
        if solution:
            log(f"Manual captcha entry: {solution}", "SUCCESS")
            return solution
        error("Manual captcha input failed")
        return None

    # Strategy 1: Local EasyOCR
    debug("Trying Strategy 1: EasyOCR...")
    solution = _solve_with_easyocr(captcha_base64)
    if solution:
        log(f"EasyOCR solved captcha: {solution}", "SUCCESS")
        return solution
    debug("EasyOCR failed or unavailable")

    # Strategy 2: Remote API (local Flask server)
    api_url = os.getenv("CAPTCHA_API_URL", "http://localhost:5001/extract-text")
    debug(f"Trying Strategy 2: Remote API at {api_url}...")
    solution = _solve_with_api(captcha_base64, api_url)
    if solution:
        log(f"API solved captcha: {solution}", "SUCCESS")
        return solution
    debug("Remote API failed or unreachable")

    # Strategy 3: Google Cloud Vision
    gcloud_path = os.getenv("GCLOUD_CREDENTIALS")
    if gcloud_path:
        debug(f"Trying Strategy 3: Google Cloud Vision (creds: {gcloud_path})...")
        solution = _solve_with_gcloud(captcha_base64, gcloud_path)
        if solution:
            log(f"Google Vision solved captcha: {solution}", "SUCCESS")
            return solution
        debug("Google Vision failed")
    else:
        debug("Strategy 3 skipped: GCLOUD_CREDENTIALS not set")

    # Strategy 4: Manual input (always available as last resort)
    debug("Trying Strategy 4: Manual input...")
    solution = _solve_manually(captcha_base64)
    if solution:
        log(f"Manual captcha entry: {solution}", "SUCCESS")
        return solution

    error("All captcha solving strategies failed!")
    return None


def _solve_with_easyocr(base64_data: str) -> Optional[str]:
    """Solve captcha using local EasyOCR."""
    try:
        reader = _get_ocr_reader()
    except Exception as e:
        warn(f"EasyOCR reader error: {e}")
        return None
    if not reader:
        debug("EasyOCR reader not available")
        return None

    try:
        image_bytes = base64.b64decode(base64_data)
        image = Image.open(io.BytesIO(image_bytes))
        debug(f"Original captcha image size: {image.size}, mode: {image.mode}")

        variants = _build_easyocr_variants(image)
        debug(f"EasyOCR variant count: {len(variants)}")

        candidates: List[Tuple[str, float]] = []
        import time as _time

        for name, variant in variants:
            buf = io.BytesIO()
            variant.save(buf, format="PNG")
            data = buf.getvalue()

            ocr_start = _time.time()
            results = reader.readtext(
                data,
                detail=1,  # includes confidence
                paragraph=False,
                allowlist='0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz'
            )
            ocr_elapsed = (_time.time() - ocr_start) * 1000
            debug(f"EasyOCR[{name}] raw results: {results} (took {ocr_elapsed:.0f}ms)")

            if not results:
                continue

            text_parts = []
            conf_total = 0.0
            conf_count = 0
            for row in results:
                # row format: [bbox, text, confidence]
                if isinstance(row, (list, tuple)) and len(row) >= 3:
                    txt = str(row[1])
                    conf = float(row[2])
                else:
                    txt = str(row)
                    conf = 0.3
                clean = "".join(c for c in txt if c.isalnum())
                if clean:
                    text_parts.append(clean)
                    conf_total += conf
                    conf_count += 1

            merged = "".join(text_parts)
            merged = "".join(c for c in merged if c.isalnum())
            if not merged:
                continue

            avg_conf = conf_total / conf_count if conf_count else 0.3
            # Favor typical captcha length (4-6 chars)
            len_bonus = 0.3 if 4 <= len(merged) <= 6 else 0.0
            score = avg_conf + len_bonus
            debug(f"EasyOCR[{name}] candidate='{merged}' score={score:.3f}")
            candidates.append((merged, score))

        if candidates:
            # Aggregate by exact text and prefer highest total score
            aggregate = {}
            for txt, score in candidates:
                aggregate[txt] = aggregate.get(txt, 0.0) + score

            best_text, best_score = sorted(
                aggregate.items(), key=lambda x: x[1], reverse=True
            )[0]
            debug(f"EasyOCR best candidate: '{best_text}' (agg_score={best_score:.3f})")

            if 3 <= len(best_text) <= 8:
                return best_text
            debug(f"EasyOCR best candidate '{best_text}' outside expected length 3-8")
        else:
            debug("EasyOCR produced no usable candidates")

    except Exception as e:
        warn(f"EasyOCR error: {e}")
        debug(f"EasyOCR error details: {type(e).__name__}: {e}")

    return None


def _preprocess_captcha_image(
    image: Image.Image,
    threshold: int = 140,
    invert: bool = False,
    contrast: float = 2.0,
    sharpness: float = 2.0,
) -> Image.Image:
    """Pre-process captcha image for better OCR accuracy."""
    from PIL import ImageEnhance, ImageFilter

    # Convert to grayscale
    image = image.convert("L")

    # Increase contrast
    enhancer = ImageEnhance.Contrast(image)
    image = enhancer.enhance(contrast)

    # Increase sharpness
    enhancer = ImageEnhance.Sharpness(image)
    image = enhancer.enhance(sharpness)

    # Apply threshold to make it binary
    if invert:
        image = image.point(lambda x: 255 - x)
    image = image.point(lambda x: 255 if x > threshold else 0, "1")

    # Scale up for better OCR
    width, height = image.size
    image = image.resize((width * 3, height * 3), Image.LANCZOS)

    # Remove noise with median filter
    image = image.convert("L")
    image = image.filter(ImageFilter.MedianFilter(size=3))

    return image


def _build_easyocr_variants(image: Image.Image) -> List[Tuple[str, Image.Image]]:
    """Generate multiple preprocessing variants and let OCR voting choose the winner."""
    variants: List[Tuple[str, Image.Image]] = []
    variants.append(("default", _preprocess_captcha_image(image, threshold=140, invert=False)))
    variants.append(("low_thr", _preprocess_captcha_image(image, threshold=120, invert=False)))
    variants.append(("high_thr", _preprocess_captcha_image(image, threshold=160, invert=False)))
    variants.append(("invert", _preprocess_captcha_image(image, threshold=140, invert=True)))
    variants.append(("invert_low", _preprocess_captcha_image(image, threshold=120, invert=True)))
    return variants


def _solve_with_api(base64_data: str, api_url: str) -> Optional[str]:
    """Solve captcha using remote API (e.g., local Flask OCR server)."""
    try:
        debug(f"Calling captcha API: POST {api_url}")
        with httpx.Client(timeout=10.0) as client:
            response = client.post(
                api_url,
                json={"image": base64_data},
                headers={"Content-Type": "application/json"}
            )
            debug(f"Captcha API response: status={response.status_code}")
            if response.status_code == 200:
                data = response.json()
                debug(f"Captcha API response data: {data}")
                text = data.get("text", data.get("result", "")).strip()
                text = "".join(c for c in text if c.isalnum())
                debug(f"Captcha API cleaned text: '{text}' (len={len(text)})")
                if 3 <= len(text) <= 8:
                    return text
            else:
                debug(f"Captcha API non-200: {response.text[:200]}")
    except (httpx.ConnectError, httpx.TimeoutException) as e:
        debug(f"Captcha API not reachable: {type(e).__name__}: {e}")
    except Exception as e:
        warn(f"Captcha API error: {e}")
        debug(f"Captcha API error details: {type(e).__name__}: {e}")

    return None


def _solve_with_gcloud(base64_data: str, credentials_path: str) -> Optional[str]:
    """
    Solve captcha using Google Cloud Vision API.
    Requires a service account JSON with Cloud Vision API enabled.
    """
    try:
        import json
        import time

        # Load credentials
        if os.path.isfile(credentials_path):
            with open(credentials_path) as f:
                creds = json.load(f)
        else:
            creds = json.loads(credentials_path)

        # Get access token using service account
        import jwt
        now = int(time.time())
        payload = {
            "iss": creds["client_email"],
            "scope": "https://www.googleapis.com/auth/cloud-vision",
            "aud": "https://oauth2.googleapis.com/token",
            "iat": now,
            "exp": now + 3600,
        }
        signed_jwt = jwt.encode(payload, creds["private_key"], algorithm="RS256")

        with httpx.Client(timeout=15.0) as client:
            # Exchange JWT for access token
            token_resp = client.post(
                "https://oauth2.googleapis.com/token",
                data={
                    "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
                    "assertion": signed_jwt,
                }
            )
            access_token = token_resp.json()["access_token"]

            # Call Vision API
            vision_resp = client.post(
                "https://vision.googleapis.com/v1/images:annotate",
                headers={"Authorization": f"Bearer {access_token}"},
                json={
                    "requests": [{
                        "image": {"content": base64_data},
                        "features": [{"type": "TEXT_DETECTION"}]
                    }]
                }
            )
            result = vision_resp.json()
            annotations = result.get("responses", [{}])[0].get("textAnnotations", [])
            if annotations:
                text = annotations[0].get("description", "").strip()
                text = "".join(c for c in text if c.isalnum())
                if 3 <= len(text) <= 8:
                    return text

    except ImportError:
        log("PyJWT not installed, skipping Google Cloud Vision", "DEBUG")
    except Exception as e:
        warn(f"Google Vision API error: {e}")

    return None


def _solve_manually(base64_data: str) -> Optional[str]:
    """Prompt user to solve captcha manually via terminal input."""
    try:
        image_bytes = base64.b64decode(base64_data)
        captcha_path = SCREENSHOTS_DIR / "current_captcha.png"
        with open(captcha_path, "wb") as f:
            f.write(image_bytes)

        log(f"Captcha image saved to: {captcha_path}")
        log("Please open the image and type the captcha text below.")
        debug(f"Waiting for manual captcha input (image: {len(image_bytes)} bytes at {captcha_path})")

        solution = input("\n>>> Enter captcha text: ").strip()
        debug(f"Manual captcha input received: '{solution}'")
        if solution:
            return solution

    except (EOFError, KeyboardInterrupt):
        warn("Manual captcha input cancelled")
        debug("Manual input: EOFError or KeyboardInterrupt")
    except Exception as e:
        warn(f"Manual captcha input error: {e}")
        debug(f"Manual input error: {type(e).__name__}: {e}")

    return None


