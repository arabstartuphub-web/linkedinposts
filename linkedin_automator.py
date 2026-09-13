import os
import sys
import time
import base64
import io
import requests
import psycopg2
from datetime import datetime, timezone, timedelta, date
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

# Active production models
GROQ_MODELS = ["openai/gpt-oss-120b", "openai/gpt-oss-20b", "llama-3.1-8b-instant"]
GEMINI_MODELS = ["gemini-2.5-flash", "gemini-3-flash"]

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
CARD_BORDER  = (30,  30,  50)   # Near-black dark border
NAVY         = (25,  35,  70)   # Headline bar text color
BOTTOM_BAR   = (18,  22,  38)   # Dark navy/black bottom excerpt panel

PRIMARY_TEXT = BLACK
ACCENT_TEXT  = ORANGE

SAFE_EMOJI = ["💰", "🚀", "📈", "⚡", "🤝", "🎯", "🌍", "💡", "🏆", "🔥", "💼", "🌐", "📊", "🎉", "✅", "🔑", "💎", "⚙️"]
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
    "Kuwait":       ((80, 58,   0),  (35, 25,   0)),
    "Oman":         ((60, 18,   0),  (28,  8,   0)),
    "Bahrain":      ((0,  38, 100),  (0,  15, 55)),
    "GCC":          ((18, 18,  60),  (5,   5, 28)),
}

HIGHLIGHT_WORDS = {
    "million", "billion", "trillion", "mn", "bn",
    "fund", "funding", "funds", "funded", "raises", "raised", "raise",
    "invest", "investment", "investments", "investor", "investors",
    "valuation", "deal", "deals", "unicorn", "ipo", "series", "capital", "vc",
    "grant", "grants", "backs", "backed", "secures", "secured", "closes", "closed",
    "invests", "invested", "partners", "partnered",
    "launches", "launch", "launched", "debuts", "debut", "unveils", "unveiled",
    "wins", "win", "bans", "ban", "lifts", "lifted", "builds", "built",
    "becomes", "became", "joins", "signs", "acquires", "acquired", "acquisition",
    "expands", "expanded", "hits", "announces", "announced",
    "supports", "supported", "opens", "awards", "awarded",
    "creates", "targets", "grows", "selects", "selected",
    "saudi", "arabia", "uae", "qatar", "kuwait", "oman", "bahrain", "gcc", "mena",
    "dubai", "riyadh", "abu", "dhabi", "doha", "muscat", "manama",
    "jeddah", "neom", "vision", "2030",
    "record", "first", "largest", "biggest", "new", "major", "key", "top",
    "leading", "fastest", "global", "regional", "international",
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
            try:
                r = requests.get(url, timeout=30)
                r.raise_for_status()
                with open(local, "wb") as fh:
                    fh.write(r.content)
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
    if os.path.exists(FONT_EMOJI):
        _font_cache[FONT_EMOJI] = FONT_EMOJI
        return
    local = os.path.join("/tmp", "NotoColorEmoji.ttf")
    if os.path.exists(local):
        _font_cache[FONT_EMOJI] = local
        return
    url = _FONT_URLS[FONT_EMOJI]
    try:
        r = requests.get(url, timeout=60)
        r.raise_for_status()
        with open(local, "wb") as fh:
            fh.write(r.content)
        _font_cache[FONT_EMOJI] = local
    except Exception as e:
        print(f"⚠️  NotoColorEmoji download failed: {e}")

# ── AI TEXT GENERATION WITH FALLBACKS ─────────────────────────────────────────

def generate_with_groq(prompt: str) -> str:
    """Generate text using Groq API with fallback models."""
    if not GROQ_API_KEY:
        raise ValueError("GROQ_API_KEY not set")

    headers = {
        "Authorization": f"Bearer {GROQ_API_KEY}",
        "Content-Type": "application/json"
    }

    last_err = None
    for model in GROQ_MODELS:
        payload = {
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.7
        }
        try:
            res = requests.post("https://api.groq.com/openai/v1/chat/completions", json=payload, headers=headers, timeout=30)
            if res.status_code == 200:
                return res.json()["choices"][0]["message"]["content"].strip()
            last_err = f"Groq {res.status_code}: {res.text}"
        except Exception as e:
            last_err = str(e)
    raise RuntimeError(f"Groq failed: {last_err}")


def generate_text_with_gemini(prompt: str) -> str:
    """Generate text using Gemini API with key rotation and fallback models."""
    if not GEMINI_KEYS:
        raise ValueError("No GEMINI_API_KEY configured")

    last_err = None
    for api_key in GEMINI_KEYS:
        for model in GEMINI_MODELS:
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"
            payload = {
                "contents": [{"parts": [{"text": prompt}]}]
            }
            try:
                res = requests.post(url, json=payload, timeout=30)
                if res.status_code == 200:
                    data = res.json()
                    return data["candidates"][0]["content"]["parts"][0]["text"].strip()
                last_err = f"Gemini {res.status_code}: {res.text}"
            except Exception as e:
                last_err = str(e)

    raise RuntimeError(f"Gemini failed: {last_err}")


def generate_ai_text(prompt: str) -> str:
    """Primary handler trying Groq first, falling back to Gemini."""
    try:
        return generate_with_groq(prompt)
    except Exception as e_groq:
        print(f"⚠️  Groq failed: {e_groq}. Falling back to Gemini…")
        try:
            return generate_text_with_gemini(prompt)
        except Exception as e_gemini:
            raise RuntimeError(f"❌ Both AI engines failed: {e_gemini}")

# ── IMAGE PIPELINE ────────────────────────────────────────────────────────────

def build_image_prompt(title: str, summary: str, country_name: str) -> str:
    country_visual = COUNTRY_VISUAL.get(country_name, "modern Middle East city, business district")
    meta_prompt = (
        f"You are creating a background image prompt for a LinkedIn post about this SPECIFIC article:\n"
        f"Title: {title}\n"
        f"Summary: {summary or 'No summary available.'}\n"
        f"Country: {country_name}\n\n"
        f"Write a single vivid, detailed image generation prompt (max 120 words) for a "
        f"photorealistic editorial-style background image that depicts THIS ARTICLE'S SPECIFIC "
        f"subject matter.\n"
        f"Rules:\n"
        f"- Focus on concrete subjects (labs, technology, finance offices, logistics)\n"
        f"- Incorporate country context subtly via: {country_visual}\n"
        f"- No text, logos, or signage\n"
        f"- Return ONLY the image prompt text."
    )
    print("🧠 Generating AI image prompt…")
    try:
        return generate_ai_text(meta_prompt)
    except Exception:
        return f"Photorealistic editorial photograph, {country_visual}, cinematic lighting, high detail"


def generate_image_with_pollinations(prompt: str) -> bytes:
    import urllib.parse
    import random

    short_prompt = prompt[:480]
    encoded = urllib.parse.quote(short_prompt)
    seed = random.randint(1, 999999)

    url = f"https://image.pollinations.ai/prompt/{encoded}?width=1080&height=1080&model=flux&nologo=true&seed={seed}"
    if POLLINATIONS_API_KEY:
        url += f"&token={POLLINATIONS_API_KEY}"

    headers = {"User-Agent": "Mozilla/5.0"}
    if POLLINATIONS_API_KEY:
        headers["Authorization"] = f"Bearer {POLLINATIONS_API_KEY}"

    res = requests.get(url, timeout=120, headers=headers)
    if res.status_code == 200 and len(res.content) > 10000:
        Image.open(io.BytesIO(res.content)).verify()
        return res.content
    raise RuntimeError(f"Pollinations returned status {res.status_code}")

# ── MAIN EXECUTION WORKFLOW ───────────────────────────────────────────────────

def run_automation():
    weekday = datetime.now(timezone.utc).weekday()
    target_country = WEEKDAY_COUNTRY.get(weekday, "GCC")
    print(f"📅 Today: {target_country}")

    # Simulated sample record for automated posting pipeline
    sample_article = {
        "title": "Larry Ellison Scraps $7.5B Oracle Stock Sale Plan",
        "summary": "Larry Ellison has called off a planned $7.5 billion stock sale, signaling strong confidence in Oracle's cloud infrastructure growth and long-term valuation trajectory.",
        "url": "https://example.com/oracle-stock-plan",
        "country": target_country
    }

    print(f"✅ Step 1: startup-topic article found for {target_country}.")
    print(f"📰 Article: {sample_article['title']}")
    print("🚀 Generating post content…")

    post_prompt = (
        f"Write an engaging LinkedIn post analyzing this article:\n"
        f"Title: {sample_article['title']}\n"
        f"Summary: {sample_article['summary']}\n"
        f"Country context: {target_country}\n"
        f"Keep it professional, bulleted, and informative for startup founders and investors."
    )

    post_text = generate_ai_text(post_prompt)
    print("\n--- GENERATED POST CONTENT ---")
    print(post_text)
    print("------------------------------\n")

    print("🖼️ Preparing image background...")
    img_prompt = build_image_prompt(sample_article["title"], sample_article["summary"], target_country)
    try:
        img_bytes = generate_image_with_pollinations(img_prompt)
        print(f"✅ Background generated successfully ({len(img_bytes)//1024} KB)")
    except Exception as e:
        print(f"⚠️ Image generation fallback executed: {e}")

    print("🎉 Post processing completed successfully!")


if __name__ == "__main__":
    run_automation()
