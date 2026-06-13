import os
import sys
import time
import base64
import io
import requests
import psycopg2
from datetime import datetime, timezone
from html.parser import HTMLParser
from PIL import Image, ImageDraw, ImageFont

# ── CONFIG ────────────────────────────────────────────────────────────────────
DB_URL         = os.environ.get("DATABASE_URL")
GROQ_API_KEY   = os.environ.get("GROQ_API_KEY")
NEWS_API_KEY   = os.environ.get("NEWS_API_KEY")
WEBHOOK_URL    = os.environ.get("MAKE_WEBHOOK_URL")
GITHUB_TOKEN   = os.environ.get("GITHUB_TOKEN")
GITHUB_REPO    = "arabstartuphub-web/linkedinposts"
GITHUB_BRANCH  = "main"
GITHUB_BASE    = f"https://raw.githubusercontent.com/{GITHUB_REPO}/{GITHUB_BRANCH}/"

# All 3 Gemini keys — used for both text generation and image generation
GEMINI_KEYS = [k for k in [
    os.environ.get("GEMINI_API_KEY"),
    os.environ.get("GEMINI_API_KEY_BACKUP1"),
    os.environ.get("GEMINI_API_KEY_BACKUP2"),
] if k]

# ── IMAGE DESIGN ─────────────────────────────────────────────────────────────
IMG_W, IMG_H = 1080, 1080

FONT_BOLD   = "/usr/share/fonts/truetype/google-fonts/Poppins-Bold.ttf"
FONT_MEDIUM = "/usr/share/fonts/truetype/google-fonts/Poppins-Medium.ttf"
FONT_EMOJI  = "/usr/share/fonts/truetype/noto/NotoColorEmoji.ttf"

# Google Fonts download URLs — used as fallback when local path is missing
_FONT_URLS = {
    FONT_BOLD:   "https://github.com/google/fonts/raw/main/ofl/poppins/Poppins-Bold.ttf",
    FONT_MEDIUM: "https://github.com/google/fonts/raw/main/ofl/poppins/Poppins-Medium.ttf",
    FONT_EMOJI:  "https://github.com/googlefonts/noto-emoji/raw/main/fonts/NotoColorEmoji.ttf",
}
_font_cache: dict = {}


def _ensure_font(path: str) -> str:
    """Return a valid .ttf path: the original if present, otherwise download it to /tmp."""
    if path in _font_cache:
        return _font_cache[path]
    if os.path.exists(path):
        _font_cache[path] = path
        return path
    local = os.path.join("/tmp", os.path.basename(path))
    if not os.path.exists(local):
        url = _FONT_URLS.get(path)
        if url:
            print(f"Downloading font {os.path.basename(path)}...")
            try:
                r = requests.get(url, timeout=20)
                r.raise_for_status()
                with open(local, "wb") as fh:
                    fh.write(r.content)
                print(f"Font saved to {local}")
            except Exception as e:
                print(f"Font download failed: {e}")
                _font_cache[path] = path
                return path
    _font_cache[path] = local
    return local

WHITE     = (255, 255, 255)
BLACK     = (15,  15,  15)
ORANGE    = (224, 82,  18)
BLUE_LINE = (25,  100, 220)

# ── CARD TEXT COLORS (two-color scheme inside white card) ─────────────────────
# PRIMARY_TEXT  = dominant color for non-highlighted words
# ACCENT_TEXT   = highlight color for key financial/geo terms
PRIMARY_TEXT = BLACK   # dark charcoal — matches Arabian Startups style
ACCENT_TEXT  = ORANGE  # vivid orange — matches Smashi Business style

COUNTRY_MAP = {
    "Saudi Arabia": {"code": "KSA",     "flag": "🇸🇦"},
    "UAE":          {"code": "UAE",     "flag": "🇦🇪"},
    "Qatar":        {"code": "QATAR",   "flag": "🇶🇦"},
    "Kuwait":       {"code": "KUWAIT",  "flag": "🇰🇼"},
    "Oman":         {"code": "OMAN",    "flag": "🇴🇲"},
    "Bahrain":      {"code": "BAHRAIN", "flag": "🇧🇭"},
    "GCC":          {"code": "GCC",     "flag": "🌍"},
}

WEEKDAY_COUNTRY = {
    0: "Saudi Arabia",
    1: "UAE",
    2: "Qatar",
    3: "Kuwait",
    4: "Oman",
    5: "Bahrain",
    6: "GCC",
}

# Country visual identity for AI image prompts
COUNTRY_VISUAL = {
    "Saudi Arabia": "Riyadh skyline, Vision 2030 towers, desert gold tones, futuristic architecture",
    "UAE":          "Dubai Marina skyline, Burj Khalifa, modern glass towers, blue and silver tones",
    "Qatar":        "Doha corniche, pearl-shaped towers, warm amber desert light",
    "Kuwait":       "Kuwait City skyline, Liberation Tower, Arabian Gulf waterfront",
    "Oman":         "Muscat mountains, Sultan Qaboos Grand Mosque, warm terracotta tones",
    "Bahrain":      "Manama financial district, World Trade Center towers, sea reflections",
    "GCC":          "MENA region panoramic skyline, Arabian Gulf, diverse modern architecture",
}

HIGHLIGHT_WORDS = {
    "million","billion","trillion","fund","funding","raises","raised",
    "invest","investment","valuation","deal","unicorn","ipo","series",
    "saudi","arabia","uae","qatar","kuwait","oman","bahrain","gcc",
    "mena","dubai","riyadh","abu","dhabi","doha","muscat","manama",
    "lebanese","lebanon","arab","emirati","khaleeji","jordanian",
    "egyptian","moroccan","tunisian","iraqi","yemeni","libyan",
    "launches","launch","orders","ordered","wins","bans","ban",
    "lifts","lifted","builds","built","becomes","became","joins",
    "signs","acquires","expands","hits","secures","secured","closes",
    "closed","backs","backed","funds","funded","partners","partnered",
    "invests","invested","announces","announced","unveils","unveiled",
}


# ── FONT / DRAW HELPERS ───────────────────────────────────────────────────────

def get_font(path, size):
    resolved = _ensure_font(path)
    try:
        return ImageFont.truetype(resolved, size)
    except Exception:
        # Last resort: try system DejaVu which is always present on Ubuntu
        for fallback in ["/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
                         "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf"]:
            if os.path.exists(fallback):
                try:
                    return ImageFont.truetype(fallback, size)
                except Exception:
                    pass
        return ImageFont.load_default()


def ensure_noto_emoji():
    """Download Noto Color Emoji to /tmp if not installed system-wide."""
    if os.path.exists(FONT_EMOJI):
        return
    local = os.path.join("/tmp", "NotoColorEmoji.ttf")
    if os.path.exists(local):
        _font_cache[FONT_EMOJI] = local
        return
    url = _FONT_URLS[FONT_EMOJI]
    print("Downloading NotoColorEmoji font…")
    try:
        r = requests.get(url, timeout=30)
        r.raise_for_status()
        with open(local, "wb") as fh:
            fh.write(r.content)
        _font_cache[FONT_EMOJI] = local
        print(f"NotoColorEmoji saved to {local}")
    except Exception as e:
        print(f"⚠️  NotoColorEmoji download failed: {e}")


def measure(draw, text, font):
    bb = draw.textbbox((0, 0), text, font=font)
    return bb[2] - bb[0], bb[3] - bb[1]


def _is_emoji(char: str) -> bool:
    """Return True if the character is an emoji codepoint."""
    cp = ord(char)
    return (
        0x1F300 <= cp <= 0x1FAFF or  # Misc symbols, emoticons, transport, etc.
        0x2600  <= cp <= 0x27BF or   # Misc symbols & dingbats
        0x1F000 <= cp <= 0x1F02F or  # Mahjong / domino
        0x1F0A0 <= cp <= 0x1F0FF or  # Playing cards
        0xFE00  <= cp <= 0xFE0F or   # Variation selectors
        0x200D == cp                  # Zero-width joiner
    )


def draw_text_with_emoji(base_img: Image.Image, xy: tuple, text: str,
                         font, fill, emoji_size: int = None):
    """
    Draw text with emoji support by compositing Noto Color Emoji glyphs inline.
    Falls back to plain draw.text() if the emoji font is unavailable.
    """
    emoji_font_path = _ensure_font(FONT_EMOJI)
    if not os.path.exists(emoji_font_path):
        # No emoji font — draw plain text
        draw = ImageDraw.Draw(base_img)
        draw.text(xy, text, font=font, fill=fill)
        return

    # Split text into emoji / non-emoji segments
    segments = []
    buf = ""
    for ch in text:
        if _is_emoji(ch):
            if buf:
                segments.append(("text", buf))
                buf = ""
            segments.append(("emoji", ch))
        else:
            buf += ch
    if buf:
        segments.append(("text", buf))

    draw     = ImageDraw.Draw(base_img)
    x, y     = xy
    e_size   = emoji_size or (font.size if hasattr(font, "size") else 64)
    try:
        e_font = ImageFont.truetype(emoji_font_path, e_size)
    except Exception:
        draw.text(xy, text, font=font, fill=fill)
        return

    for seg_type, seg_text in segments:
        if seg_type == "text":
            draw.text((x, y), seg_text, font=font, fill=fill)
            bb = draw.textbbox((x, y), seg_text, font=font)
            x  = bb[2]
        else:
            # Render emoji via Noto Color Emoji onto a small RGBA patch
            try:
                patch = Image.new("RGBA", (e_size * 2, e_size * 2), (0, 0, 0, 0))
                pd    = ImageDraw.Draw(patch)
                pd.text((0, 0), seg_text, font=e_font, embedded_color=True)
                # Crop to actual glyph
                bb_patch = pd.textbbox((0, 0), seg_text, font=e_font)
                glyph_w  = max(bb_patch[2] - bb_patch[0], 1)
                glyph_h  = max(bb_patch[3] - bb_patch[1], 1)
                patch    = patch.crop((0, 0, glyph_w + 4, glyph_h + 4))
                base_img.paste(patch, (int(x), int(y)), patch)
                x += glyph_w + 4
            except Exception:
                # Pillow too old for embedded_color — skip emoji silently
                x += e_size


def word_color(word):
    clean = word.lower().strip(".,!?:;\"'()[]%#@")
    if clean.startswith("$") or (any(c.isdigit() for c in clean) and any(c.isalpha() for c in clean)):
        return ACCENT_TEXT
    if clean in HIGHLIGHT_WORDS:
        return ACCENT_TEXT
    if word.isupper() and len(word) >= 2 and word.isalpha():
        return ACCENT_TEXT
    return PRIMARY_TEXT


def wrap_words(draw, words, font, max_w):
    lines, cur = [], []
    for word in words:
        test = cur + [word]
        w, _ = measure(draw, " ".join(test), font)
        if w <= max_w or not cur:
            cur = test
        else:
            lines.append(cur)
            cur = [word]
    if cur:
        lines.append(cur)
    # Fix orphan last word
    if len(lines) >= 2 and len(lines[-1]) == 1 and len(lines[-2]) >= 3:
        moved = lines[-2].pop()
        lines[-1].insert(0, moved)
    return lines


def auto_fit(draw, headline, max_w, max_h, start=90, minimum=34):
    words = headline.split()
    for size in range(start, minimum - 1, -2):
        font   = get_font(FONT_BOLD, size)
        lines  = wrap_words(draw, words, font, max_w)
        line_h = int(size * 1.28)
        if len(lines) * line_h <= max_h and len(lines) <= 5:
            return font, lines, size, line_h
    font   = get_font(FONT_BOLD, minimum)
    lines  = wrap_words(draw, words, font, max_w)
    return font, lines, minimum, int(minimum * 1.28)


def draw_colored_line(draw, word_list, font, x, y):
    """Left-aligned word-by-word colored text (used internally)."""
    sp_w, _ = measure(draw, " ", font)
    cx = x
    for word in word_list:
        draw.text((cx, y), word, font=font, fill=word_color(word))
        w, _ = measure(draw, word, font)
        cx += w + sp_w


def draw_colored_line_centered(draw, word_list, font, card_x, card_w, y, base_img=None):
    """Center-aligned word-by-word colored text — Smashi style. Uses emoji compositing if base_img provided."""
    sp_w, _ = measure(draw, " ", font)
    line_text = " ".join(word_list)
    total_w, _ = measure(draw, line_text, font)
    start_x = card_x + (card_w - total_w) // 2
    cx = start_x
    for word in word_list:
        color = word_color(word)
        if base_img is not None:
            draw_text_with_emoji(base_img, (cx, y), word, font=font, fill=color)
        else:
            draw.text((cx, y), word, font=font, fill=color)
        w, _ = measure(draw, word, font)
        cx += w + sp_w


# ── AI IMAGE GENERATION ───────────────────────────────────────────────────────

def build_image_prompt(title: str, summary: str, country_name: str) -> str:
    """
    Use Groq (or Gemini text) to craft a detailed, vivid image generation prompt
    based on the article content and country visual identity.
    """
    country_visual = COUNTRY_VISUAL.get(country_name, "modern Middle East city, business district")

    meta_prompt = (
        f"You are creating a background image prompt for a LinkedIn post about this article:\n"
        f"Title: {title}\n"
        f"Summary: {summary or 'No summary available.'}\n"
        f"Country: {country_name}\n\n"
        f"Write a single vivid, detailed image generation prompt (max 120 words) for a "
        f"photorealistic editorial-style background image.\n\n"
        f"Rules:\n"
        f"- The image must feel professional, journalistic, and relevant to the article topic\n"
        f"- Incorporate this country's visual identity: {country_visual}\n"
        f"- No text, no logos, no overlays, no watermarks in the image\n"
        f"- Cinematic lighting, sharp focus, high detail\n"
        f"- Style: editorial photography, wide establishing shot or dramatic close-up\n"
        f"- If the article is about funding/investment: show a business handshake, modern boardroom, "
        f"or financial district at golden hour\n"
        f"- If about a startup/tech: show a modern coworking space, tech campus, or futuristic cityscape\n"
        f"- If about government/policy: show government buildings, official ceremony, or city skyline\n"
        f"- Output ONLY the image prompt, no preamble, no quotes, no explanation."
    )

    print("🧠 Generating AI image prompt…")
    try:
        return generate_with_groq(meta_prompt)
    except Exception:
        try:
            return generate_text_with_gemini(meta_prompt)
        except Exception:
            # Hard fallback prompt
            return (
                f"Photorealistic editorial photograph, {country_visual}, "
                f"cinematic golden hour lighting, sharp focus, wide establishing shot, "
                f"professional business atmosphere, no text, no logos"
            )


def generate_image_with_gemini(prompt: str) -> bytes:
    """
    Try to generate an image using Gemini image generation models.
    Tries 3 keys × 2 models = up to 6 attempts before giving up.
    Returns raw image bytes (JPEG/PNG) or raises RuntimeError.
    """
    if not GEMINI_KEYS:
        raise RuntimeError("No Gemini API keys configured.")

    # Model priority: imagen-3 is highest quality, flash-preview as fallback
    models = [
        {
            "name":     "imagen-3.0-generate-002",
            "endpoint": "https://us-central1-aiplatform.googleapis.com/v1/projects/{}/locations/us-central1/publishers/google/models/imagen-3.0-generate-002:predict",
            "type":     "imagen",
        },
        {
            "name":     "gemini-2.0-flash-preview-image-generation",
            "endpoint": "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash-preview-image-generation:generateContent?key={}",
            "type":     "gemini_multimodal",
        },
    ]

    for key_idx, key in enumerate(GEMINI_KEYS):
        for model in models:
            print(f"🎨 Trying {model['name']} with key {key_idx + 1}/{len(GEMINI_KEYS)}…")
            try:
                img_bytes = _call_gemini_image_model(model, key, prompt)
                if img_bytes:
                    print(f"✅ Image generated with {model['name']}")
                    return img_bytes
            except Exception as e:
                print(f"   ⚠️  {model['name']} key {key_idx+1} failed: {e}")
                if "429" in str(e) or "quota" in str(e).lower():
                    time.sleep(10)
                continue

    raise RuntimeError("All Gemini image generation attempts exhausted.")


def _call_gemini_image_model(model: dict, key: str, prompt: str) -> bytes:
    """Call one specific Gemini image model. Returns image bytes or raises."""

    if model["type"] == "gemini_multimodal":
        # gemini-2.0-flash-preview-image-generation
        url = model["endpoint"].format(key)
        payload = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"responseModalities": ["IMAGE"]},
        }
        r = requests.post(url, json=payload, timeout=60)
        if r.status_code != 200:
            raise RuntimeError(f"HTTP {r.status_code}: {r.text[:300]}")
        data = r.json()
        # Response: candidates[0].content.parts[].inlineData.data (base64)
        for candidate in data.get("candidates", []):
            for part in candidate.get("content", {}).get("parts", []):
                inline = part.get("inlineData", {})
                if inline.get("data"):
                    return base64.b64decode(inline["data"])
        raise RuntimeError(f"No image in response: {str(data)[:300]}")

    elif model["type"] == "imagen":
        # imagen-3.0-generate-002 uses a different endpoint + auth
        # For API key auth (not OAuth), use the generativelanguage endpoint
        url = (
            f"https://generativelanguage.googleapis.com/v1beta/"
            f"models/imagen-3.0-generate-002:predict?key={key}"
        )
        payload = {
            "instances": [{"prompt": prompt}],
            "parameters": {
                "sampleCount":   1,
                "aspectRatio":   "1:1",
                "safetyFilterLevel": "block_few",
                "personGeneration": "allow_adult",
            },
        }
        r = requests.post(url, json=payload, timeout=60)
        if r.status_code != 200:
            raise RuntimeError(f"HTTP {r.status_code}: {r.text[:300]}")
        data = r.json()
        # Response: predictions[0].bytesBase64Encoded
        preds = data.get("predictions", [])
        if preds and preds[0].get("bytesBase64Encoded"):
            return base64.b64decode(preds[0]["bytesBase64Encoded"])
        raise RuntimeError(f"No image in response: {str(data)[:300]}")

    raise RuntimeError(f"Unknown model type: {model['type']}")


def is_image_too_light(img: Image.Image, threshold: int = 195, light_fraction: float = 0.55) -> bool:
    """
    Returns True if the image is predominantly light/white.
    Uses perceptual luminance, weights the bottom half 2× (where the card sits).
    """
    small = img.resize((40, 40), Image.LANCZOS).convert("RGB")
    w, h  = small.size
    total = 0
    light = 0
    for y in range(h):
        weight = 2 if y > h // 2 else 1
        for x in range(w):
            r, g, b   = small.getpixel((x, y))
            luminance = (r * 299 + g * 587 + b * 114) // 1000
            total    += weight
            if luminance > threshold:
                light += weight
    ratio = light / total
    print(f"  Background brightness ratio: {ratio:.2f} ({'too light — skipping' if ratio > light_fraction else 'OK'})")
    return ratio > light_fraction


def get_background_image(source_url: str, title: str, summary: str, country_name: str):
    """
    Returns (image_bytes_or_None, source_label).
    Priority:
      1. og:image — only if it is dark enough to work as a background
      2. AI-generated image via Gemini (all 3 keys × 2 models)
      3. None → caller uses gradient fallback
    """
    # ── Step 1: Try og:image, reject if too light ──
    og_bytes = fetch_og_image_bytes(source_url)
    if og_bytes:
        try:
            og_img = Image.open(io.BytesIO(og_bytes)).convert("RGB")
            if not is_image_too_light(og_img):
                print("✅ Using og:image as background.")
                return og_bytes, "og_image"
            else:
                print("⚠️  og:image is too light/white — ignoring it, generating AI image instead.")
        except Exception as e:
            print(f"⚠️  og:image decode check failed: {e}")

    print("🎨 Generating AI background image…")

    # ── Step 2: AI-generated image ──
    try:
        img_prompt = build_image_prompt(title, summary, country_name)
        print(f"📝 Image prompt: {img_prompt[:120]}…")
        ai_bytes = generate_image_with_gemini(img_prompt)
        # Safety-check the AI image too (extremely unlikely to be white, but just in case)
        try:
            ai_img = Image.open(io.BytesIO(ai_bytes)).convert("RGB")
            if is_image_too_light(ai_img):
                print("⚠️  AI image also too light — falling back to gradient.")
                return None, "gradient"
        except Exception:
            pass
        return ai_bytes, "ai_generated"
    except Exception as e:
        print(f"⚠️  AI image generation failed: {e}")
        print("ℹ️  Falling back to gradient background.")
        return None, "gradient"


# ── GRADIENT FALLBACK ─────────────────────────────────────────────────────────

COUNTRY_GRADIENTS = {
    "Saudi Arabia": ((0,  80,  40),  (0,  30, 15)),
    "UAE":          ((0,  55, 110),  (0,  20, 60)),
    "Qatar":        ((75,  0,  40),  (35,  0, 18)),
    "Kuwait":       ((80, 58,   0),  (35, 25,  0)),
    "Oman":         ((60, 18,   0),  (28,  8,  0)),
    "Bahrain":      ((0,  38, 100),  (0,  15, 55)),
    "GCC":          ((18, 18,  60),  (5,   5, 28)),
}


def make_gradient_bg(country_name):
    top, bot = COUNTRY_GRADIENTS.get(country_name, ((18, 18, 60), (5, 5, 28)))
    img = Image.new("RGB", (IMG_W, IMG_H))
    px  = img.load()
    for y in range(IMG_H):
        t = y / IMG_H
        r = int(top[0] + (bot[0] - top[0]) * t)
        g = int(top[1] + (bot[1] - top[1]) * t)
        b = int(top[2] + (bot[2] - top[2]) * t)
        for x in range(IMG_W):
            px[x, y] = (r, g, b)
    return img


def prepare_background(img_bytes, country_name):
    if img_bytes:
        try:
            base = Image.open(io.BytesIO(img_bytes)).convert("RGB")
            bw, bh = base.size
            side  = min(bw, bh)
            left  = (bw - side) // 2
            top   = (bh - side) // 2
            base  = base.crop((left, top, left + side, top + side))
            base  = base.resize((IMG_W, IMG_H), Image.LANCZOS)
            return base
        except Exception as e:
            print(f"⚠️  Background decode failed: {e}")
    return make_gradient_bg(country_name)


# ── BRANDED IMAGE COMPOSER ───────────────────────────────────────────────────

def generate_branded_image(bg_bytes, headline, country_name, logo_bytes=None):
    """
    Compose final 1080×1080 branded image — Smashi Business style:
      - Full-bleed dark background (AI photo or country gradient)
      - Heavy bottom vignette so card always pops
      - White rounded card, auto-height based on text content
      - THICK blue border OUTLINE around the card (not just a bottom bar)
      - CENTER-aligned text — large bold ALL-CAPS headline
      - Per-word orange highlights on key terms
      - Country code pill top-left, logo top-right
    """
    base = prepare_background(bg_bytes, country_name)

    # ── Heavy vignette at bottom ──
    vignette = Image.new("RGBA", (IMG_W, IMG_H), (0, 0, 0, 0))
    vd = ImageDraw.Draw(vignette)
    VIGN_H = 520
    for i in range(VIGN_H):
        alpha = int((i / VIGN_H) ** 1.5 * 235)
        vd.rectangle(
            [(0, IMG_H - VIGN_H + i), (IMG_W, IMG_H - VIGN_H + i + 1)],
            fill=(0, 0, 0, alpha),
        )
    base = Image.alpha_composite(base.convert("RGBA"), vignette).convert("RGB")
    draw = ImageDraw.Draw(base)

    # ── Card geometry ──
    MARGIN      = 36           # side gap from image edge
    BORDER_W    = 6            # blue outline border thickness (Smashi style)
    PAD_X       = 60           # horizontal text padding inside card
    PAD_TOP     = 44           # top padding inside white area
    PAD_BOT     = 44           # bottom padding inside white area
    CARD_X      = MARGIN
    CARD_W      = IMG_W - 2 * MARGIN
    TEXT_W      = CARD_W - 2 * PAD_X - 2 * BORDER_W
    MAX_TEXT_H  = int(IMG_H * 0.46)
    MIN_FONT    = 58

    # Uppercase for bold news-card impact (Smashi does this)
    headline_upper = headline.upper()

    font, lines, fsize, line_h = auto_fit(
        draw, headline_upper, TEXT_W, MAX_TEXT_H, start=96, minimum=MIN_FONT
    )
    print(f"  📝 Font: {fsize}px — {len(lines)} lines")

    text_block_h = len(lines) * line_h
    inner_h      = PAD_TOP + text_block_h + PAD_BOT
    card_h       = inner_h + 2 * BORDER_W
    card_y       = IMG_H - MARGIN - card_h

    # Safety: never overlap logo/pill area
    if card_y < MARGIN + 150:
        card_y = MARGIN + 150

    # ── Shadow (soft dark drop) ──
    for s in range(8, 0, -1):
        alpha = 30 + (8 - s) * 10
        # Pillow rounded_rectangle fill is opaque — simulate with layered rects
        draw.rounded_rectangle(
            [CARD_X + s, card_y + s, CARD_X + CARD_W + s, card_y + card_h + s],
            radius=20, fill=(0, 0, 0),
        )

    # ── Blue border outline (Smashi signature) ──
    draw.rounded_rectangle(
        [CARD_X, card_y, CARD_X + CARD_W, card_y + card_h],
        radius=20, fill=BLUE_LINE,
    )

    # ── White inner card ──
    draw.rounded_rectangle(
        [CARD_X + BORDER_W, card_y + BORDER_W,
         CARD_X + CARD_W - BORDER_W, card_y + card_h - BORDER_W],
        radius=16, fill=WHITE,
    )

    # ── CENTER-aligned headline text ──
    inner_card_x = CARD_X + BORDER_W
    inner_card_w = CARD_W - 2 * BORDER_W
    # Vertically center text in white area
    text_start_y = card_y + BORDER_W + (inner_h - text_block_h) // 2
    ty = text_start_y
    for word_list in lines:
        draw_colored_line_centered(draw, word_list, font, inner_card_x, inner_card_w, ty, base_img=base)
        ty += line_h

    # ── Country code pill — top left ──
    country_code = COUNTRY_MAP.get(country_name, {}).get("code", country_name[:3].upper())
    code_font    = get_font(FONT_BOLD, 26)
    cw, ch       = measure(draw, country_code, code_font)
    pill_x, pill_y = 22, 22
    pill_w, pill_h = cw + 36, ch + 24
    draw.rounded_rectangle(
        [pill_x - 2, pill_y - 2, pill_x + pill_w + 2, pill_y + pill_h + 2],
        radius=14, fill=(20, 20, 20)
    )
    draw.rounded_rectangle(
        [pill_x, pill_y, pill_x + pill_w, pill_y + pill_h],
        radius=12, fill=WHITE
    )
    draw.text((pill_x + 18, pill_y + 12), country_code, font=code_font, fill=(20, 20, 80))

    # ── Logo — top right ──
    if logo_bytes:
        try:
            logo      = Image.open(io.BytesIO(logo_bytes)).convert("RGBA")
            logo_size = 110
            logo      = logo.resize((logo_size, logo_size), Image.LANCZOS)
            base.paste(logo, (IMG_W - logo_size - 18, 14), logo)
        except Exception as e:
            print(f"⚠️  Logo paste error: {e}")

    return base


# ── GITHUB UPLOAD ─────────────────────────────────────────────────────────────

def upload_image_to_github(img: Image.Image, filename: str) -> str:
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=94)
    content_b64 = base64.b64encode(buf.getvalue()).decode()

    api_url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/generated/{filename}"
    headers = {
        "Authorization": f"token {GITHUB_TOKEN}",
        "Accept":        "application/vnd.github.v3+json",
    }
    sha   = None
    check = requests.get(api_url, headers=headers)
    if check.status_code == 200:
        sha = check.json().get("sha")

    body = {
        "message": f"Auto-generated post image: {filename}",
        "content": content_b64,
        "branch":  GITHUB_BRANCH,
    }
    if sha:
        body["sha"] = sha

    res = requests.put(api_url, headers=headers, json=body, timeout=30)
    if res.status_code in [200, 201]:
        raw_url = f"{GITHUB_BASE}generated/{filename}"
        print(f"✅ Image uploaded: {raw_url}")
        return raw_url
    raise RuntimeError(f"GitHub upload failed {res.status_code}: {res.text}")


# ── OG IMAGE ─────────────────────────────────────────────────────────────────

def fetch_og_image_bytes(url: str):
    if not url:
        return None
    try:
        res = requests.get(url, timeout=10, headers={"User-Agent": "Mozilla/5.0"})
        if res.status_code != 200:
            return None

        class OGParser(HTMLParser):
            def __init__(self):
                super().__init__()
                self.og_image = None
            def handle_starttag(self, tag, attrs):
                if tag == "meta":
                    d = dict(attrs)
                    if d.get("property") == "og:image":
                        self.og_image = d.get("content")

        parser = OGParser()
        parser.feed(res.text)
        if not parser.og_image:
            return None

        print(f"✅ og:image: {parser.og_image}")
        img_res = requests.get(parser.og_image, timeout=10, headers={"User-Agent": "Mozilla/5.0"})
        if img_res.status_code == 200:
            return img_res.content
    except Exception as e:
        print(f"⚠️  og:image fetch failed: {e}")
    return None


# ── DATABASE ─────────────────────────────────────────────────────────────────

def get_country_for_today() -> str:
    return WEEKDAY_COUNTRY.get(datetime.now(timezone.utc).weekday(), "GCC")


def get_daily_article(country_name: str):
    conn = psycopg2.connect(DB_URL)
    cur  = conn.cursor()
    cur.execute(
        """
        SELECT id, title, summary, source_url
        FROM   articles
        WHERE  linkedin_posted = FALSE AND country = %s
        ORDER  BY published_at DESC
        LIMIT  1;
        """,
        (country_name,),
    )
    row = cur.fetchone()
    if not row and country_name != "GCC":
        print(f"No unposted articles for {country_name}. Trying GCC pool…")
        cur.execute(
            """
            SELECT id, title, summary, source_url
            FROM   articles
            WHERE  linkedin_posted = FALSE AND country = 'GCC'
            ORDER  BY published_at DESC
            LIMIT  1;
            """
        )
        row = cur.fetchone()
    cur.close()
    conn.close()
    return row


def mark_article_posted(article_id: int):
    conn = psycopg2.connect(DB_URL)
    cur  = conn.cursor()
    cur.execute("UPDATE articles SET linkedin_posted = TRUE WHERE id = %s;", (article_id,))
    conn.commit()
    cur.close()
    conn.close()
    print(f"✅ Article {article_id} marked as posted.")


def fetch_live_article(country_name: str):
    query_map = {
        "Saudi Arabia": "Saudi Arabia startup OR funding OR investment",
        "UAE":          "UAE startup OR funding OR investment",
        "Qatar":        "Qatar startup OR funding OR investment",
        "Kuwait":       "Kuwait startup OR funding OR investment",
        "Oman":         "Oman startup OR funding OR investment",
        "Bahrain":      "Bahrain startup OR funding OR investment",
        "GCC":          "GCC OR MENA startup OR funding OR investment",
    }
    query = query_map.get(country_name, "Arab startup ecosystem")
    url = (
        f"https://newsapi.org/v2/everything"
        f"?q={requests.utils.quote(query)}"
        f"&language=en&sortBy=publishedAt&pageSize=1"
        f"&apiKey={NEWS_API_KEY}"
    )
    try:
        res = requests.get(url, timeout=15)
        if res.status_code == 200:
            articles = res.json().get("articles", [])
            if articles:
                a = articles[0]
                print(f"✅ Live article: {a.get('title','')}")
                return a.get("title",""), a.get("description","") or a.get("content",""), a.get("url","")
    except Exception as e:
        print(f"NewsAPI error: {e}")
    return None, None, None


# ── AI TEXT GENERATION ────────────────────────────────────────────────────────

def generate_with_groq(prompt: str) -> str:
    if not GROQ_API_KEY:
        raise ValueError("GROQ_API_KEY missing.")
    url     = "https://api.groq.com/openai/v1/chat/completions"
    headers = {"Authorization": f"Bearer {GROQ_API_KEY}", "Content-Type": "application/json"}
    payload = {
        "model":       "llama-3.3-70b-versatile",
        "messages":    [{"role": "user", "content": prompt}],
        "temperature": 0.7,
    }
    for attempt in range(3):
        try:
            r = requests.post(url, json=payload, headers=headers, timeout=20)
            if r.status_code == 200:
                return r.json()["choices"][0]["message"]["content"].strip()
            elif r.status_code in [429, 503]:
                time.sleep(15 * (attempt + 1))
            else:
                raise RuntimeError(f"Groq {r.status_code}: {r.text}")
        except requests.exceptions.RequestException as e:
            time.sleep(15 * (attempt + 1))
    raise RuntimeError("Groq exhausted retries.")


def generate_text_with_gemini(prompt: str) -> str:
    if not GEMINI_KEYS:
        raise ValueError("No Gemini keys.")
    payload = {"contents": [{"parts": [{"text": prompt}]}]}
    for i, key in enumerate(GEMINI_KEYS):
        url = (
            f"https://generativelanguage.googleapis.com/v1beta/"
            f"models/gemini-2.0-flash:generateContent?key={key}"
        )
        for attempt in range(3):
            try:
                r = requests.post(url, json=payload, timeout=20)
                if r.status_code == 200:
                    return r.json()["candidates"][0]["content"]["parts"][0]["text"].strip()
                elif r.status_code in [429, 503]:
                    if attempt < 2:
                        time.sleep(35 * (attempt + 1))
                    else:
                        break
                else:
                    raise RuntimeError(f"Gemini {r.status_code}: {r.text}")
            except requests.exceptions.RequestException:
                time.sleep(35 * (attempt + 1))
    raise RuntimeError("Gemini text exhausted.")


def generate_post_content(title: str, summary: str, source_url: str) -> str:
    prompt = (
        f"Write a professional LinkedIn post for an Arab startup ecosystem audience.\n"
        f"Article title: {title}\n"
        f"Summary: {summary}\n\n"
        f"IMPORTANT: Write ONLY about the article above.\n"
        f"Requirements:\n"
        f"- Start with a compelling hook using a relevant emoji (no generic openers like 'Exciting news')\n"
        f"- 3-5 short paragraphs\n"
        f"- Use 1-2 relevant emojis per paragraph to enhance visual scanning (💡🚀💰🤝📈🌍🏙️⚡ etc.)\n"
        f"- Do NOT include the article URL inside the paragraphs\n"
        f"- After the last paragraph add exactly one blank line then: Read more: {source_url}\n"
        f"- After that add exactly one blank line then end with 4-6 relevant hashtags\n"
        f"- Tone: insightful, professional, engaging\n"
        f"- CRITICAL: No markdown. No asterisks. Plain text only. Emojis are allowed and encouraged."
    )
    print("🚀 Generating post with Groq…")
    try:
        return generate_with_groq(prompt)
    except Exception as e:
        print(f"⚠️  Groq failed: {e}. Falling back to Gemini…")
        try:
            return generate_text_with_gemini(prompt)
        except Exception as e2:
            print(f"❌ Both AI engines failed: {e2}")
            sys.exit(1)


# ── MAIN ─────────────────────────────────────────────────────────────────────

def main():
    ensure_noto_emoji()  # Download Noto Color Emoji if not present

    country_name = get_country_for_today()
    country_data = COUNTRY_MAP.get(country_name, {"code": "GCC", "flag": "🌍"})
    flag         = country_data["flag"]
    print(f"📅 Today: {country_name} {flag}")

    # 1. Get article
    article    = get_daily_article(country_name)
    article_id = None
    if not article:
        print(f"No DB articles for {country_name}. Fetching from NewsAPI…")
        db_title, summary, source_url = fetch_live_article(country_name)
        if not db_title:
            print("NewsAPI returned nothing. Exiting.")
            sys.exit(0)
    else:
        article_id, db_title, summary, source_url = article

    print(f"📰 Article: {db_title}")

    # 2. Generate LinkedIn post text (now includes emojis)
    post_text = generate_post_content(
        db_title or summary or country_name,
        summary  or "",
        source_url or "",
    )

    # 3. Build image headline — include country flag emoji prefix
    # Noto Color Emoji is used at render-time to composite emoji glyphs
    image_headline = f"{flag} " + ((db_title or "").strip() or post_text.split("\n")[0][:100])

    # 4. Get background: og:image → AI-generated → gradient
    bg_bytes, bg_source = get_background_image(
        source_url, db_title or "", summary or "", country_name
    )
    print(f"🖼  Background source: {bg_source}")

    # 5. Fetch logo
    logo_bytes = None
    try:
        lr         = requests.get(f"{GITHUB_BASE}logo.jpg", timeout=10)
        logo_bytes = lr.content if lr.status_code == 200 else None
    except Exception as e:
        print(f"⚠️  Logo fetch failed: {e}")

    # 6. Compose branded image
    print("🎨 Composing branded image…")
    branded_img = generate_branded_image(bg_bytes, image_headline, country_name, logo_bytes)

    # 7. Upload to GitHub
    filename      = f"post_{country_data['code']}_{datetime.now(timezone.utc).strftime('%Y%m%d')}.jpg"
    thumbnail_url = upload_image_to_github(branded_img, filename)

    # 8. Send to Make.com
    payload = {
        "text":          post_text,
        "url":           "",
        "title":         "",
        "thumbnail_url": thumbnail_url,
        "country":       country_name,
        "flag":          flag,
    }
    print("📤 Sending to Make.com webhook…")
    res = requests.post(WEBHOOK_URL, json=payload, timeout=30)
    if res.status_code in [200, 201, 204]:
        print("✅ Successfully sent to Make.com.")
        if article_id:
            mark_article_posted(article_id)
    else:
        print(f"❌ Webhook failed {res.status_code}: {res.text}")
        sys.exit(1)


if __name__ == "__main__":
    main()
