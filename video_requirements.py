"""Per-job video requirements: what a campaign brief demands of every clip.

Two kinds of rule, kept apart on purpose:

* Enforced by code, so they always hold: the logo, the link, the required text and
  the closing call to action are burned on (``brand_delivery``), and the link and
  required description text are appended to the descriptions (``apply_to_descriptions``).
* Judged by the model, so they are guidance: must-include and must-avoid material
  goes into the selection prompts (``prompt_block``). The picks read the
  transcript, so visual rules hold far less reliably than spoken ones.

The overlay is a served copy, ``brand_<final>``, one layer under the free plan's
``wm_``: canonical files stay clean, every editor endpoint re-applies it through
``app._deliver``, and the walk-backs strip it first. The whole overlay is drawn
with Pillow and laid over as one PNG, so user text never reaches an ffmpeg filter
string.

The spec lives in the job directory as dotfiles (never servable, kept by the
auto-retry, and read by the job process and the API alike).
"""
import base64
import io
import json
import os
import re
import subprocess
import uuid

PREFIX = "brand_"
SPEC_FILE = ".video_requirements.json"
LOGO_FILE = ".brand_logo.png"

LOGO_POSITIONS = ("top-left", "top-center", "top-right")
DEFAULT_LOGO_POSITION = "top-left"
DEFAULT_LOGO_WIDTH = 0.18
MIN_LOGO_WIDTH, MAX_LOGO_WIDTH = 0.08, 0.40

# Starlette caps a non-file multipart field at 1 MB and base64 adds a third, so
# anything above this could pass on a URL job and fail on an upload job.
MAX_LOGO_BYTES = 700 * 1024
MAX_LOGO_PIXELS = 16_000_000
MAX_LOGO_SIDE = 1024

MAX_LINK_CHARS = 200
MAX_OVERLAY_CHARS = 120
MAX_CTA_CHARS = 120
MAX_DESCRIPTION_CHARS = 500
MAX_RULE_CHARS = 1000

# Fractions of the frame. The top margin clears the platforms' top tabs
# (hook_placement.TOP_MARGIN); the stack ends above the hook's usual y of 0.20.
_MARGIN_X = 0.05
_MARGIN_TOP = 0.06
_GAP = 0.012
_FONT_RATIO = 0.03
# The closing call to action: on screen for the clip's last seconds, centred under
# SPLIT's seam captions (mid-frame) and above bottom captions and the platforms' UI.
CTA_SECONDS = 4.0
_CTA_CENTER_Y = 0.66
_CTA_FONT_RATIO = 0.05
_FONT_CANDIDATES = (
    os.path.join("fonts", "Montserrat-ExtraBold.ttf"),
    "DejaVuSans-Bold.ttf",
    "arialbd.ttf",
    "arial.ttf",
)
_CARRIES_BRAND_RE = re.compile(r'^(?:wm_)?(?:translated_[^_]+_)?brand_')
_CONTROL_RE = re.compile(r'[\x00-\x08\x0b-\x1f\x7f]')
_LOGO_DATA_RE = re.compile(r'^data:image/(png|jpeg|webp);base64,(.+)$', re.DOTALL)
_DESCRIPTION_KEYS = ("video_description_for_tiktok", "video_description_for_instagram")

_active = {}


def _text(raw, limit, name, multiline=False):
    if raw is None:
        return ""
    if not isinstance(raw, str):
        raise ValueError(f"{name} must be text")
    text = _CONTROL_RE.sub(" ", raw.replace("\r\n", "\n")).strip()
    if not multiline:
        text = " ".join(text.split())
    if len(text) > limit:
        raise ValueError(f"{name} is limited to {limit} characters")
    return text


def _decode_logo(data_url):
    """The uploaded logo as normalised PNG bytes. Re-encoding through Pillow
    means what lands on disk is always a plain raster of a bounded size."""
    from PIL import Image

    if not isinstance(data_url, str):
        raise ValueError("logo must be a PNG, JPEG or WebP data URL")
    match = _LOGO_DATA_RE.match(data_url.strip())
    if not match:
        raise ValueError("logo must be a PNG, JPEG or WebP data URL")
    try:
        raw = base64.b64decode(match.group(2), validate=True)
    except ValueError:
        raise ValueError("logo is not valid base64")
    if len(raw) > MAX_LOGO_BYTES:
        raise ValueError(f"logo is limited to {MAX_LOGO_BYTES // 1024} KB")
    try:
        with Image.open(io.BytesIO(raw)) as img:
            if img.format not in ("PNG", "JPEG", "WEBP"):
                raise ValueError("logo must be a PNG, JPEG or WebP image")
            if img.width * img.height > MAX_LOGO_PIXELS:
                raise ValueError("logo has too many pixels")
            rgba = img.convert("RGBA")
    except ValueError:
        raise
    except Exception:
        raise ValueError("logo could not be read as an image")
    rgba.thumbnail((MAX_LOGO_SIDE, MAX_LOGO_SIDE))
    out = io.BytesIO()
    rgba.save(out, format="PNG")
    return out.getvalue()


def parse(raw):
    """(spec, logo_png_bytes) from the API field, a dict or its JSON string.

    Empty input gives ({}, None). Raises ValueError with a message fit for a 400.
    """
    if raw in (None, "", {}):
        return {}, None
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except ValueError:
            raise ValueError("video_requirements must be valid JSON")
    if not isinstance(raw, dict):
        raise ValueError("video_requirements must be an object")

    logo = _decode_logo(raw["logo"]) if raw.get("logo") else None
    position = raw.get("logo_position") or DEFAULT_LOGO_POSITION
    if position not in LOGO_POSITIONS:
        raise ValueError(f"logo_position must be one of: {', '.join(LOGO_POSITIONS)}")
    try:
        width = float(raw.get("logo_width") or DEFAULT_LOGO_WIDTH)
    except (TypeError, ValueError):
        raise ValueError("logo_width must be a number")
    if not MIN_LOGO_WIDTH <= width <= MAX_LOGO_WIDTH:
        raise ValueError(f"logo_width must be between {MIN_LOGO_WIDTH} and {MAX_LOGO_WIDTH}")

    link = _text(raw.get("link"), MAX_LINK_CHARS, "link")
    if " " in link:
        raise ValueError("link must not contain spaces")
    spec = {
        "link": link,
        "link_on_video": bool(raw.get("link_on_video")) and bool(link),
        "overlay_text": _text(raw.get("overlay_text"), MAX_OVERLAY_CHARS, "overlay_text"),
        "cta_text": _text(raw.get("cta_text"), MAX_CTA_CHARS, "cta_text"),
        "description_text": _text(raw.get("description_text"), MAX_DESCRIPTION_CHARS,
                                  "description_text", multiline=True),
        "must_include": _text(raw.get("must_include"), MAX_RULE_CHARS,
                              "must_include", multiline=True),
        "must_avoid": _text(raw.get("must_avoid"), MAX_RULE_CHARS,
                            "must_avoid", multiline=True),
    }
    if logo:
        spec["logo_position"] = position
        spec["logo_width"] = width
    if not logo and not any(v for v in spec.values()):
        return {}, None
    return spec, logo


def save(job_dir, spec, logo=None):
    if not spec:
        return
    if logo:
        with open(os.path.join(job_dir, LOGO_FILE), "wb") as f:
            f.write(logo)
    with open(os.path.join(job_dir, SPEC_FILE), "w", encoding="utf-8") as f:
        json.dump(spec, f, ensure_ascii=False)


def load(job_dir):
    """The job's spec, or {} when it has none or the file is unreadable."""
    if not job_dir:
        return {}
    try:
        with open(os.path.join(job_dir, SPEC_FILE), "r", encoding="utf-8") as f:
            spec = json.load(f)
    except (OSError, ValueError):
        return {}
    return spec if isinstance(spec, dict) else {}


def activate(spec):
    """Set the rules the selection prompts of this job process read."""
    global _active
    _active = spec or {}


def prompt_block(spec=None):
    """Text appended to a selection prompt; empty when the job has no rules.

    Appended after ``str.format`` so braces in the user's text stay literal."""
    spec = _active if spec is None else spec
    include = (spec or {}).get("must_include")
    avoid = (spec or {}).get("must_avoid")
    if not include and not avoid:
        return ""
    lines = ["", "", "CAMPAIGN RULES, set by the user. They outrank virality:"]
    if include:
        lines += ["MUST INCLUDE (every clip should contain this):", include]
    if avoid:
        lines += ["MUST AVOID (no clip may contain this):", avoid]
    lines += [
        "How to apply them:",
        "- When scoring windows: a window containing MUST AVOID material scores under 10, "
        "and a window with MUST INCLUDE material beats an otherwise equal one.",
        "- When picking clips: set the boundaries so MUST AVOID material stays outside the "
        "clip. Return fewer clips rather than break a rule.",
        "- Titles, hooks and descriptions follow the same rules.",
        "- Never claim required material is there when it is not.",
        "",
    ]
    return "\n".join(lines)


def apply_to_descriptions(clip, spec):
    """Append the link and required text to the clip's descriptions. Idempotent."""
    parts = [p for p in ((spec or {}).get("link"), (spec or {}).get("description_text")) if p]
    if not parts:
        return
    suffix = "\n\n".join(parts)
    for key in _DESCRIPTION_KEYS:
        current = (clip.get(key) or "").rstrip()
        if not current.endswith(suffix):
            clip[key] = f"{current}\n\n{suffix}" if current else suffix


def needs_overlay(job_dir, spec=None):
    spec = load(job_dir) if spec is None else spec
    if not spec:
        return False
    return bool(spec.get("overlay_text") or spec.get("link_on_video") or spec.get("cta_text")
                or os.path.exists(os.path.join(job_dir, LOGO_FILE)))


def carries_brand(filename):
    """True when this file (or the file it derives from) already has the overlay."""
    return bool(_CARRIES_BRAND_RE.match(os.path.basename(filename or "")))


def unbranded_name(filename):
    """``brand_<final>`` -> ``<final>``; other names unchanged."""
    base = os.path.basename(filename or "")
    return base[len(PREFIX):] if base.startswith(PREFIX) else base


def _load_font(size):
    from PIL import ImageFont

    for candidate in _FONT_CANDIDATES:
        try:
            return ImageFont.truetype(candidate, size)
        except OSError:
            continue
    return ImageFont.load_default(size)


def _wrap(draw, text, font, max_width):
    # By character when one word is wider than the frame: a long URL has no
    # spaces, and it has to stay readable in full, not run off the edge.
    lines, current = [], ""
    for word in text.split():
        trial = f"{current} {word}".strip()
        if draw.textlength(trial, font=font) <= max_width:
            current = trial
            continue
        if current:
            lines.append(current)
            current = ""
        for char in word:
            if current and draw.textlength(current + char, font=font) > max_width:
                lines.append(current)
                current = ""
            current += char
    if current:
        lines.append(current)
    return lines


def build_overlay(job_dir, spec, width, height):
    """A transparent RGBA frame holding the logo and the required text lines."""
    from PIL import Image, ImageDraw

    canvas = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(canvas)
    margin_x = int(width * _MARGIN_X)
    y = int(height * _MARGIN_TOP)

    logo_path = os.path.join(job_dir, LOGO_FILE)
    if os.path.exists(logo_path):
        with Image.open(logo_path) as raw:
            logo = raw.convert("RGBA")
        logo_w = max(1, int(width * float(spec.get("logo_width") or DEFAULT_LOGO_WIDTH)))
        logo_h = max(1, round(logo.height * logo_w / logo.width))
        logo = logo.resize((logo_w, logo_h), Image.LANCZOS)
        position = spec.get("logo_position") or DEFAULT_LOGO_POSITION
        x = {"top-left": margin_x, "top-right": width - margin_x - logo_w,
             "top-center": (width - logo_w) // 2}.get(position, margin_x)
        canvas.alpha_composite(logo, (x, y))
        y += logo_h + int(height * _GAP)

    texts = []
    if spec.get("link_on_video") and spec.get("link"):
        texts.append(spec["link"])
    if spec.get("overlay_text"):
        texts.append(spec["overlay_text"])
    font_size = max(14, int(min(width, height) * _FONT_RATIO))
    font = _load_font(font_size)
    pad_x, pad_y = int(font_size * 0.6), int(font_size * 0.3)
    for text in texts:
        for line in _wrap(draw, text, font, width - 2 * (margin_x + pad_x)):
            line_w = draw.textlength(line, font=font)
            x = (width - line_w) / 2
            box = (x - pad_x, y, x + line_w + pad_x, y + font_size + 2 * pad_y)
            draw.rounded_rectangle(box, radius=int(font_size * 0.4), fill=(15, 23, 42, 215))
            draw.text((x, y + pad_y), line, font=font, fill=(255, 255, 255, 255))
            y += font_size + 2 * pad_y + int(height * _GAP / 2)
    return canvas


def build_cta_overlay(text, width, height):
    """A transparent RGBA frame holding the closing call to action as one card."""
    from PIL import Image, ImageDraw

    canvas = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(canvas)
    font_size = max(16, int(min(width, height) * _CTA_FONT_RATIO))
    font = _load_font(font_size)
    pad_x, pad_y = int(font_size * 0.8), int(font_size * 0.6)
    lines = _wrap(draw, text, font, width - 2 * (int(width * _MARGIN_X) + pad_x))
    line_h = int(font_size * 1.3)
    card_w = int(max(draw.textlength(line, font=font) for line in lines)) + 2 * pad_x
    card_h = line_h * len(lines) + 2 * pad_y
    x = (width - card_w) // 2
    y = int(height * _CTA_CENTER_Y) - card_h // 2
    draw.rounded_rectangle((x, y, x + card_w, y + card_h), radius=int(font_size * 0.5),
                           fill=(255, 255, 255, 245))
    for i, line in enumerate(lines):
        line_x = (width - draw.textlength(line, font=font)) / 2
        draw.text((line_x, y + pad_y + i * line_h), line, font=font, fill=(15, 23, 42, 255))
    return canvas


def overlay_filter(cta_start=None):
    """The filtergraph: the always-on overlay, then the CTA from ``cta_start``.
    Only a number reaches it: all user text is already pixels in the PNGs."""
    base = "[0:v][1:v]overlay=0:0:format=auto"
    if cta_start is None:
        return base
    return f"{base}[branded];[branded][2:v]overlay=0:0:format=auto:enable='gte(t,{cta_start:.3f})'"


def _probe_duration(video_path):
    return float(subprocess.check_output(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", video_path],
        stderr=subprocess.STDOUT, timeout=60,
    ).decode().strip())


def _probe_size(video_path):
    probe = subprocess.check_output(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=width,height", "-of", "csv=p=0:s=x", video_path],
        stderr=subprocess.STDOUT, timeout=60,
    ).decode().strip().split("x")
    return int(probe[0]), int(probe[1])


def brand_delivery(final_path):
    """The copy of a finished clip to serve: ``brand_<final>`` next to it.

    Returns ``final_path`` itself when the job has no overlay, when the file
    already carries it, or when the copy could not be made: a requirement must
    never cost the user the clip. The failure is logged so it can be seen.
    """
    job_dir = os.path.dirname(final_path)
    name = os.path.basename(final_path)
    spec = load(job_dir)
    if not needs_overlay(job_dir, spec) or carries_brand(name):
        return final_path
    out_path = os.path.join(job_dir, PREFIX + name)
    if os.path.exists(out_path) and os.path.getsize(out_path) > 0:
        return out_path

    token = uuid.uuid4().hex[:8]
    overlay_path = os.path.join(job_dir, f".overlay_{token}.png")
    cta_path = os.path.join(job_dir, f".overlay_cta_{token}.png")
    tmp_path = f"{out_path}.{token}.mp4"
    try:
        from ffmpeg_utils import _X264_ARGS, QUALITY, video_encode_args

        width, height = _probe_size(final_path)
        build_overlay(job_dir, spec, width, height).save(overlay_path)
        inputs = ["-i", final_path, "-i", overlay_path]
        cta_start = None
        if spec.get("cta_text"):
            build_cta_overlay(spec["cta_text"], width, height).save(cta_path)
            inputs += ["-i", cta_path]
            cta_start = max(0.0, _probe_duration(final_path) - CTA_SECONDS)
        # NVENC can refuse a session on a busy card. A clip missing its required
        # logo is worth a slower encode, so libx264 gets a turn.
        encoders = [video_encode_args(QUALITY)]
        if list(_X264_ARGS[QUALITY]) != encoders[0]:
            encoders.append(list(_X264_ARGS[QUALITY]))
        for encoder in encoders:
            # No metadata scrub: the file keeps what it has, including the AI-dub
            # mark that ffmpeg_utils.mark_ai_generated writes on translated clips.
            cmd = ["ffmpeg", "-y", *inputs,
                   "-filter_complex", overlay_filter(cta_start),
                   *encoder, "-c:a", "copy", "-movflags", "+faststart", tmp_path]
            result = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                                    timeout=1800)
            if result.returncode == 0 and os.path.exists(tmp_path):
                os.replace(tmp_path, out_path)
                return out_path
        tail = (result.stderr or b"").decode(errors="ignore")[-300:]
        raise RuntimeError(f"ffmpeg exit {result.returncode}: {tail}")
    except Exception as e:
        print(f"   ⚠️ Logo/text overlay failed, clip delivered without it "
              f"({type(e).__name__}: {e})")
        return final_path
    finally:
        for leftover in (overlay_path, cta_path, tmp_path):
            if os.path.exists(leftover):
                os.remove(leftover)
