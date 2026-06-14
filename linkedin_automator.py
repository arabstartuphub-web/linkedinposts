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

POLLINATIONS_API_KEY = os.environ.get("POLLINATIONS_API_KEY")

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

WHITE        = (255, 255, 255)
BLACK        = (15,  15,  15)
ORANGE       = (224, 82,  18)   # Smashi orange: vivid red-orange for accent words
CARD_BORDER  = (30,  30,  50)   # Near-black dark border (matches Smashi dark outline)
NAVY         = (25,  35,  70)   # Headline bar text color (news-card style)
BOTTOM_BAR   = (18,  22,  38)   # Dark navy/black bottom excerpt panel

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
    """Orange for impactful/financial/geo words, black for the rest.
    Works with ALL CAPS text — lowercases before comparing against HIGHLIGHT_WORDS."""
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
             start: int = 96, minimum: int = 48, max_lines: int = 5):
    """Find largest font size where text fits in max_w × max_h. Returns (font, lines, size, line_h)."""
    tokens = tokenize_headline(headline)
    for size in range(start, minimum - 1, -2):
        font   = get_font(FONT_BOLD, size)
        lines  = wrap_tokens(draw, tokens, font, max_w)
        line_h = int(size * 1.30)
        if len(lines) * line_h <= max_h and len(lines) <= max_lines:
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


def draw_text_line_left(draw, word_list: list, font, x: int, y: int, color: tuple):
    """
    Draw one line of tokens left-aligned in a single fixed color (no emoji handling,
    no per-word highlighting). Used for the headline bar and excerpt panel in the
    news-card layout.
    """
    sp_w = measure(draw, " ", font)[0]
    cx   = x
    for idx, token in enumerate(word_list):
        draw.text((cx, y), token, font=font, fill=color)
        cx += measure_token_width(draw, token, font)
        if idx < len(word_list) - 1:
            cx += sp_w


# ── AI IMAGE GENERATION ───────────────────────────────────────────────────────

def build_image_prompt(title: str, summary: str, country_name: str) -> str:
    country_visual = COUNTRY_VISUAL.get(country_name, "modern Middle East city, business district")
    meta_prompt = (
        f"You are creating a background image prompt for a LinkedIn post about this SPECIFIC article:\n"
        f"Title: {title}\n"
        f"Summary: {summary or 'No summary available.'}\n"
        f"Country: {country_name}\n\n"
        f"Write a single vivid, detailed image generation prompt (max 120 words) for a "
        f"photorealistic editorial-style background image that depicts THIS ARTICLE'S SPECIFIC "
        f"subject matter — not a generic country skyline.\n\n"
        f"Rules:\n"
        f"- First identify the concrete subject of the article (e.g. a named organization, "
        f"a sector such as health/longevity/fintech/AI, a building, an event, a product)\n"
        f"- The image MUST visually represent that specific subject — e.g. a longevity/health "
        f"article should show a modern clinic, lab, or wellness setting, not just a skyline\n"
        f"- Incorporate this country's setting subtly via: {country_visual}\n"
        f"- No text, no logos, no overlays, no watermarks, no readable signage in the image\n"
        f"- Cinematic lighting, sharp focus, high detail, professional editorial photography\n"
        f"- Style: wide establishing shot or dramatic close-up relevant to the subject\n"
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


def generate_image_with_pollinations(prompt: str) -> bytes:
    """
    Pollinations.AI — completely free, no API key, no signup, no quota.
    Uses FLUX.1 under the hood. Returns raw image bytes (JPEG/PNG).

    Endpoint: GET https://image.pollinations.ai/prompt/{url-encoded-prompt}
    Params:
      width=1080, height=1080  → square 1:1 for LinkedIn
      model=flux               → FLUX.1 (best quality on Pollinations)
      nologo=true              → strip the Pollinations watermark
      seed=<random>            → reproducible but varied results
    """
    import urllib.parse
    import random

    # Keep prompt under 500 chars — very long prompts get truncated by the service
    short_prompt = prompt[:480]
    encoded      = urllib.parse.quote(short_prompt)
    seed         = random.randint(1, 999999)

    url = (
        f"https://image.pollinations.ai/prompt/{encoded}"
        f"?width=1080&height=1080&model=flux&nologo=true&seed={seed}"
    )
    if POLLINATIONS_API_KEY:
        url += f"&token={POLLINATIONS_API_KEY}"
    print(f"🌸 Trying Pollinations.AI (FLUX.1)…")
    headers = {"User-Agent": "Mozilla/5.0"}
    if POLLINATIONS_API_KEY:
        headers["Authorization"] = f"Bearer {POLLINATIONS_API_KEY}"
    try:
        r = requests.get(url, timeout=120, headers=headers)
        if r.status_code == 200 and len(r.content) > 10000:
            # Validate it's actually an image
            Image.open(io.BytesIO(r.content)).verify()
            print(f"✅ Pollinations.AI image generated ({len(r.content)//1024}KB)")
            return r.content
        raise RuntimeError(f"Pollinations returned {r.status_code}, {len(r.content)} bytes")
    except Exception as e:
        raise RuntimeError(f"Pollinations.AI failed: {e}")


# ── BACKGROUND ────────────────────────────────────────────────────────────────

def is_image_too_light(img: Image.Image,
                       threshold: int = 210,
                       light_fraction: float = 0.80) -> bool:
    """
    Returns True only if the image is extremely washed-out (>80% near-white pixels).
    We apply a heavy dark overlay in generate_branded_image anyway, so most images
    are fine — only truly all-white/blank images get rejected.
    """
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
    print(f"  Brightness ratio: {ratio:.2f} ({'rejected — too light' if ratio > light_fraction else 'OK — accepted'})")
    return ratio > light_fraction


def fetch_image_bytes_from_url(url: str):
    """Download raw image bytes from a direct image URL (e.g. articles.image_url
    already resolved by the website's scraper). Returns None on any failure —
    caller should fall back to the next source in the pipeline."""
    if not url or not url.startswith("http"):
        return None
    HEADERS = {
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        ),
    }
    try:
        res = requests.get(url, timeout=15, headers=HEADERS, allow_redirects=True)
        if res.status_code == 200 and len(res.content) > 5000:
            return res.content
        print(f"⚠️  DB image_url fetch failed or too small: {res.status_code}, {len(res.content)} bytes")
    except Exception as e:
        print(f"⚠️  DB image_url fetch error: {e}")
    return None


def fetch_og_image_bytes(url: str):
    if not url:
        return None
    # Realistic browser headers to avoid 403 blocks from news sites
    HEADERS = {
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "gzip, deflate, br",
        "Cache-Control": "no-cache",
        "Pragma": "no-cache",
    }
    try:
        res = requests.get(url, timeout=15, headers=HEADERS, allow_redirects=True)
        if res.status_code != 200:
            print(f"⚠️  Article page returned {res.status_code}")
            return None

        class OGParser(HTMLParser):
            def __init__(self):
                super().__init__()
                self.og_image = None
            def handle_starttag(self, tag, attrs):
                if self.og_image:
                    return
                if tag == "meta":
                    d = dict(attrs)
                    # Accept both og:image and twitter:image
                    prop = d.get("property") or d.get("name") or ""
                    if prop in ("og:image", "twitter:image", "twitter:image:src"):
                        val = d.get("content") or d.get("value")
                        if val:
                            self.og_image = val

        parser = OGParser()
        parser.feed(res.text)
        if not parser.og_image:
            print("⚠️  No og:image or twitter:image meta tag found.")
            return None

        og_url = parser.og_image
        # Handle protocol-relative URLs
        if og_url.startswith("//"):
            og_url = "https:" + og_url

        print(f"✅ og:image found: {og_url}")
        img_res = requests.get(og_url, timeout=15, headers=HEADERS, allow_redirects=True)
        if img_res.status_code == 200 and len(img_res.content) > 5000:
            return img_res.content
        print(f"⚠️  og:image download failed or too small: {img_res.status_code}, {len(img_res.content)} bytes")
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


def _darken_image_for_editorial(img: Image.Image) -> Image.Image:
    """Apply a cinematic dark overlay to make any light image work as a background."""
    overlay = Image.new("RGBA", img.size, (0, 0, 0, 80))  # 31% dark tint
    return Image.alpha_composite(img.convert("RGBA"), overlay).convert("RGB")


def fetch_country_repo_image(country_name: str) -> bytes:
    """
    Fetch the pre-stored country background image from the GitHub repo.
    Expected filenames (in repo root): KSA.jpg, UAE.jpg, QATAR.jpg,
    KUWAIT.jpg, OMAN.jpg, BAHRAIN.jpg, GCC.jpg
    Uses the country code from COUNTRY_MAP to build the filename.
    """
    code = COUNTRY_MAP.get(country_name, {}).get("code", country_name[:3].upper())
    filename = f"{code}.jpg"
    url = f"{GITHUB_BASE}{filename}"
    print(f"🗂  Fetching country repo image: {filename}…")
    try:
        r = requests.get(url, timeout=15, headers={"User-Agent": "Mozilla/5.0"})
        if r.status_code == 200 and len(r.content) > 5000:
            # Validate it's a real image
            Image.open(io.BytesIO(r.content)).verify()
            print(f"✅ Country repo image fetched: {filename} ({len(r.content)//1024}KB)")
            return r.content
        raise RuntimeError(f"Repo image {filename} returned {r.status_code}, {len(r.content)} bytes")
    except Exception as e:
        raise RuntimeError(f"Country repo image fetch failed: {e}")


def get_background_image(source_url: str, title: str, summary: str, country_name: str, db_image_url: str = ""):
    """
    Background image pipeline:
      0. image_url already stored on the article by the website's scraper
         (its own og:image / feed-image / in-article-image fallback chain —
         reuse it directly instead of re-fetching).
      1. og:image from the article itself (primary fallback — always topically
         relevant, it IS the article's photo). Dark editorial overlay applied.
      2. Pollinations.AI (FLUX.1) — free, no key, no quota. Prompt is built from
         the article's actual title/summary so it stays on-topic (backup).
      3. Country repo image (KSA.jpg / UAE.jpg / etc. from GitHub repo root)
      4. Gradient — absolute last resort
    """
    # Step 0: image already resolved by the website's scraper — top priority
    if db_image_url:
        db_bytes = fetch_image_bytes_from_url(db_image_url)
        if db_bytes:
            try:
                db_img = Image.open(io.BytesIO(db_bytes)).convert("RGB")
                if is_image_too_light(db_img):
                    db_img = _darken_image_for_editorial(db_img)
                buf = io.BytesIO()
                db_img.save(buf, "JPEG", quality=92)
                print("✅ Using website DB image_url as background.")
                return buf.getvalue(), "db_image_url"
            except Exception as e:
                print(f"⚠️  DB image_url decode failed: {e}")

    # Step 1: og:image from the article — fallback if DB had no image, still on-topic
    og_bytes = fetch_og_image_bytes(source_url)
    if og_bytes:
        try:
            og_img = Image.open(io.BytesIO(og_bytes)).convert("RGB")
            if is_image_too_light(og_img):
                og_img = _darken_image_for_editorial(og_img)
            buf = io.BytesIO()
            og_img.save(buf, "JPEG", quality=92)
            print("✅ Using article og:image as background.")
            return buf.getvalue(), "og_image"
        except Exception as e:
            print(f"⚠️  og:image decode failed: {e}")

    # Step 2: Pollinations.AI — built from this article's specific subject
    print("🌸 No og:image — generating AI background from article content…")
    try:
        prompt = build_image_prompt(title, summary, country_name)
        print(f"📝 Image prompt (preview): {prompt[:100]}…")
        poll_bytes = generate_image_with_pollinations(prompt)
        if poll_bytes:
            try:
                poll_img = Image.open(io.BytesIO(poll_bytes)).convert("RGB")
                if is_image_too_light(poll_img):
                    print("⚠️  Pollinations image very light — applying dark overlay.")
                    poll_img = _darken_image_for_editorial(poll_img)
                    buf = io.BytesIO()
                    poll_img.save(buf, "JPEG", quality=92)
                    poll_bytes = buf.getvalue()
            except Exception:
                pass
            return poll_bytes, "pollinations_ai"
    except Exception as e:
        print(f"⚠️  Pollinations.AI failed: {e}")

    # Step 3: Country repo image (KSA.jpg / UAE.jpg / etc.)
    print(f"🗂  Trying country repo image for {country_name}…")
    try:
        repo_bytes = fetch_country_repo_image(country_name)
        if repo_bytes:
            # No dark overlay needed — these images are pre-designed for this purpose
            return repo_bytes, "country_repo_image"
    except Exception as e:
        print(f"⚠️  Country repo image failed: {e}")

    # Step 4: everything failed — gradient
    print("ℹ️  All image sources exhausted — using country gradient.")
    return None, "gradient"


# ── BRANDED IMAGE COMPOSER ────────────────────────────────────────────────────

def generate_branded_image(bg_bytes, headline: str, country_name: str,
                           logo_bytes=None, bg_source: str = "",
                           excerpt: str = "") -> Image.Image:
    """
    Compose final 1080×1080 branded image in news-card style:
      - Top bar (white): navy/black headline, sentence case, left-aligned
      - Middle: clean full-width photo (og image / AI image / country gradient)
      - Bottom bar (dark navy): white excerpt text, left-aligned
      - Country flag pill + logo in the top bar
    """
    PAD_X = 50

    # ── TOP HEADLINE BAR ─────────────────────────────────────────────────────
    TOP_BAR_MAX_H = int(IMG_H * 0.30)
    TOP_PAD_TOP   = 36
    TOP_PAD_BOT   = 28
    HEADLINE_W    = IMG_W - 2 * PAD_X

    base = Image.new("RGB", (IMG_W, IMG_H), WHITE)
    draw = ImageDraw.Draw(base)

    headline_clean = "".join(
        s for t, s in _split_grapheme_clusters(headline.strip()) if t == "text"
    ).strip()

    font, lines, fsize, line_h = auto_fit(
        draw, headline_clean, HEADLINE_W, TOP_BAR_MAX_H - TOP_PAD_TOP - TOP_PAD_BOT,
        start=72, minimum=40, max_lines=3
    )
    print(f"  📝 Headline font: {fsize}px  Lines: {len(lines)}")

    text_block_h = len(lines) * line_h
    top_bar_h    = TOP_PAD_TOP + text_block_h + TOP_PAD_BOT

    ty = TOP_PAD_TOP
    for word_list in lines:
        draw_text_line_left(draw, word_list, font, PAD_X, ty, NAVY)
        ty += line_h

    # ── BOTTOM EXCERPT BAR ───────────────────────────────────────────────────
    BOTTOM_BAR_MAX_H = int(IMG_H * 0.28)
    BOT_PAD_TOP      = 34
    BOT_PAD_BOT      = 42
    EXCERPT_W        = IMG_W - 2 * PAD_X

    excerpt_clean = "".join(
        s for t, s in _split_grapheme_clusters((excerpt or "").strip()) if t == "text"
    ).strip()

    if excerpt_clean:
        e_font, e_lines, e_fsize, e_line_h = auto_fit(
            draw, excerpt_clean, EXCERPT_W, BOTTOM_BAR_MAX_H - BOT_PAD_TOP - BOT_PAD_BOT,
            start=40, minimum=26, max_lines=4
        )
        print(f"  📝 Excerpt font: {e_fsize}px  Lines: {len(e_lines)}")
        e_text_block_h = len(e_lines) * e_line_h
        bottom_bar_h   = BOT_PAD_TOP + e_text_block_h + BOT_PAD_BOT
    else:
        e_lines, e_line_h, e_font = [], 0, None
        bottom_bar_h = int(IMG_H * 0.12)

    # ── MIDDLE PHOTO SECTION ─────────────────────────────────────────────────
    photo_h = IMG_H - top_bar_h - bottom_bar_h
    photo_h = max(photo_h, int(IMG_H * 0.30))  # ensure photo never collapses

    src = prepare_background(bg_bytes, country_name)  # 1080x1080 square
    # Crop a horizontal band matching photo_h from the vertical center
    crop_top = max(0, (IMG_H - photo_h) // 2)
    photo    = src.crop((0, crop_top, IMG_W, crop_top + photo_h))
    base.paste(photo, (0, top_bar_h))

    # Recompute bottom bar position (in case photo_h was clamped)
    bottom_bar_y = top_bar_h + photo_h
    bottom_bar_h = IMG_H - bottom_bar_y

    draw = ImageDraw.Draw(base)
    draw.rectangle([0, bottom_bar_y, IMG_W, IMG_H], fill=BOTTOM_BAR)

    ey = bottom_bar_y + BOT_PAD_TOP
    for word_list in e_lines:
        draw_text_line_left(draw, word_list, e_font, PAD_X, ey, WHITE)
        ey += e_line_h

    # ── COUNTRY FLAG — small pill on the photo, top-left ────────────────────
    # Skip entirely when the background is the country repo image, since
    # that image already has its own flag/branding baked in.
    skip_flag_overlay = (bg_source == "country_repo_image")

    flag_str = COUNTRY_MAP.get(country_name, {}).get("flag", "")
    TARGET_FLAG = 64
    NOTO_SIZE   = 109
    FLAG_X, FLAG_Y = PAD_X - 18, top_bar_h + 18

    flag_rendered = False
    if flag_str and not skip_flag_overlay:
        emoji_font_path = _ensure_font(FONT_EMOJI)
        if os.path.exists(emoji_font_path):
            try:
                flag_font  = ImageFont.truetype(emoji_font_path, NOTO_SIZE)
                patch_dim  = NOTO_SIZE * 3
                flag_patch = Image.new("RGBA", (patch_dim, patch_dim), (0, 0, 0, 0))
                fpd = ImageDraw.Draw(flag_patch)
                fpd.text((0, 0), flag_str, font=flag_font, embedded_color=True)
                bb  = fpd.textbbox((0, 0), flag_str, font=flag_font)
                fw  = max(bb[2] - bb[0], 1)
                fh  = max(bb[3] - bb[1], 1)
                flag_patch = flag_patch.crop((0, 0, min(fw + 4, patch_dim), min(fh + 4, patch_dim)))
                scale      = TARGET_FLAG / max(flag_patch.width, flag_patch.height)
                new_w      = max(1, int(flag_patch.width  * scale))
                new_h      = max(1, int(flag_patch.height * scale))
                flag_patch = flag_patch.resize((new_w, new_h), Image.LANCZOS)
                base.paste(flag_patch, (FLAG_X, FLAG_Y), flag_patch)
                flag_rendered = True
                print(f"  🏳  Flag rendered at {new_w}×{new_h}px")
            except Exception as e:
                print(f"⚠️  Flag render failed: {e}")

    if not flag_rendered and not skip_flag_overlay:
        draw = ImageDraw.Draw(base)
        country_code = COUNTRY_MAP.get(country_name, {}).get("code", country_name[:3].upper())
        pill_font = get_font(FONT_BOLD, 24)
        cw, ch    = measure(draw, country_code, pill_font)
        pill_x, pill_y = FLAG_X, FLAG_Y
        pill_w, pill_h = cw + 28, ch + 16
        draw.rounded_rectangle([pill_x, pill_y, pill_x + pill_w, pill_y + pill_h],
                                radius=10, fill=(20, 20, 20))
        draw.text((pill_x + 14, pill_y + 8), country_code, font=pill_font, fill=WHITE)

    # ── LOGO — top-right corner of the photo ────────────────────────────────
    LOGO_SIZE   = 64
    LOGO_MARGIN = 18
    if logo_bytes:
        try:
            logo   = Image.open(io.BytesIO(logo_bytes)).convert("RGBA")
            logo   = logo.resize((LOGO_SIZE, LOGO_SIZE), Image.LANCZOS)
            logo_x = IMG_W - LOGO_MARGIN - LOGO_SIZE
            logo_y = top_bar_h + LOGO_MARGIN
            base.paste(logo, (logo_x, logo_y), logo)
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
# Schema contract — columns referenced below must stay in sync with:
#   arabstartuphub-web/website → lib/db/src/schema/articles.ts (Drizzle)
#
# Columns used by this script:
#   id              INTEGER   primary key
#   title           TEXT
#   summary         TEXT
#   source_url      TEXT
#   image_url       TEXT      populated by the website's 4-tier scraper
#   published_at    TIMESTAMPTZ
#   country         TEXT      values: 'Saudi Arabia' | 'UAE' | 'Qatar' |
#                             'Kuwait' | 'Oman' | 'Bahrain' | 'GCC'
#   linkedin_posted BOOLEAN   default FALSE
#
# ⚠️  If any column is renamed in the Drizzle schema, update the queries
#     below (bare psycopg2 — no type safety, breaks silently at runtime).

def get_country_for_today() -> str:
    return WEEKDAY_COUNTRY.get(datetime.now(timezone.utc).weekday(), "GCC")


def get_daily_article(country_name: str):
    conn = psycopg2.connect(DB_URL)
    try:
        with conn.cursor() as cur:
            # ── Log per-country article inventory so we can see if rotation is decorative ──
            cur.execute(
                """
                SELECT country, COUNT(*) AS unposted
                FROM   articles
                WHERE  linkedin_posted = FALSE
                GROUP  BY country
                ORDER  BY unposted DESC;
                """
            )
            counts = cur.fetchall()
            if counts:
                summary_str = ", ".join(f"{c}: {n}" for c, n in counts)
                print(f"  📊 Unposted article inventory — {summary_str}")
            else:
                print("  📊 Unposted article inventory — all empty")

            cur.execute(
                """
                SELECT id, title, summary, source_url, image_url
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
                    SELECT id, title, summary, source_url, image_url
                    FROM   articles
                    WHERE  linkedin_posted = FALSE AND country = 'GCC'
                    ORDER  BY published_at DESC
                    LIMIT  1;
                    """
                )
                row = cur.fetchone()
            return row
    finally:
        conn.close()


def mark_article_posted(article_id: int):
    conn = psycopg2.connect(DB_URL)
    try:
        with conn.cursor() as cur:
            cur.execute("UPDATE articles SET linkedin_posted = TRUE WHERE id = %s;", (article_id,))
        conn.commit()
        print(f"✅ Article {article_id} marked as posted.")
    finally:
        conn.close()


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
    # max_tokens scoped per call-site via a sentinel in the prompt prefix;
    # default 500 covers the LinkedIn post (≤15 lines). Headline/excerpt
    # callers pass shorter prompts but the model honours the cap regardless.
    _max_tokens = 150 if prompt.startswith("Rewrite this article title") or \
                         prompt.startswith("Write a 1-2 sentence summary") else 500
    payload = {
        "model":       "llama-3.3-70b-versatile",
        "messages":    [{"role": "user", "content": prompt}],
        "temperature": 0.7,
        "max_tokens":  _max_tokens,
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
    - Sentence case (like a news chyron), no emoji
    - Short, punchy, max 16 words
    - Keeps key names/figures/orgs from the original title
    """
    ai_prompt = (
        f"Rewrite this article title as a short punchy news headline for an image card.\n"
        f"Original title: {title}\n"
        f"Country: {country_name}\n\n"
        f"STRICT RULES:\n"
        f"- Maximum 16 words\n"
        f"- NO emoji whatsoever\n"
        f"- Sentence case (capitalize only the first word and proper nouns — NOT all caps)\n"
        f"- Keep dollar/number amounts exactly as they appear (e.g. $15 billion)\n"
        f"- Keep the core meaning and ALL key names, people, and organizations from the "
        f"original title — do not generalize a specific name into a generic term\n"
        f"- Output ONLY the headline text. No quotes, no explanation."
    )

    raw = None
    try:
        raw = generate_with_groq(ai_prompt).strip().strip('"\'')
        print("  [headline] Provider: Groq ✅")
    except Exception as e:
        print(f"  [headline] Groq failed: {e} — falling back to Gemini…")
        try:
            raw = generate_text_with_gemini(ai_prompt).strip().strip('"\'')
            print("  [headline] Provider: Gemini ✅")
        except Exception as e2:
            print(f"  [headline] ❌ Gemini also failed: {e2} — using hard fallback (raw title).")

    if raw:
        # Strip any emoji the AI may have slipped in
        segs    = _split_grapheme_clusters(raw)
        cleaned = "".join(s for t, s in segs if t == "text").strip()
        if cleaned:
            return cleaned

    # Hard fallback — use title as-is
    print("  [headline] ⚠️  Using raw title as fallback.")
    return title.strip()


def build_image_excerpt(title: str, summary: str, country_name: str) -> str:
    """
    Build a short 1-2 sentence excerpt for the bottom text panel of the image card.
    Plain sentence case, no emoji, no hashtags — summarizes the article's key takeaway.
    """
    ai_prompt = (
        f"Write a 1-2 sentence summary of this article for an image caption panel.\n"
        f"Title: {title}\n"
        f"Summary: {summary or 'No summary available.'}\n"
        f"Country: {country_name}\n\n"
        f"STRICT RULES:\n"
        f"- Maximum 25 words total\n"
        f"- NO emoji, NO hashtags, NO markdown\n"
        f"- Sentence case, plain prose\n"
        f"- Summarize the key takeaway or context of the article\n"
        f"- Output ONLY the summary text. No quotes, no explanation."
    )

    raw = None
    try:
        raw = generate_with_groq(ai_prompt).strip().strip('"\'')
        print("  [excerpt] Provider: Groq ✅")
    except Exception as e:
        print(f"  [excerpt] Groq failed: {e} — falling back to Gemini…")
        try:
            raw = generate_text_with_gemini(ai_prompt).strip().strip('"\'')
            print("  [excerpt] Provider: Gemini ✅")
        except Exception as e2:
            print(f"  [excerpt] ❌ Gemini also failed: {e2} — using hard fallback (summary/title).")

    if raw:
        segs    = _split_grapheme_clusters(raw)
        cleaned = "".join(s for t, s in segs if t == "text").strip()
        if cleaned:
            return cleaned

    # Hard fallback — use summary or title
    print("  [excerpt] ⚠️  Using summary/title as fallback.")
    return (summary or title).strip()


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
    db_image_url = ""
    if not article:
        print(f"No DB articles for {country_name}. Fetching from NewsAPI…")
        db_title, summary, source_url = fetch_live_article(country_name)
        if not db_title:
            print("NewsAPI returned nothing. Exiting.")
            sys.exit(0)
    else:
        article_id, db_title, summary, source_url, db_image_url = article

    print(f"📰 Article: {db_title}")

    # 2. Generate LinkedIn caption (correct flag enforced programmatically)
    post_text = generate_post_content(
        db_title or summary or country_name,
        summary  or "",
        source_url or "",
        country_name=country_name,
        flag=flag,
    )

    # 3. Build image headline (sentence case, deterministically enforced)
    print("✍️  Building image headline…")
    _headline_base = (db_title or "").strip() or post_text.splitlines()[0][:100]
    image_headline = build_image_headline(_headline_base, country_name)
    print(f"  Headline: {image_headline}")

    # 3b. Build short excerpt for the bottom panel
    print("✍️  Building image excerpt…")
    image_excerpt = build_image_excerpt(_headline_base, summary or "", country_name)
    print(f"  Excerpt: {image_excerpt}")

    # 4. Get background: DB image_url → og:image → AI → country repo → gradient
    bg_bytes, bg_source = get_background_image(
        source_url, db_title or "", summary or "", country_name, db_image_url
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
    branded_img = generate_branded_image(
        bg_bytes, image_headline, country_name, logo_bytes, bg_source, image_excerpt
    )

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
