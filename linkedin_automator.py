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

GEMINI_KEYS = [k for k in [
    os.environ.get("GEMINI_API_KEY"),
    os.environ.get("GEMINI_API_KEY_BACKUP1"),
    os.environ.get("GEMINI_API_KEY_BACKUP2"),
] if k]

# ── IMAGE DESIGN ──────────────────────────────────────────────────────────────
IMG_W, IMG_H = 1080, 1080

FONT_BOLD   = "/usr/share/fonts/truetype/google-fonts/Poppins-Bold.ttf"
FONT_MEDIUM = "/usr/share/fonts/truetype/google-fonts/Poppins-Medium.ttf"
FONT_EMOJI  = "/usr/share/fonts/truetype/noto/NotoColorEmoji.ttf"

_FONT_URLS = {
    FONT_BOLD:   "https://github.com/google/fonts/raw/main/ofl/poppins/Poppins-Bold.ttf",
    FONT_MEDIUM: "https://github.com/google/fonts/raw/main/ofl/poppins/Poppins-Medium.ttf",
    FONT_EMOJI:  "https://github.com/googlefonts/noto-emoji/raw/main/fonts/NotoColorEmoji.ttf",
}
_font_cache: dict = {}

WHITE     = (255, 255, 255)
BLACK     = (15,  15,  15)
ORANGE    = (224, 82,  18)
BLUE_LINE = (25,  100, 220)

# Two card text colors: black for normal words, orange for impactful words
PRIMARY_TEXT = BLACK
ACCENT_TEXT  = ORANGE

# ── SAFE SINGLE-CODEPOINT EMOJI for image card (no flags, no variation selectors)
# Each of these is exactly ONE Unicode codepoint — guaranteed to render via Noto
SAFE_EMOJI = ["💰", "🚀", "📈", "⚡", "🤝", "🎯", "🌍", "💡", "🏆", "🔥", "💼", "🌐", "📊", "🎉", "✅", "🔑", "💎", "⚙️"]
# Note: ⚙️ has variation selector but is widely supported — keep as last resort

# Truly safe (pure single codepoint, no variation selector):
PURE_SAFE_EMOJI = ["💰", "🚀", "📈", "⚡", "🤝", "🎯", "🌍", "💡", "🏆", "🔥", "💼", "🌐", "📊", "🎉", "✅", "🔑", "💎"]

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

COUNTRY_VISUAL = {
    "Saudi Arabia": "Riyadh skyline, Vision 2030 towers, desert gold tones, futuristic architecture",
    "UAE":          "Dubai Marina skyline, Burj Khalifa, modern glass towers, blue and silver tones",
    "Qatar":        "Doha corniche, pearl-shaped towers, warm amber desert light",
    "Kuwait":       "Kuwait City skyline, Liberation Tower, Arabian Gulf waterfront",
    "Oman":         "Muscat mountains, Sultan Qaboos Grand Mosque, warm terracotta tones",
    "Bahrain":      "Manama financial district, World Trade Center towers, sea reflections",
    "GCC":          "MENA region panoramic skyline, Arabian Gulf, diverse modern architecture",
}

COUNTRY_GRADIENTS = {
    "Saudi Arabia": ((0,  80,  40),  (0,  30, 15)),
    "UAE":          ((0,  55, 110),  (0,  20, 60)),
    "Qatar":        ((75,  0,  40),  (35,  0, 18)),
    "Kuwait":       ((80, 58,   0),  (35, 25,  0)),
    "Oman":         ((60, 18,   0),  (28,  8,  0)),
    "Bahrain":      ((0,  38, 100),  (0,  15, 55)),
    "GCC":          ((18, 18,  60),  (5,   5, 28)),
}

HIGHLIGHT_WORDS = {
    # Money / scale
    "million", "billion", "trillion", "mn", "bn",
    # Finance
    "fund", "funding", "funds", "funded", "raises", "raised", "raise",
    "invest", "investment", "investments", "investor", "investors",
    "valuation", "deal", "deals", "unicorn", "ipo", "series", "capital", "vc",
    "grant", "grants", "backs", "backed", "secures", "secured", "closes", "closed",
    "invests", "invested", "partners", "partnered",
    # Action verbs
    "launches", "launch", "launched", "debuts", "debut", "unveils", "unveiled",
    "wins", "win", "bans", "ban", "lifts", "lifted", "builds", "built",
    "becomes", "became", "joins", "signs", "acquires", "acquired", "acquisition",
    "expands", "expanded", "hits", "announces", "announced",
    "supports", "supported", "opens", "awards", "awarded",
    "creates", "targets", "grows", "selects", "selected",
    # GCC / MENA geo
    "saudi", "arabia", "uae", "qatar", "kuwait", "oman", "bahrain", "gcc", "mena",
    "dubai", "riyadh", "abu", "dhabi", "doha", "muscat", "manama",
    "jeddah", "neom", "vision", "2030",
    # Impact words
    "record", "first", "largest", "biggest", "new", "major", "key", "top",
    "leading", "fastest", "global", "regional", "international",
    # Known orgs
    "tamkeen", "stc", "aramco", "adnoc", "misk", "sdaia", "sabic",
}


# ── FONT HELPERS ──────────────────────────────────────────────────────────────

def _ensure_font(path: str) -> str:
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
                r = requests.get(url, timeout=30)
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


def get_font(path, size):
    resolved = _ensure_font(path)
    try:
        return ImageFont.truetype(resolved, size)
    except Exception:
        for fallback in [
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
            "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
        ]:
            if os.path.exists(fallback):
                try:
                    return ImageFont.truetype(fallback, size)
                except Exception:
                    pass
        return ImageFont.load_default()


def ensure_noto_emoji():
    """Download Noto Color Emoji to /tmp if not installed system-wide."""
    if os.path.exists(FONT_EMOJI):
        _font_cache[FONT_EMOJI] = FONT_EMOJI
        return
    local = os.path.join("/tmp", "NotoColorEmoji.ttf")
    if os.path.exists(local):
        _font_cache[FONT_EMOJI] = local
        return
    url = _FONT_URLS[FONT_EMOJI]
    print("Downloading NotoColorEmoji font…")
    try:
        r = requests.get(url, timeout=60)
        r.raise_for_status()
        with open(local, "wb") as fh:
            fh.write(r.content)
        _font_cache[FONT_EMOJI] = local
        print(f"NotoColorEmoji saved to {local}")
    except Exception as e:
        print(f"⚠️  NotoColorEmoji download failed: {e}")


# ── EMOJI SEGMENTATION ────────────────────────────────────────────────────────

def _is_emoji_cp(cp: int) -> bool:
    return (
        0x1F300 <= cp <= 0x1FAFF or
        0x2600  <= cp <= 0x27BF  or
        0x1F000 <= cp <= 0x1F02F or
        0x1F0A0 <= cp <= 0x1F0FF or
        0xFE00  <= cp <= 0xFE0F  or
        cp == 0x200D              or
        0x1F1E0 <= cp <= 0x1F1FF
    )


def _split_grapheme_clusters(text: str):
    """
    Split text into [('text'|'emoji', str)] tuples.
    Correctly handles: flag pairs (RI+RI), ZWJ sequences, skin-tone modifiers,
    variation selectors, and plain single-codepoint emoji.
    """
    segments = []
    buf      = ""
    chars    = list(text)
    i        = 0
    while i < len(chars):
        cp = ord(chars[i])

        # Regional Indicator pair → flag (e.g. 🇧🇭)
        if (0x1F1E0 <= cp <= 0x1F1FF
                and i + 1 < len(chars)
                and 0x1F1E0 <= ord(chars[i + 1]) <= 0x1F1FF):
            if buf:
                segments.append(("text", buf))
                buf = ""
            segments.append(("emoji", chars[i] + chars[i + 1]))
            i += 2
            continue

        if _is_emoji_cp(cp):
            if buf:
                segments.append(("text", buf))
                buf = ""
            cluster = chars[i]
            i += 1
            # Absorb variation selectors, skin-tone modifiers, ZWJ + next base
            while i < len(chars):
                ncp = ord(chars[i])
                if ncp == 0x200D or 0xFE00 <= ncp <= 0xFE0F or 0x1F3FB <= ncp <= 0x1F3FF:
                    cluster += chars[i]
                    i += 1
                    if ncp == 0x200D and i < len(chars) and _is_emoji_cp(ord(chars[i])):
                        cluster += chars[i]
                        i += 1
                else:
                    break
            segments.append(("emoji", cluster))
            continue

        buf += chars[i]
        i   += 1

    if buf:
        segments.append(("text", buf))
    return segments


def is_pure_emoji_token(token: str) -> bool:
    """Return True if every grapheme cluster in token is emoji."""
    segs = _split_grapheme_clusters(token)
    return bool(segs) and all(t == "emoji" for t, _ in segs)


def count_emoji_in(text: str) -> int:
    return sum(1 for t, _ in _split_grapheme_clusters(text) if t == "emoji")


def pick_emoji_for(title_lower: str, exclude: str = "") -> str:
    """Pick the most contextually relevant safe emoji for a headline."""
    if any(w in title_lower for w in ["fund", "invest", "raise", "million", "billion", "capital"]):
        candidates = ["💰", "📈"]
    elif any(w in title_lower for w in ["ipo", "stock", "nasdaq", "market"]):
        candidates = ["📈", "💰"]
    elif any(w in title_lower for w in ["launch", "debut", "unveil", "new"]):
        candidates = ["🚀", "🎯"]
    elif any(w in title_lower for w in ["ai", "tech", "digital", "software", "platform"]):
        candidates = ["⚡", "💡"]
    elif any(w in title_lower for w in ["partner", "deal", "sign", "agreement", "acqui"]):
        candidates = ["🤝", "💼"]
    elif any(w in title_lower for w in ["accelerat", "startup", "founder", "incubat"]):
        candidates = ["🎯", "🚀"]
    elif any(w in title_lower for w in ["award", "win", "record", "first", "largest"]):
        candidates = ["🏆", "🔥"]
    else:
        candidates = ["🌍", "💡"]
    for c in candidates:
        if c != exclude:
            return c
    return "💡" if exclude != "💡" else "🌐"


# ── TEXT DRAWING ──────────────────────────────────────────────────────────────

def measure(draw, text, font):
    bb = draw.textbbox((0, 0), text, font=font)
    return bb[2] - bb[0], bb[3] - bb[1]


def word_color(word: str) -> tuple:
    """Orange for impactful/financial/geo words, black for the rest."""
    clean = word.lower().strip(".,!?:;\"'()[]%#@$")
    if any(c.isdigit() for c in clean):
        return ACCENT_TEXT
    if word.startswith(("$", "€", "£", "BD", "AED", "SAR", "QAR", "KWD", "OMR", "BHD")):
        return ACCENT_TEXT
    if clean in HIGHLIGHT_WORDS:
        return ACCENT_TEXT
    return PRIMARY_TEXT


def draw_emoji_glyph(base_img: Image.Image, x: int, y: int,
                     cluster: str, e_font, font_size: int) -> int:
    """
    Render a single emoji cluster onto base_img at (x, y).
    Returns the pixel width consumed.
    """
    try:
        patch_size = font_size * 3
        patch = Image.new("RGBA", (patch_size, patch_size), (0, 0, 0, 0))
        pd    = ImageDraw.Draw(patch)
        pd.text((0, 0), cluster, font=e_font, embedded_color=True)
        bb   = pd.textbbox((0, 0), cluster, font=e_font)
        gw   = max(bb[2] - bb[0], 1)
        gh   = max(bb[3] - bb[1], 1)
        patch = patch.crop((0, 0, min(gw + 4, patch_size), min(gh + 4, patch_size)))
        # Vertically center emoji on the text baseline
        ey = int(y + (font_size - gh) // 2)
        base_img.paste(patch, (int(x), ey), patch)
        return gw + 6
    except Exception:
        return font_size  # fallback: advance by font size


def tokenize_headline(text: str) -> list:
    """
    Split headline into tokens keeping emoji clusters atomic.
    Returns list of str tokens (words or emoji clusters).
    """
    tokens = []
    for seg_type, seg_text in _split_grapheme_clusters(text):
        if seg_type == "emoji":
            tokens.append(seg_text)
        else:
            tokens.extend(seg_text.split())
    return [t for t in tokens if t]


def measure_token_width(draw, token: str, font) -> int:
    """Pixel width of a single token (word or emoji cluster)."""
    if is_pure_emoji_token(token):
        # Emoji width = font size (square glyph approximation)
        fs = font.size if hasattr(font, "size") else 64
        return fs + 6
    bb = draw.textbbox((0, 0), token, font=font)
    return bb[2] - bb[0]


def wrap_tokens(draw, tokens: list, font, max_w: int) -> list:
    """Word-wrap a token list to max_w pixels. Returns list of lines (each a list of tokens)."""
    sp_w = measure(draw, " ", font)[0]
    lines, cur, cur_w = [], [], 0
    for token in tokens:
        tw = measure_token_width(draw, token, font)
        needed = (cur_w + sp_w + tw) if cur else tw
        if needed <= max_w or not cur:
            cur.append(token)
            cur_w = needed
        else:
            lines.append(cur)
            cur, cur_w = [token], tw
    if cur:
        lines.append(cur)
    # Anti-orphan: move last word of second-to-last line down if last line is 1 word
    if len(lines) >= 2 and len(lines[-1]) == 1 and len(lines[-2]) >= 3:
        lines[-1].insert(0, lines[-2].pop())
    return lines


def auto_fit(draw, headline: str, max_w: int, max_h: int,
             start: int = 96, minimum: int = 48):
    """Find largest font size where text fits in max_w × max_h. Returns (font, lines, size, line_h)."""
    tokens = tokenize_headline(headline)
    for size in range(start, minimum - 1, -2):
        font   = get_font(FONT_BOLD, size)
        lines  = wrap_tokens(draw, tokens, font, max_w)
        line_h = int(size * 1.30)
        if len(lines) * line_h <= max_h and len(lines) <= 5:
            return font, lines, size, line_h
    font  = get_font(FONT_BOLD, minimum)
    lines = wrap_tokens(draw, tokens, font, max_w)
    return font, lines, minimum, int(minimum * 1.30)


def draw_headline_line_centered(base_img: Image.Image, draw, word_list: list,
                                 font, e_font, card_x: int, card_w: int, y: int):
    """
    Draw one line of headline tokens centered in the card.
    Handles emoji compositing and per-word two-color scheme.
    """
    fs   = font.size if hasattr(font, "size") else 64
    sp_w = measure(draw, " ", font)[0]

    # Calculate total line width for centering
    total_w = sum(measure_token_width(draw, t, font) for t in word_list)
    total_w += sp_w * max(0, len(word_list) - 1)
    cx = card_x + (card_w - total_w) // 2

    for idx, token in enumerate(word_list):
        if is_pure_emoji_token(token):
            # Render each cluster in the token
            for _, cluster in _split_grapheme_clusters(token):
                if e_font:
                    w = draw_emoji_glyph(base_img, cx, y, cluster, e_font, fs)
                else:
                    w = fs + 6
                cx += w
        else:
            color = word_color(token)
            draw.text((cx, y), token, font=font, fill=color)
            cx += measure_token_width(draw, token, font)
        if idx < len(word_list) - 1:
            cx += sp_w


# ── AI IMAGE GENERATION ───────────────────────────────────────────────────────

def build_image_prompt(title: str, summary: str, country_name: str) -> str:
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
        f"- If about funding/investment: business handshake, modern boardroom, or financial district at golden hour\n"
        f"- If about startup/tech: modern coworking space, tech campus, or futuristic cityscape\n"
        f"- If about government/policy: government buildings, official ceremony, or city skyline\n"
        f"- Output ONLY the image prompt, no preamble, no quotes, no explanation."
    )
    print("🧠 Generating AI image prompt…")
    try:
        return generate_with_groq(meta_prompt)
    except Exception:
        try:
            return generate_text_with_gemini(meta_prompt)
        except Exception:
            return (
                f"Photorealistic editorial photograph, {country_visual}, "
                f"cinematic golden hour lighting, sharp focus, wide establishing shot, "
                f"professional business atmosphere, no text, no logos"
            )


def generate_image_with_gemini(prompt: str) -> bytes:
    if not GEMINI_KEYS:
        raise RuntimeError("No Gemini API keys configured.")
    models = [
        {
            "name":     "imagen-3.0-generate-002",
            "type":     "imagen",
        },
        {
            "name":     "gemini-2.0-flash-preview-image-generation",
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
    raise RuntimeError("All Gemini image generation attempts exhausted.")


def _call_gemini_image_model(model: dict, key: str, prompt: str) -> bytes:
    if model["type"] == "gemini_multimodal":
        url = (
            f"https://generativelanguage.googleapis.com/v1beta/models/"
            f"gemini-2.0-flash-preview-image-generation:generateContent?key={key}"
        )
        payload = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"responseModalities": ["IMAGE"]},
        }
        r = requests.post(url, json=payload, timeout=60)
        if r.status_code != 200:
            raise RuntimeError(f"HTTP {r.status_code}: {r.text[:300]}")
        data = r.json()
        for candidate in data.get("candidates", []):
            for part in candidate.get("content", {}).get("parts", []):
                inline = part.get("inlineData", {})
                if inline.get("data"):
                    return base64.b64decode(inline["data"])
        raise RuntimeError(f"No image in response: {str(data)[:300]}")

    elif model["type"] == "imagen":
        url = (
            f"https://generativelanguage.googleapis.com/v1beta/"
            f"models/imagen-3.0-generate-002:predict?key={key}"
        )
        payload = {
            "instances": [{"prompt": prompt}],
            "parameters": {
                "sampleCount":       1,
                "aspectRatio":       "1:1",
                "safetyFilterLevel": "block_few",
                "personGeneration":  "allow_adult",
            },
        }
        r = requests.post(url, json=payload, timeout=60)
        if r.status_code != 200:
            raise RuntimeError(f"HTTP {r.status_code}: {r.text[:300]}")
        data  = r.json()
        preds = data.get("predictions", [])
        if preds and preds[0].get("bytesBase64Encoded"):
            return base64.b64decode(preds[0]["bytesBase64Encoded"])
        raise RuntimeError(f"No image in response: {str(data)[:300]}")

    raise RuntimeError(f"Unknown model type: {model['type']}")


# ── BACKGROUND ────────────────────────────────────────────────────────────────

def is_image_too_light(img: Image.Image,
                       threshold: int = 195,
                       light_fraction: float = 0.55) -> bool:
    small = img.resize((40, 40), Image.LANCZOS).convert("RGB")
    w, h  = small.size
    total = light = 0
    for y in range(h):
        weight = 2 if y > h // 2 else 1
        for x in range(w):
            r, g, b   = small.getpixel((x, y))
            luminance = (r * 299 + g * 587 + b * 114) // 1000
            total    += weight
            if luminance > threshold:
                light += weight
    ratio = light / total
    print(f"  Brightness ratio: {ratio:.2f} ({'too light' if ratio > light_fraction else 'OK'})")
    return ratio > light_fraction


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
        print(f"✅ og:image found: {parser.og_image}")
        img_res = requests.get(parser.og_image, timeout=10, headers={"User-Agent": "Mozilla/5.0"})
        if img_res.status_code == 200:
            return img_res.content
    except Exception as e:
        print(f"⚠️  og:image fetch failed: {e}")
    return None


def make_gradient_bg(country_name: str) -> Image.Image:
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


def prepare_background(img_bytes, country_name: str) -> Image.Image:
    if img_bytes:
        try:
            base  = Image.open(io.BytesIO(img_bytes)).convert("RGB")
            bw, bh = base.size
            side  = min(bw, bh)
            left  = (bw - side) // 2
            top   = (bh - side) // 2
            base  = base.crop((left, top, left + side, top + side))
            return base.resize((IMG_W, IMG_H), Image.LANCZOS)
        except Exception as e:
            print(f"⚠️  Background decode failed: {e}")
    return make_gradient_bg(country_name)


def get_background_image(source_url: str, title: str, summary: str, country_name: str):
    """
    Priority: 1) og:image (if dark enough)  2) Gemini AI image  3) gradient
    Returns (bytes_or_None, source_label)
    """
    # Step 1: og:image
    og_bytes = fetch_og_image_bytes(source_url)
    if og_bytes:
        try:
            og_img = Image.open(io.BytesIO(og_bytes)).convert("RGB")
            if not is_image_too_light(og_img):
                print("✅ Using og:image as background.")
                return og_bytes, "og_image"
            else:
                print("⚠️  og:image too light — trying AI image.")
        except Exception as e:
            print(f"⚠️  og:image decode failed: {e}")

    # Step 2: AI-generated image
    print("🎨 Generating AI background image…")
    try:
        img_prompt = build_image_prompt(title, summary, country_name)
        print(f"📝 Image prompt (preview): {img_prompt[:100]}…")
        ai_bytes = generate_image_with_gemini(img_prompt)
        try:
            ai_img = Image.open(io.BytesIO(ai_bytes)).convert("RGB")
            if is_image_too_light(ai_img):
                print("⚠️  AI image too light — falling back to gradient.")
                return None, "gradient"
        except Exception:
            pass
        return ai_bytes, "ai_generated"
    except Exception as e:
        print(f"⚠️  AI image generation failed: {e}")

    # Step 3: gradient fallback
    print("ℹ️  Using country gradient background.")
    return None, "gradient"


# ── BRANDED IMAGE COMPOSER ────────────────────────────────────────────────────

def generate_branded_image(bg_bytes, headline: str, country_name: str,
                           logo_bytes=None) -> Image.Image:
    """
    Compose final 1080×1080 branded image:
      - Full-bleed dark background (og image / AI image / country gradient)
      - Heavy bottom vignette
      - White rounded card with blue border
      - Title Case headline — two-color (black + orange) per word
      - Emoji rendered via Noto Color Emoji compositing
      - Country code pill top-left, logo top-right
    """
    base = prepare_background(bg_bytes, country_name)

    # Heavy vignette at bottom so white card always pops
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

    # Card geometry
    MARGIN   = 36
    BORDER_W = 6
    PAD_X    = 56
    PAD_TOP  = 44
    PAD_BOT  = 44
    CARD_X   = MARGIN
    CARD_W   = IMG_W - 2 * MARGIN
    TEXT_W   = CARD_W - 2 * PAD_X - 2 * BORDER_W
    MAX_TEXT_H = int(IMG_H * 0.44)
    MIN_FONT   = 52

    # Headline: Title Case, emoji preserved exactly as returned by AI
    headline_display = headline.strip()

    font, lines, fsize, line_h = auto_fit(
        draw, headline_display, TEXT_W, MAX_TEXT_H, start=92, minimum=MIN_FONT
    )
    print(f"  📝 Font: {fsize}px  Lines: {len(lines)}")

    # Load emoji font at matching size
    e_font = None
    emoji_font_path = _ensure_font(FONT_EMOJI)
    if os.path.exists(emoji_font_path):
        try:
            e_font = ImageFont.truetype(emoji_font_path, fsize)
        except Exception as e:
            print(f"⚠️  Could not load emoji font at size {fsize}: {e}")

    text_block_h = len(lines) * line_h
    inner_h      = PAD_TOP + text_block_h + PAD_BOT
    card_h       = inner_h + 2 * BORDER_W
    card_y       = IMG_H - MARGIN - card_h
    if card_y < MARGIN + 150:
        card_y = MARGIN + 150

    # Drop shadow
    for s in range(8, 0, -1):
        draw.rounded_rectangle(
            [CARD_X + s, card_y + s, CARD_X + CARD_W + s, card_y + card_h + s],
            radius=20, fill=(0, 0, 0),
        )

    # Blue border
    draw.rounded_rectangle(
        [CARD_X, card_y, CARD_X + CARD_W, card_y + card_h],
        radius=20, fill=BLUE_LINE,
    )

    # White inner card
    draw.rounded_rectangle(
        [CARD_X + BORDER_W, card_y + BORDER_W,
         CARD_X + CARD_W - BORDER_W, card_y + card_h - BORDER_W],
        radius=16, fill=WHITE,
    )

    # Draw headline text
    inner_card_x = CARD_X + BORDER_W
    inner_card_w = CARD_W - 2 * BORDER_W
    text_start_y = card_y + BORDER_W + (inner_h - text_block_h) // 2
    ty = text_start_y
    for word_list in lines:
        draw_headline_line_centered(
            base, draw, word_list, font, e_font,
            inner_card_x, inner_card_w, ty
        )
        ty += line_h

    # Country code pill — top left
    country_code = COUNTRY_MAP.get(country_name, {}).get("code", country_name[:3].upper())
    code_font    = get_font(FONT_BOLD, 26)
    cw, ch       = measure(draw, country_code, code_font)
    pill_x, pill_y = 22, 22
    pill_w, pill_h = cw + 36, ch + 24
    draw.rounded_rectangle(
        [pill_x - 2, pill_y - 2, pill_x + pill_w + 2, pill_y + pill_h + 2],
        radius=14, fill=(20, 20, 20),
    )
    draw.rounded_rectangle(
        [pill_x, pill_y, pill_x + pill_w, pill_y + pill_h],
        radius=12, fill=WHITE,
    )
    draw.text((pill_x + 18, pill_y + 12), country_code, font=code_font, fill=(20, 20, 80))

    # Logo — top right
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


# ── DATABASE ──────────────────────────────────────────────────────────────────

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
                print(f"✅ Live article: {a.get('title', '')}")
                return a.get("title", ""), a.get("description", "") or a.get("content", ""), a.get("url", "")
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
        except requests.exceptions.RequestException:
            time.sleep(15 * (attempt + 1))
    raise RuntimeError("Groq exhausted retries.")


def generate_text_with_gemini(prompt: str) -> str:
    if not GEMINI_KEYS:
        raise ValueError("No Gemini keys.")
    payload = {"contents": [{"parts": [{"text": prompt}]}]}
    for key in GEMINI_KEYS:
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


def build_image_headline(title: str, country_name: str) -> str:
    """
    Build the image card headline:
    1. Ask AI for a punchy ≤10-word rewrite with 2 safe emoji placed contextually.
    2. Validate the result has exactly 2 emoji — if not, enforce deterministically.
    3. Never use flag emoji or multi-codepoint sequences.
    """
    safe_list = " ".join(PURE_SAFE_EMOJI)
    ai_prompt = (
        f"Rewrite this article title as a short punchy LinkedIn image card headline.\n"
        f"Original title: {title}\n"
        f"Country: {country_name}\n\n"
        f"STRICT RULES:\n"
        f"- Maximum 10 words (emoji do not count as words)\n"
        f"- Include EXACTLY 2 emoji — one at the very start of the headline, one near the end\n"
        f"- ONLY use emoji from this list (single characters only): {safe_list}\n"
        f"- Absolutely NO flag emoji, NO emoji with variation selectors, NO joined emoji\n"
        f"- Keep the core meaning of the original title\n"
        f"- Title Case (not ALL CAPS)\n"
        f"- Output ONLY the headline text. No quotes, no explanation."
    )

    raw = None
    try:
        raw = generate_with_groq(ai_prompt).strip().strip('"\'')
    except Exception:
        try:
            raw = generate_text_with_gemini(ai_prompt).strip().strip('"\'')
        except Exception:
            pass

    # Validate and enforce exactly 2 safe emoji
    if raw:
        # Strip any flag emoji or unsafe sequences the AI might have slipped in
        segs    = _split_grapheme_clusters(raw)
        cleaned = ""
        for seg_type, seg_text in segs:
            if seg_type == "emoji":
                # Only keep if it's a single pure codepoint in our safe list
                if seg_text in PURE_SAFE_EMOJI:
                    cleaned += seg_text
                # else: drop it (flag, ZWJ sequence, etc.)
            else:
                cleaned += seg_text
        cleaned = cleaned.strip()

        n_emoji = count_emoji_in(cleaned)
        title_lower = title.lower()

        if n_emoji == 2:
            return cleaned  # Perfect

        elif n_emoji == 0:
            # Prepend + append
            e1 = pick_emoji_for(title_lower)
            e2 = pick_emoji_for(title_lower, exclude=e1)
            return f"{e1} {cleaned} {e2}"

        elif n_emoji == 1:
            # Find which end is missing emoji and add one
            e1 = pick_emoji_for(title_lower)
            if not is_pure_emoji_token(cleaned.split()[0]):
                return f"{e1} {cleaned}"
            else:
                e2 = pick_emoji_for(title_lower, exclude=e1)
                return f"{cleaned} {e2}"

        else:
            # Too many emoji — strip all and re-add 2
            text_only = "".join(s for t, s in _split_grapheme_clusters(cleaned) if t == "text").strip()
            e1 = pick_emoji_for(title_lower)
            e2 = pick_emoji_for(title_lower, exclude=e1)
            return f"{e1} {text_only} {e2}"

    # Hard fallback — title unchanged + 2 emoji
    title_lower = title.lower()
    e1 = pick_emoji_for(title_lower)
    e2 = pick_emoji_for(title_lower, exclude=e1)
    return f"{e1} {title} {e2}"


def generate_post_content(title: str, summary: str, source_url: str,
                          country_name: str = "GCC", flag: str = "🌍") -> str:
    """
    Generate LinkedIn caption in the MENA Startup Digest style:
    - 2-line hook starting with the correct country flag
    - 5-6 bullet points with contextual emoji
    - 2-3 conclusion lines
    - Source link
    - Hashtags
    Max 15 lines total.
    """
    prompt = (
        f"Write a LinkedIn post for an Arab startup ecosystem page.\n"
        f"Article title: {title}\n"
        f"Summary: {summary}\n"
        f"Country focus: {country_name}\n\n"
        f"Follow this EXACT structure (max 15 lines total including blank lines):\n\n"
        f"LINE 1: {flag} [attention-grabbing one-liner about the news — no other flag emoji]\n"
        f"LINE 2: [one sentence of essential context]\n"
        f"LINE 3: [blank]\n"
        f"LINE 4: Here is what this means:\n"
        f"LINE 5: ✅ [bullet — one short punchy line]\n"
        f"LINE 6: 💡 [bullet — one short punchy line]\n"
        f"LINE 7: 🚀 [bullet — one short punchy line]\n"
        f"LINE 8: 💰 [bullet — one short punchy line]\n"
        f"LINE 9: 🎯 [bullet — one short punchy line — optional 5th bullet]\n"
        f"LINE 10: [blank]\n"
        f"LINE 11: [conclusion sentence 1 with 1 emoji]\n"
        f"LINE 12: [conclusion sentence 2]\n"
        f"LINE 13: [blank]\n"
        f"LINE 14: → Read more: {source_url}\n"
        f"LINE 15: #{country_name.lower().replace(' ', '')} [4-6 more relevant hashtags on same line]\n\n"
        f"RULES:\n"
        f"- NO markdown, NO asterisks, NO bold\n"
        f"- Plain text only — emojis are encouraged\n"
        f"- CRITICAL: Line 1 MUST start with exactly {flag} and no other flag emoji\n"
        f"- Write ONLY about the article — no invented facts\n"
        f"- Each bullet point is ONE line only, do not wrap"
    )
    print("🚀 Generating post content…")
    try:
        text = generate_with_groq(prompt)
    except Exception as e:
        print(f"⚠️  Groq failed: {e}. Falling back to Gemini…")
        try:
            text = generate_text_with_gemini(prompt)
        except Exception as e2:
            print(f"❌ Both AI engines failed: {e2}")
            sys.exit(1)

    # Enforce correct flag on line 1 regardless of what AI returned
    lines = text.splitlines()
    if lines:
        first = lines[0].lstrip()
        # Strip any leading emoji/flag the AI put and prepend the correct one
        segs = _split_grapheme_clusters(first)
        text_part = "".join(s for t, s in segs if t == "text").lstrip()
        lines[0] = f"{flag} {text_part}"
        text = "\n".join(lines)

    return text


# ── MAIN ──────────────────────────────────────────────────────────────────────

def main():
    ensure_noto_emoji()

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

    # 2. Generate LinkedIn caption (correct flag enforced programmatically)
    post_text = generate_post_content(
        db_title or summary or country_name,
        summary  or "",
        source_url or "",
        country_name=country_name,
        flag=flag,
    )

    # 3. Build image headline with exactly 2 safe emoji (deterministically enforced)
    print("✍️  Building image headline…")
    _headline_base = (db_title or "").strip() or post_text.splitlines()[0][:100]
    image_headline = build_image_headline(_headline_base, country_name)
    print(f"  Headline: {image_headline}")

    # 4. Get background: og:image → AI → gradient
    bg_bytes, bg_source = get_background_image(
        source_url, db_title or "", summary or "", country_name
    )
    print(f"🖼  Background: {bg_source}")

    # 5. Fetch logo from GitHub repo
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

    # 8. Send to Make.com webhook
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
