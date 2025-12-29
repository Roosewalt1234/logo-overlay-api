import base64
import binascii
import io
import os
import re
from urllib.parse import urlparse

import requests
from flask import Flask, jsonify, request
from PIL import Image, ImageOps

app = Flask(__name__)

ALLOWED_POSITIONS = {"top-right", "top-left", "bottom-right", "bottom-left"}

DEFAULT_LOGO_SCALE = 0.15
DEFAULT_POSITION = "top-right"
DEFAULT_PADDING = 20

LOGO_BG_PADDING = 10
LOGO_BG_COLOR = (255, 255, 255, 200)  # semi-transparent white

REQUEST_TIMEOUT = (5, 15)
MAX_LOGO_BYTES = 10 * 1024 * 1024  # 10MB

try:
    RESAMPLE = Image.Resampling.LANCZOS
except AttributeError:  # Pillow<9.1
    RESAMPLE = Image.LANCZOS

_DATA_URL_PREFIX_RE = re.compile(r"^data:.*?;base64,", re.IGNORECASE)
_WHITESPACE_RE = re.compile(r"\s+")


def _error(message: str, status_code: int = 400):
    return (
        jsonify({"status": "error", "message": message, "image": None, "format": "base64"}),
        status_code,
    )


@app.after_request
def _add_cors_headers(response):
    response.headers["Access-Control-Allow-Origin"] = "*"
    response.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
    response.headers["Access-Control-Allow-Headers"] = "Content-Type, Authorization"
    return response


@app.route("/", methods=["GET"], strict_slashes=False)
def index():
    return jsonify(
        {
            "status": "ok",
            "endpoints": {"health": "/health", "overlay_logo": "/overlay-logo"},
        }
    )


@app.route("/favicon.ico", methods=["GET"])
def favicon():
    return ("", 204)


@app.route("/health", methods=["GET"], strict_slashes=False)
def health():
    return jsonify({"status": "healthy"})


def _parse_logo_scale(value) -> float:
    if value is None:
        return DEFAULT_LOGO_SCALE
    try:
        scale = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("logo_scale must be a number") from exc

    if scale <= 0 or scale > 1:
        raise ValueError("logo_scale must be > 0 and <= 1")
    return scale


def _parse_padding(value) -> int:
    if value is None:
        return DEFAULT_PADDING
    try:
        padding = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("padding must be an integer") from exc

    if padding < 0:
        raise ValueError("padding must be >= 0")
    return padding


def _decode_base64_image(data: str) -> Image.Image:
    if not isinstance(data, str) or not data.strip():
        raise ValueError("base_image must be a non-empty base64 string")

    data = data.strip()
    data = _DATA_URL_PREFIX_RE.sub("", data)
    data = _WHITESPACE_RE.sub("", data)

    missing_padding = len(data) % 4
    if missing_padding:
        data += "=" * (4 - missing_padding)

    try:
        raw = base64.b64decode(data, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ValueError("Invalid base64 in base_image") from exc

    try:
        image = Image.open(io.BytesIO(raw))
        image.load()
    except Exception as exc:
        raise ValueError("base_image is not a valid image") from exc

    return ImageOps.exif_transpose(image)


def _download_logo(url: str) -> Image.Image:
    if not isinstance(url, str) or not url.strip():
        raise ValueError("logo_url must be a non-empty string")

    parsed = urlparse(url.strip())
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise ValueError("logo_url must be a valid http(s) URL")

    headers = {"User-Agent": "logo-overlay-api/1.0"}
    try:
        with requests.get(url, stream=True, timeout=REQUEST_TIMEOUT, headers=headers) as resp:
            resp.raise_for_status()

            content_length = resp.headers.get("Content-Length")
            if content_length is not None:
                try:
                    length = int(content_length)
                except ValueError:
                    length = None
                if length is not None and length > MAX_LOGO_BYTES:
                    raise ValueError("Logo download is too large")

            chunks = []
            total = 0
            for chunk in resp.iter_content(chunk_size=8192):
                if not chunk:
                    continue
                total += len(chunk)
                if total > MAX_LOGO_BYTES:
                    raise ValueError("Logo download is too large")
                chunks.append(chunk)

        raw = b"".join(chunks)
    except requests.RequestException as exc:
        raise ValueError("Failed to download logo from logo_url") from exc

    try:
        logo = Image.open(io.BytesIO(raw))
        logo.load()
    except Exception as exc:
        raise ValueError("Downloaded logo is not a valid image") from exc

    return ImageOps.exif_transpose(logo)


def _overlay_logo(
    base: Image.Image,
    logo: Image.Image,
    logo_scale: float,
    position: str,
    padding: int,
) -> Image.Image:
    base_rgba = base.convert("RGBA")
    logo_rgba = logo.convert("RGBA")

    base_w, base_h = base_rgba.size
    if base_w <= 0 or base_h <= 0:
        raise ValueError("Base image has invalid dimensions")

    target_logo_w = max(1, min(base_w, int(base_w * logo_scale)))
    scale_factor = target_logo_w / max(1, logo_rgba.width)
    target_logo_h = max(1, int(logo_rgba.height * scale_factor))

    logo_rgba = logo_rgba.resize((target_logo_w, target_logo_h), RESAMPLE)

    max_allowed_w = base_w - (2 * padding)
    max_allowed_h = base_h - (2 * padding)
    if max_allowed_w <= 0:
        max_allowed_w = base_w
    if max_allowed_h <= 0:
        max_allowed_h = base_h

    max_logo_w = max(1, max_allowed_w - (2 * LOGO_BG_PADDING))
    max_logo_h = max(1, max_allowed_h - (2 * LOGO_BG_PADDING))

    if logo_rgba.width > max_logo_w or logo_rgba.height > max_logo_h:
        shrink = min(max_logo_w / logo_rgba.width, max_logo_h / logo_rgba.height)
        new_w = max(1, int(logo_rgba.width * shrink))
        new_h = max(1, int(logo_rgba.height * shrink))
        logo_rgba = logo_rgba.resize((new_w, new_h), RESAMPLE)

    bg_w = logo_rgba.width + (2 * LOGO_BG_PADDING)
    bg_h = logo_rgba.height + (2 * LOGO_BG_PADDING)
    background = Image.new("RGBA", (bg_w, bg_h), LOGO_BG_COLOR)
    background.paste(logo_rgba, (LOGO_BG_PADDING, LOGO_BG_PADDING), mask=logo_rgba)

    if position == "top-left":
        x, y = padding, padding
    elif position == "top-right":
        x, y = base_w - bg_w - padding, padding
    elif position == "bottom-left":
        x, y = padding, base_h - bg_h - padding
    elif position == "bottom-right":
        x, y = base_w - bg_w - padding, base_h - bg_h - padding
    else:
        raise ValueError(f"Invalid position: {position}")

    x = max(0, x)
    y = max(0, y)

    base_rgba.paste(background, (x, y), mask=background)
    return base_rgba


@app.route("/overlay-logo", methods=["POST", "OPTIONS"], strict_slashes=False)
def overlay_logo():
    if request.method == "OPTIONS":
        return ("", 204)

    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return _error("Request body must be a JSON object", 400)

    base_image_b64 = payload.get("base_image")
    logo_url = payload.get("logo_url")
    if not base_image_b64:
        return _error("Missing required field: base_image", 400)
    if not logo_url:
        return _error("Missing required field: logo_url", 400)

    try:
        logo_scale = _parse_logo_scale(payload.get("logo_scale"))
        position = payload.get("position", DEFAULT_POSITION) or DEFAULT_POSITION
        padding = _parse_padding(payload.get("padding"))
    except ValueError as exc:
        return _error(str(exc), 400)

    if position not in ALLOWED_POSITIONS:
        allowed = ", ".join(sorted(ALLOWED_POSITIONS))
        return _error(f"position must be one of: {allowed}", 400)

    try:
        base_img = _decode_base64_image(base_image_b64)
        logo_img = _download_logo(logo_url)
        out_img = _overlay_logo(base_img, logo_img, logo_scale, position, padding)

        buf = io.BytesIO()
        out_img.save(buf, format="PNG")
        out_b64 = base64.b64encode(buf.getvalue()).decode("utf-8")
    except ValueError as exc:
        return _error(str(exc), 400)
    except Exception:
        app.logger.exception("Unhandled error during image processing")
        return _error("Image processing failed", 500)

    return jsonify({"status": "success", "image": out_b64, "format": "base64"})


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "5000"))
    app.run(host="0.0.0.0", port=port)
