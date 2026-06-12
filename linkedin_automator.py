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
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
NEWS_API_KEY   = os.environ.get("NEWS_API_KEY")
WEBHOOK_URL    = os.environ.get("MAKE_WEBHOOK_URL")
GITHUB_TOKEN   = os.environ.get("GITHUB_TOKEN")
GITHUB_REPO    = "arabstartuphub-web/linkedinposts"
GITHUB_BRANCH  = "main"
GITHUB_BASE    = f"https://raw.githubusercontent.com/{GITHUB_REPO}/{GITHUB_BRANCH}/"

# ── IMAGE DESIGN ─────────────────────────────────────────────────────────────
IMG_W, IMG_H = 1080, 1080   # Square (LinkedIn best practice)

FONT_BOLD   = "/usr/share/fonts/truetype/google-fonts/Poppins-Bold.ttf"
FONT_MEDIUM = "/usr/share/fonts/truetype/google-fonts/Poppins-Medium.ttf"

WHITE     = (255, 255, 255)
BLACK     = (15,  15,  15)
ORANGE    = (224, 82,  18)   # Smashi-style highlight
BLUE_LINE = (25,  100, 220)  # Card bottom accent

# Per-country gradient background (used when no OG photo is available)
COUNTRY_GRADIENTS = {
    "Saudi Arabia": ((0,  80,  40),  (0,  30, 15)),
    "UAE":          ((0,  55, 110),  (0,  20, 60)),
    "Qatar":        ((75,  0,  40),  (35,  0, 18)),
    "Kuwait":       ((80, 58,   0),  (35, 25,  0)),
    "Oman":         ((60, 18,   0),  (28,  8,  0)),
    "Bahrain":      ((0,  38, 100),  (0,  15, 55)),
    "GCC":          ((18, 18,  60),  (5,   5, 28)),
}

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

# Words that get highlighted in orange
HIGHLIGHT_WORDS = {
    # money
    "million","billion","trillion","fund","funding","raises","raised",
    "invest","investment","valuation","deal","unicorn","ipo","series",
    # geography
    "saudi","arabia","uae","qatar","kuwait","oman","bahrain","gcc",
    "mena","dubai","riyadh","abu","dhabi","doha","muscat","manama",
    "lebanese","lebanon","arab","emirati","khaleeji","jordanian",
    "egyptian","moroccan","tunisian","iraqi","yemeni","libyan",
    # action
    "launches","launch","orders","ordered","wins","bans","ban",
    "lifts","lifted","builds","built","becomes","became","joins",
    "signs","acquires","expands","hits","secures","secured","closes",
    "closed","backs","backed","funds","funded","partners","partnered",
    "invests","invested","announces","announced","unveils","unveiled",
}


# ── FONT HELPERS ─────────────────────────────────────────────────────────────

def get_font(path, size):
    try:
        return ImageFont.truetype(path, size)
    except Exception:
        return ImageFont.load_default()


def measure(draw, text, font):
    bb = draw.textbbox((0, 0), text, font=font)
    return bb[2] - bb[0], bb[3] - bb[1]


def word_color(word):
    """Orange for key words, black for the rest."""
    clean = word.lower().strip(".,!?:;\"'()[]%#@")
    if clean.startswith("$") or (any(c.isdigit() for c in clean) and any(c.isalpha() for c in clean)):
        return ORANGE
    if clean in HIGHLIGHT_WORDS:
        return ORANGE
    # ALL-CAPS acronyms (e.g. MBS, GCC, IPO)
    if word.isupper() and len(word) >= 2 and word.isalpha():
        return ORANGE
    return BLACK


def wrap_words(draw, words, font, max_w):
    """Greedy line-wrap; never puts a single orphan word alone if avoidable."""
    lines, cur = [], []
    sp_w, _ = measure(draw, " ", font)
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

    # Fix orphan: if last line has 1 word and 2nd-to-last has ≥ 3 words, rebalance
    if len(lines) >= 2 and len(lines[-1]) == 1 and len(lines[-2]) >= 3:
        moved = lines[-2].pop()
        lines[-1].insert(0, moved)

    return lines


def auto_fit(draw, headline, max_w, max_h, start=90, minimum=36):
    """Find largest font size where wrapped text fits within max_h."""
    words = headline.split()
    for size in range(start, minimum - 1, -2):
        font   = get_font(FONT_BOLD, size)
        lines  = wrap_words(draw, words, font, max_w)
        line_h = int(size * 1.28)
        if len(lines) * line_h <= max_h and len(lines) <= 5:
            return font, lines, size, line_h
    font   = get_font(FONT_BOLD, minimum)
    lines  = wrap_words(draw, words, font, max_w)
    line_h = int(minimum * 1.28)
    return font, lines, minimum, line_h


def draw_colored_line(draw, word_list, font, x, y):
    """Draw one line left-aligned with per-word colors."""
    sp_w, _ = measure(draw, " ", font)
    cx = x
    for word in word_list:
        color = word_color(word)
        draw.text((cx, y), word, font=font, fill=color)
        w, _ = measure(draw, word, font)
        cx += w + sp_w


# ── BACKGROUND HELPERS ───────────────────────────────────────────────────────

def make_gradient_bg(country_name):
    """Solid dark gradient when no article photo is available."""
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
    """Load image bytes → centre-crop to 1080×1080, or gradient fallback."""
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
            print(f"⚠️  Background image decode failed: {e}")
    return make_gradient_bg(country_name)


# ── BRANDED IMAGE GENERATOR ──────────────────────────────────────────────────

def generate_branded_image(bg_bytes, headline, country_name, logo_bytes=None):
    """
    Returns a PIL Image (1080×1080) styled like Smashi Business posts:
      - Full-bleed photo (or gradient) background
      - White rounded-rectangle card at bottom with auto-fitting headline
      - Per-word orange highlighting for key terms
      - Country code pill top-left, logo top-right
      - Blue accent bar at card bottom
    """
    base = prepare_background(bg_bytes, country_name)

    # Vignette: subtle dark fade on lower third so card floats cleanly
    vignette = Image.new("RGBA", (IMG_W, IMG_H), (0, 0, 0, 0))
    vd = ImageDraw.Draw(vignette)
    VIGN_H = 360
    for i in range(VIGN_H):
        alpha = int((i / VIGN_H) ** 1.9 * 170)
        vd.rectangle(
            [(0, IMG_H - VIGN_H + i), (IMG_W, IMG_H - VIGN_H + i + 1)],
            fill=(0, 0, 0, alpha)
        )
    base = Image.alpha_composite(base.convert("RGBA"), vignette).convert("RGB")
    draw = ImageDraw.Draw(base)

    # ── Card layout constants ──
    MARGIN   = 28
    PAD_X    = 40
    PAD_TOP  = 36
    PAD_BOT  = 28
    BLUE_H   = 8
    CARD_X   = MARGIN
    CARD_W   = IMG_W - 2 * MARGIN
    TEXT_W   = CARD_W - 2 * PAD_X
    MAX_TEXT_H = int(IMG_H * 0.42)   # headline block ≤ 42% of image height

    # ── Auto-fit headline ──
    font, lines, fsize, line_h = auto_fit(
        draw, headline, TEXT_W, MAX_TEXT_H, start=90, minimum=34
    )
    text_block_h = len(lines) * line_h
    card_h       = PAD_TOP + text_block_h + PAD_BOT + BLUE_H
    card_y       = IMG_H - MARGIN - card_h

    # ── White card ──
    draw.rounded_rectangle(
        [CARD_X, card_y, CARD_X + CARD_W, card_y + card_h],
        radius=20, fill=WHITE
    )

    # Blue accent bar at card bottom (flush bottom of card)
    bar_top = card_y + card_h - BLUE_H
    draw.rectangle(
        [CARD_X + 20, bar_top, CARD_X + CARD_W - 20, card_y + card_h],
        fill=BLUE_LINE
    )
    # Round the outer bottom corners to match the card
    draw.rounded_rectangle(
        [CARD_X, bar_top - 1, CARD_X + CARD_W, card_y + card_h],
        radius=20, fill=BLUE_LINE
    )
    # Re-draw white above to keep bar height exact
    draw.rectangle(
        [CARD_X + 1, card_y, CARD_X + CARD_W - 1, bar_top],
        fill=WHITE
    )

    # ── Headline text ──
    ty = card_y + PAD_TOP - 4
    for word_list in lines:
        draw_colored_line(draw, word_list, font, CARD_X + PAD_X, ty)
        ty += line_h

    # ── Country code pill — top left ──
    country_code = COUNTRY_MAP.get(country_name, {}).get("code", country_name[:3].upper())
    pill_x, pill_y = 22, 22
    code_font = get_font(FONT_BOLD, 21)
    cw, ch    = measure(draw, country_code, code_font)
    pill_w    = cw + 28
    pill_h    = ch + 20
    draw.rounded_rectangle(
        [pill_x, pill_y, pill_x + pill_w, pill_y + pill_h],
        radius=10, fill=WHITE
    )
    draw.text(
        (pill_x + 14, pill_y + 10),
        country_code, font=code_font, fill=(20, 20, 80)
    )

    # ── Logo — top right ──
    if logo_bytes:
        try:
            logo      = Image.open(io.BytesIO(logo_bytes)).convert("RGBA")
            logo_size = 106
            logo      = logo.resize((logo_size, logo_size), Image.LANCZOS)
            lx        = IMG_W - logo_size - 18
            ly        = 14
            base.paste(logo, (lx, ly), logo)
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
    sha  = None
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


# ── OG IMAGE FETCHER ─────────────────────────────────────────────────────────

def fetch_og_image_bytes(url: str):
    """Return bytes of og:image from article URL, or None."""
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
                print(f"✅ Live article: {a.get('title','')}")
                return a.get("title",""), a.get("description","") or a.get("content",""), a.get("url","")
    except Exception as e:
        print(f"NewsAPI error: {e}")
    return None, None, None


# ── AI POST GENERATION ────────────────────────────────────────────────────────

def generate_with_groq(prompt: str) -> str:
    if not GROQ_API_KEY:
        raise ValueError("GROQ_API_KEY missing.")
    url     = "https://api.groq.com/openai/v1/chat/completions"
    headers = {"Authorization": f"Bearer {GROQ_API_KEY}", "Content-Type": "application/json"}
    payload = {
        "model":    "llama-3.3-70b-versatile",
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.7,
    }
    for attempt in range(3):
        try:
            r = requests.post(url, json=payload, headers=headers, timeout=20)
            if r.status_code == 200:
                return r.json()["choices"][0]["message"]["content"].strip()
            elif r.status_code in [429, 503]:
                wait = 15 * (attempt + 1)
                print(f"   [Groq {attempt+1}/3] {r.status_code} — retry in {wait}s…")
                time.sleep(wait)
            else:
                raise RuntimeError(f"Groq {r.status_code}: {r.text}")
        except requests.exceptions.RequestException as e:
            time.sleep(15 * (attempt + 1))
    raise RuntimeError("Groq exhausted retries.")


def generate_with_gemini(prompt: str) -> str:
    keys = [k for k in [
        os.environ.get("GEMINI_API_KEY"),
        os.environ.get("GEMINI_API_KEY_BACKUP1"),
        os.environ.get("GEMINI_API_KEY_BACKUP2"),
    ] if k]
    if not keys:
        raise ValueError("No GEMINI_API_KEY found.")
    payload = {"contents": [{"parts": [{"text": prompt}]}]}
    for i, key in enumerate(keys):
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
                        wait = 35 * (attempt + 1)
                        print(f"   [Gemini key {i+1} attempt {attempt+1}] {r.status_code} — retry in {wait}s…")
                        time.sleep(wait)
                    else:
                        print(f"   [Gemini key {i+1}] rate-limited, trying next key…")
                        break
                else:
                    raise RuntimeError(f"Gemini {r.status_code}: {r.text}")
            except requests.exceptions.RequestException as e:
                time.sleep(35 * (attempt + 1))
    raise RuntimeError("Gemini exhausted all keys and retries.")


def generate_post_content(title: str, summary: str, source_url: str) -> str:
    prompt = (
        f"Write a professional LinkedIn post for an Arab startup ecosystem audience.\n"
        f"Article title: {title}\n"
        f"Summary: {summary}\n\n"
        f"IMPORTANT: Write ONLY about the article above.\n"
        f"Requirements:\n"
        f"- Start with a compelling hook (no generic openers like 'Exciting news')\n"
        f"- 3-5 short paragraphs\n"
        f"- Do NOT include the article URL inside the paragraphs\n"
        f"- After the last paragraph add exactly one blank line then: Read more: {source_url}\n"
        f"- After that add exactly one blank line then end with 4-6 relevant hashtags\n"
        f"- Tone: insightful, professional, engaging\n"
        f"- CRITICAL: No markdown. No asterisks. Plain text only."
    )
    print("🚀 Generating post with Groq…")
    try:
        return generate_with_groq(prompt)
    except Exception as e:
        print(f"⚠️  Groq failed: {e}. Falling back to Gemini…")
        try:
            return generate_with_gemini(prompt)
        except Exception as e2:
            print(f"❌ Both AI engines failed: {e2}")
            sys.exit(1)


# ── MAIN ─────────────────────────────────────────────────────────────────────

def main():
    country_name = get_country_for_today()
    country_data = COUNTRY_MAP.get(country_name, {"code": "GCC", "flag": "🌍"})
    flag         = country_data["flag"]
    print(f"📅 Today: {country_name} {flag}")

    # 1. Get article from DB (or live fallback)
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

    # 2. Generate LinkedIn post text
    post_text = generate_post_content(
        db_title or summary or country_name,
        summary  or "",
        source_url or "",
    )

    # 3. Build image headline (emoji prefix + title)
    title_lower  = (db_title or "").lower()
    emoji_prefix = (
        "💰" if any(w in title_lower for w in ["fund","million","billion","invest","raise","raises"]) else
        "🚀" if any(w in title_lower for w in ["launch","startup","expansion","unveil"]) else
        "📈" if any(w in title_lower for w in ["growth","economy","gdp","market","record"]) else
        "🤝" if any(w in title_lower for w in ["partner","sign","deal","acqui","merger"]) else
        "🌍"
    )
    image_headline = f"{emoji_prefix} {(db_title or '').strip()}" if db_title else post_text.split("\n")[0][:100]
    print(f"🖼  Image headline: {image_headline}")

    # 4. Fetch og:image from article URL (bytes, not URL string)
    print("🔍 Fetching article og:image…")
    bg_bytes = fetch_og_image_bytes(source_url)
    if not bg_bytes:
        print("ℹ️  No og:image found — using country gradient background.")

    # 5. Fetch logo bytes from GitHub
    logo_bytes = None
    try:
        lr         = requests.get(f"{GITHUB_BASE}logo.jpg", timeout=10)
        logo_bytes = lr.content if lr.status_code == 200 else None
    except Exception as e:
        print(f"⚠️  Logo fetch failed: {e}")

    # 6. Generate branded image
    print("🎨 Generating branded image…")
    branded_img = generate_branded_image(bg_bytes, image_headline, country_name, logo_bytes)

    # 7. Upload to GitHub
    filename      = f"post_{country_data['code']}_{datetime.now(timezone.utc).strftime('%Y%m%d')}.jpg"
    thumbnail_url = upload_image_to_github(branded_img, filename)

    # 8. Send to Make.com
    payload = {
        "text":          post_text,
        "url":           "",          # no link preview — image IS the post
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
