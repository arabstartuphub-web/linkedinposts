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
from pilmoji import Pilmoji

# --- CONFIG ---
DB_URL         = os.environ.get("DATABASE_URL")
GROQ_API_KEY   = os.environ.get("GROQ_API_KEY")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
NEWS_API_KEY   = os.environ.get("NEWS_API_KEY")
WEBHOOK_URL    = os.environ.get("MAKE_WEBHOOK_URL")
GITHUB_TOKEN   = os.environ.get("GITHUB_TOKEN")
GITHUB_REPO    = "arabstartuphub-web/linkedinposts"
GITHUB_BRANCH  = "main"
GITHUB_BASE    = f"https://raw.githubusercontent.com/{GITHUB_REPO}/{GITHUB_BRANCH}/"

# --- IMAGE DESIGN ---
FONT_BOLD      = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
FONT_REG       = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
IMG_W, IMG_H   = 1200, 627
ELECTRIC_CYAN  = (0, 230, 255)
WHITE          = (255, 255, 255)
SHADOW         = (0, 0, 0)

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


# ── COUNTRY / ARTICLE HELPERS ────────────────────────────────────────────────

def get_country_for_today() -> str:
    weekday = datetime.now(timezone.utc).weekday()
    return WEEKDAY_COUNTRY.get(weekday, "GCC")


def get_daily_article(country_name: str):
    conn = psycopg2.connect(DB_URL)
    cur  = conn.cursor()
    cur.execute(
        """
        SELECT id, title, summary, source_url
        FROM articles
        WHERE linkedin_posted = FALSE AND country = %s
        ORDER BY created_at ASC
        LIMIT 1;
        """,
        (country_name,),
    )
    row = cur.fetchone()
    cur.close()
    conn.close()
    return row


def mark_article_posted(article_id: int):
    conn = psycopg2.connect(DB_URL)
    cur  = conn.cursor()
    cur.execute(
        "UPDATE articles SET linkedin_posted = TRUE WHERE id = %s;",
        (article_id,),
    )
    conn.commit()
    cur.close()
    conn.close()


def fetch_live_article(country_name: str):
    query_map = {
        "Saudi Arabia": "Saudi Arabia startup OR economy OR business",
        "UAE":          "UAE startup OR economy OR business",
        "Qatar":        "Qatar startup OR economy OR business",
        "Kuwait":       "Kuwait startup OR economy OR business",
        "Oman":         "Oman startup OR economy OR business",
        "Bahrain":      "Bahrain startup OR economy OR business",
        "GCC":          "GCC startup OR economy OR business",
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
                print(f"✅ Live article fetched: {a.get('title','')}")
                return a.get("title",""), a.get("description","") or a.get("content",""), a.get("url","")
    except Exception as e:
        print(f"NewsAPI error: {e}")
    return None, None, None


# ── OG:IMAGE EXTRACTOR ───────────────────────────────────────────────────────

def get_article_image(url: str) -> str:
    if not url:
        return None
    try:
        res = requests.get(url, timeout=10, headers={"User-Agent": "Mozilla/5.0"})
        if res.status_code == 200:
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
            if parser.og_image:
                print(f"✅ og:image found: {parser.og_image}")
                return parser.og_image
    except Exception as e:
        print(f"⚠️ Could not fetch og:image: {e}")
    return None


# ── IMAGE GENERATION ─────────────────────────────────────────────────────────

def wrap_text_centered(text, font, draw, max_width):
    words = text.split()
    lines, current = [], ""
    for word in words:
        test = f"{current} {word}".strip()
        try:
            w = draw.textlength(test, font=font)
        except Exception:
            w = len(test) * 30
        if w <= max_width:
            current = test
        else:
            if current:
                lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines[:4]


def generate_branded_image(source_image_url, headline, country_name, flag, fallback_banner_url):
    # Load background
    img_url = source_image_url or fallback_banner_url
    try:
        res  = requests.get(img_url, timeout=10, headers={"User-Agent": "Mozilla/5.0"})
        base = Image.open(io.BytesIO(res.content)).convert("RGB")
    except Exception as e:
        print(f"⚠️ Background load failed ({e}), using black canvas.")
        base = Image.new("RGB", (IMG_W, IMG_H), (20, 20, 20))

    # Resize / centre-crop to 1200x627
    bw, bh = base.size
    if bw / bh > IMG_W / IMG_H:
        new_h, new_w = IMG_H, int((bw / bh) * IMG_H)
    else:
        new_w, new_h = IMG_W, int((bh / bw) * IMG_W)
    base = base.resize((new_w, new_h), Image.LANCZOS)
    left = (new_w - IMG_W) // 2
    top  = (new_h - IMG_H) // 2
    base = base.crop((left, top, left + IMG_W, top + IMG_H))
    # Ensure exact output size
    base = base.resize((IMG_W, IMG_H), Image.LANCZOS)

    # Solid black strip behind text
    overlay = Image.new("RGBA", (IMG_W, IMG_H), (0, 0, 0, 0))
    ov_draw = ImageDraw.Draw(overlay)
    strip_top    = int(IMG_H * 0.55)
    strip_bottom = IMG_H - 10
    ov_draw.rectangle(
        [(0, strip_top), (IMG_W, strip_bottom)],
        fill=(0, 0, 0, 210)
    )
    base = base.convert("RGBA")
    base = Image.alpha_composite(base, overlay)
    base = base.convert("RGB")
    draw = ImageDraw.Draw(base)

    try:
        font_headline = ImageFont.truetype(FONT_BOLD, 58)
        font_sub      = ImageFont.truetype(FONT_BOLD, 30)
        font_small    = ImageFont.truetype(FONT_REG,  21)
    except Exception:
        font_headline = font_sub = font_small = ImageFont.load_default()

    # Wrap headline text
    lines             = wrap_text_centered(headline, font_headline, draw, IMG_W - 160)
    line_h            = 68
    text_block_bottom = IMG_H - 85
    text_y            = text_block_bottom - (len(lines) * line_h)

    # Draw headline with Pilmoji (supports emoji rendering)
    with Pilmoji(base) as pilmoji:
        for idx, line in enumerate(lines):
            color = WHITE if idx == 0 else ELECTRIC_CYAN
            try:
                w = draw.textlength(line, font=font_headline)
            except Exception:
                w = len(line) * 32
            x = (IMG_W - w) // 2
            # Draw shadow
            for dx, dy in [(-4,4),(4,4),(-4,-4),(4,-4),(0,4),(0,-4),(-4,0),(4,0)]:
                pilmoji.text((x+dx, text_y+dy), line, font=font_headline, fill=SHADOW)
            # Draw main text
            pilmoji.text((x, text_y), line, font=font_headline, fill=color)
            text_y += line_h

    # Thin electric cyan underline
    draw.rectangle(
        [(IMG_W//2 - 200, text_block_bottom - 2), (IMG_W//2 + 200, text_block_bottom + 3)],
        fill=ELECTRIC_CYAN
    )

    # Country + flag in electric cyan using Pilmoji
    ct = f"{flag}  {country_name}  {flag}"
    with Pilmoji(base) as pilmoji:
        try:
            cw = draw.textlength(ct, font=font_sub)
        except Exception:
            cw = len(ct) * 18
        cx = (IMG_W - cw) // 2
        for dx, dy in [(-3,3),(3,3),(-3,-3),(3,-3),(0,3),(0,-3),(-3,0),(3,0)]:
            pilmoji.text((cx+dx, text_block_bottom+6+dy), ct, font=font_sub, fill=SHADOW)
        pilmoji.text((cx, text_block_bottom + 6), ct, font=font_sub, fill=ELECTRIC_CYAN)

    # Website bottom-center
    site = "ase-web.onrender.com"
    sw   = draw.textlength(site, font=font_small)
    for dx, dy in [(-2,2),(2,2),(-2,-2),(2,-2),(0,2),(0,-2),(-2,0),(2,0)]:
        draw.text(((IMG_W - sw)//2 + dx, IMG_H - 30 + dy), site, font=font_small, fill=SHADOW)
    draw.text(((IMG_W - sw) // 2, IMG_H - 30), site, font=font_small, fill=(220, 220, 220))

    # Logo bottom-left — fetched from GitHub
    try:
        logo_url = f"{GITHUB_BASE}logo.jpg"
        lr       = requests.get(logo_url, timeout=10)
        logo     = Image.open(io.BytesIO(lr.content)).convert("RGBA")
        logo     = logo.resize((95, 95), Image.LANCZOS)
        base.paste(logo, (22, IMG_H - 115), logo)
    except Exception as e:
        print(f"⚠️ Logo load failed: {e}")

    return base


# ── GITHUB UPLOAD ────────────────────────────────────────────────────────────

def upload_image_to_github(img: Image.Image, filename: str) -> str:
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=92)
    content_b64 = base64.b64encode(buf.getvalue()).decode()

    api_url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/generated/{filename}"
    headers = {
        "Authorization": f"token {GITHUB_TOKEN}",
        "Accept": "application/vnd.github.v3+json",
    }

    sha = None
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
        print(f"✅ Image uploaded to GitHub: {raw_url}")
        return raw_url
    else:
        raise RuntimeError(f"GitHub upload failed {res.status_code}: {res.text}")


# ── AI GENERATION ────────────────────────────────────────────────────────────

def generate_with_groq(prompt: str) -> str:
    if not GROQ_API_KEY:
        raise ValueError("GROQ_API_KEY missing.")
    url     = "https://api.groq.com/openai/v1/chat/completions"
    headers = {"Authorization": f"Bearer {GROQ_API_KEY}", "Content-Type": "application/json"}
    payload = {
        "model": "llama-3.3-70b-versatile",
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.7
    }
    for attempt in range(3):
        try:
            r = requests.post(url, json=payload, headers=headers, timeout=20)
            if r.status_code == 200:
                return r.json()["choices"][0]["message"]["content"].strip()
            elif r.status_code in [429, 503]:
                wait = 15 * (attempt + 1)
                print(f"   [Groq {attempt+1}/3] {r.status_code} — retrying in {wait}s...")
                time.sleep(wait)
            else:
                raise RuntimeError(f"Groq {r.status_code}: {r.text}")
        except requests.exceptions.RequestException as e:
            wait = 15 * (attempt + 1)
            print(f"   [Groq {attempt+1}/3] {e} — retrying in {wait}s...")
            time.sleep(wait)
    raise RuntimeError("Groq exhausted all retries.")


def generate_with_gemini(prompt: str) -> str:
    if not GEMINI_API_KEY:
        raise ValueError("GEMINI_API_KEY missing.")
    url     = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent?key={GEMINI_API_KEY}"
    payload = {"contents": [{"parts": [{"text": prompt}]}]}
    for attempt in range(3):
        try:
            r = requests.post(url, json=payload, timeout=20)
            if r.status_code == 200:
                return r.json()["candidates"][0]["content"]["parts"][0]["text"].strip()
            elif r.status_code in [429, 503]:
                wait = 35 * (attempt + 1)
                print(f"   [Gemini {attempt+1}/3] {r.status_code} — retrying in {wait}s...")
                time.sleep(wait)
            else:
                raise RuntimeError(f"Gemini {r.status_code}: {r.text}")
        except requests.exceptions.RequestException as e:
            wait = 35 * (attempt + 1)
            print(f"   [Gemini {attempt+1}/3] {e} — retrying in {wait}s...")
            time.sleep(wait)
    raise RuntimeError("Gemini exhausted all retries.")


def generate_post_content(title: str, summary: str, source_url: str) -> str:
    prompt = (
        f"Write a professional LinkedIn post for an Arab startup ecosystem audience.\n"
        f"Article title: {title}\n"
        f"Summary: {summary}\n\n"
        f"IMPORTANT: Write ONLY about the article above. Do not introduce unrelated topics, companies, or technologies not mentioned in the title or summary.\n"
        f"Requirements:\n"
        f"- Start with a compelling hook (no generic openers like 'Exciting news')\n"
        f"- 3-5 short paragraphs\n"
        f"- Do NOT include the article URL inside the paragraphs\n"
        f"- After the last paragraph add exactly one blank line then write: Read more: {source_url}\n"
        f"- After that add exactly one blank line then end with 4-6 relevant hashtags\n"
        f"- Tone: insightful, professional, engaging\n"
        f"- CRITICAL: Do NOT use markdown formatting. Never use asterisks (**) for bolding or emphasis. Output pure plain text only."
    )
    print("🚀 Primary Engine: Groq (Llama-3.3)...")
    try:
        return generate_with_groq(prompt)
    except Exception as e:
        print(f"⚠️ Groq failed: {e}")
        print("🔄 Fallback: Gemini...")
        try:
            return generate_with_gemini(prompt)
        except Exception as e2:
            print(f"❌ Gemini failed: {e2}")
            print("🚨 Both AI engines failed.")
            sys.exit(1)


def get_title_fallback(post_text: str, country_name: str, flag: str) -> str:
    lines   = [ln.strip() for ln in post_text.splitlines() if ln.strip()]
    snippet = " ".join(lines[:2])
    if len(snippet) > 80:
        snippet = snippet[:77].rstrip() + "…"
    return f"{flag} {snippet} | {country_name}"


# ── MAIN ─────────────────────────────────────────────────────────────────────

def main():
    country_name = get_country_for_today()
    country_data = COUNTRY_MAP.get(country_name, {"code": "GCC", "flag": "🌍"})
    flag         = country_data["flag"]
    print(f"Today's scheduled country: {country_name} {flag}")

    # 1. Get article
    article = get_daily_article(country_name)
    if not article:
        print(f"No DB articles for {country_name}. Fetching live from NewsAPI...")
        article_id = None
        db_title, summary, source_url = fetch_live_article(country_name)
        if not db_title:
            print("NewsAPI also returned nothing. Exiting.")
            sys.exit(0)
    else:
        article_id, db_title, summary, source_url = article

    print(f"Article: {db_title or '(no title)'}")

    # 2. Generate post text — URL included at end by prompt
    post_text = generate_post_content(
        db_title or summary or country_name,
        summary or "",
        source_url or ""
    )

    # 3. Build title for image headline with emoji prefix
    if db_title and db_title.strip():
        title_lower = db_title.lower()
        emoji_prefix = "💰" if any(w in title_lower for w in ["fund", "million", "billion", "invest", "raise"]) else \
                       "🚀" if any(w in title_lower for w in ["launch", "startup", "expansion"]) else \
                       "📈" if any(w in title_lower for w in ["growth", "economy", "gdp", "market"]) else "🌍"
        final_title = f"{emoji_prefix} {db_title.strip()}"
    else:
        final_title = get_title_fallback(post_text, country_name, flag)
    print(f"Final title: {final_title}")

    # 4. Get source og:image or fall back to country banner
    print("🔍 Checking article for og:image...")
    source_image_url = get_article_image(source_url)
    fallback_banner  = f"{GITHUB_BASE}{country_data['code']}.jpg"

    # 5. Generate branded image
    print("🎨 Generating branded image...")
    branded_img = generate_branded_image(
        source_image_url,
        final_title,
        country_name, flag,
        fallback_banner
    )

    # 6. Upload branded image to GitHub
    filename      = f"post_{country_data['code']}_{datetime.now(timezone.utc).strftime('%Y%m%d')}.jpg"
    thumbnail_url = upload_image_to_github(branded_img, filename)
    print(f"Target Graphic URL: {thumbnail_url}")

    # 7. Send to Make.com
    # Note: url field is empty — no link preview card, image posts as full visual
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
        print(f"❌ Webhook failed — status {res.status_code}: {res.text}")
        sys.exit(1)


if __name__ == "__main__":
    main()
