"""cortex screen context — understand the active window with a vision model.

Dictation, notes, and meetings only capture what you *say*. This module adds the
visual context around those moments: it grabs the focused window (grim), sends it
to a vision model (Gemini 2.5 Flash) which distills it to a single concrete
sentence — "editing the retry logic in brain_search.py", "reading Groq's rate-limit
docs" — and that one line is attached to your journal stream. So months later a
glance at the log tells you what you were actually building, not just what you
narrated.

Design choices:
  * Active window only (grim -g), never the whole screen — smaller privacy surface.
  * The screenshot is held in memory only for the one API call, then discarded. No
    image is written to disk or the vault; only the model's one-line description
    persists.
  * App/title denylist skips sensitive windows (password managers, banking).
  * Best-effort: if grim/the vision call fails, it falls back to local OCR
    (tesseract); if that's unavailable too, it yields nothing rather than erroring.
  * Opt-in via the ``screenContext`` setting.
"""

from __future__ import annotations

import base64
import json
import logging
import subprocess

logger = logging.getLogger(__name__)

VISION_MODEL = "gemini-2.5-flash"
GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
OCR_MAX_CHARS = 400  # OCR fallback keeps only a gist
# Flash reads low-res fine, so cap the long edge — fewer image tiles = faster and
# cheaper per call, with no real loss for "what am I looking at" understanding.
TARGET_MAX_PX = 1024

CAPTION_PROMPT = (
    "This is a screenshot of the active window on someone's screen while they were "
    "working. In ONE concise, specific sentence, describe what they were doing — the "
    "task plus the concrete subject (the file, page, thread, tool, or topic). Be "
    "specific and factual; no preamble, no markdown, no quotes. If the window is "
    "empty or genuinely unclear, reply with exactly: unclear"
)


def _active_window() -> dict:
    try:
        out = subprocess.run(
            ["hyprctl", "activewindow", "-j"], capture_output=True, text=True, timeout=2
        ).stdout
        return json.loads(out) or {}
    except (subprocess.SubprocessError, json.JSONDecodeError, OSError):
        return {}


def _grab_png(w: dict) -> bytes | None:
    """PNG of just the active window (its geometry) — never full-screen."""
    at, size = w.get("at"), w.get("size")
    if not (isinstance(at, list) and isinstance(size, list) and len(at) == 2 and len(size) == 2):
        return None
    if size[0] <= 0 or size[1] <= 0:
        return None
    geom = f"{at[0]},{at[1]} {size[0]}x{size[1]}"
    scale = min(1.0, TARGET_MAX_PX / (max(size[0], size[1]) or 1))  # downscale to low-res
    try:
        r = subprocess.run(
            ["grim", "-t", "png", "-s", f"{scale:.4f}", "-g", geom, "-"],
            capture_output=True, timeout=5,
        )
        return r.stdout or None
    except (subprocess.SubprocessError, OSError):
        return None


def _gemini_caption(png: bytes, api_key: str, hint: str = "") -> str | None:
    """Ask Gemini 2.5 Flash for a one-line description of the window. Returns None
    on any failure (caller falls back to OCR). Thinking is disabled for speed/cost."""
    prompt = CAPTION_PROMPT
    if hint:
        prompt += f'\n\nThey were dictating this at the same moment (a clue, not the answer): "{hint[:300]}"'
    try:
        import httpx

        resp = httpx.post(
            GEMINI_URL.format(model=VISION_MODEL),
            params={"key": api_key},
            json={
                "contents": [{"parts": [
                    {"text": prompt},
                    {"inline_data": {"mime_type": "image/png",
                                     "data": base64.b64encode(png).decode()}},
                ]}],
                "generationConfig": {
                    "maxOutputTokens": 150,
                    "temperature": 0.2,
                    "thinkingConfig": {"thinkingBudget": 0},
                },
            },
            timeout=20,
        )
        if resp.status_code != 200:
            logger.debug("gemini vision %s: %s", resp.status_code, resp.text[:200])
            return None
        cand = (resp.json().get("candidates") or [{}])[0]
        parts = cand.get("content", {}).get("parts", [])
        text = "".join(p.get("text", "") for p in parts).strip()
        return text or None
    except Exception:
        logger.debug("gemini vision call failed", exc_info=True)
        return None


def _ocr(png: bytes) -> str:
    """Local OCR fallback (tesseract). Cheap but noisy — only used when vision is
    unavailable (no key / call failed / model said 'unclear')."""
    try:
        r = subprocess.run(
            ["tesseract", "stdin", "stdout", "--psm", "6"],
            input=png, capture_output=True, timeout=15,
        )
        text = r.stdout.decode("utf-8", "ignore")
    except (subprocess.SubprocessError, OSError):
        return ""
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    return "\n".join(lines)[:OCR_MAX_CHARS]


def describe_active_window(
    denylist: list[str] | None = None,
    gemini_key: str | None = None,
    hint: str = "",
) -> dict | None:
    """Return ``{'app', 'title', 'text', 'mode'}`` describing the focused window, or
    ``None`` if it should be skipped (denylisted / nothing captured). ``mode`` is
    'vision' (Gemini description), 'ocr' (local fallback), or 'title' (nothing read
    but the window title is still worth logging). The screenshot is discarded after
    the call — only ``text`` persists."""
    w = _active_window()
    cls = (w.get("class") or "").strip()
    title = (w.get("title") or "").strip()
    haystack = f"{cls} {title}".lower()
    for bad in denylist or []:
        if bad and bad.lower() in haystack:
            logger.debug("screen: skipping denylisted window (%s)", cls)
            return None
    png = _grab_png(w)
    if not png:
        return None

    text, mode = "", "title"
    if gemini_key:
        cap = _gemini_caption(png, gemini_key, hint)
        if cap and cap.lower() != "unclear":
            text, mode = cap, "vision"
    if not text:  # no key, vision failed, or 'unclear' → cheap local OCR gist
        ocr = _ocr(png)
        if ocr:
            text, mode = ocr, "ocr"
    if not text and not title:
        return None
    return {"app": cls or "?", "title": title, "text": text, "mode": mode}


if __name__ == "__main__":  # manual check: python screen.py [gemini_key]
    import sys

    logging.basicConfig(level=logging.DEBUG)
    key = sys.argv[1] if len(sys.argv) > 1 else None
    rec = describe_active_window(gemini_key=key)
    print(json.dumps(rec, indent=2) if rec else "no capture (skipped/unavailable)")
