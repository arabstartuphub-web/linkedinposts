import os
import sys
import time
import requests
import psycopg2
from datetime import datetime, timezone

# --- CONFIG ---
DB_URL = os.environ.get("DATABASE_URL")
API_KEY = os.environ.get("GEMINI_API_KEY")
WEBHOOK_URL = os.environ.get("MAKE_WEBHOOK_URL")

# GitHub raw URL base for fallback images
GITHUB_BASE = "https://raw.githubusercontent.com/arabstartuphub-web/linkedinposts/main/"

# Maps country name → image code and flag emoji
COUNTRY_MAP = {
    "Saudi Arabia": {"code": "KSA",     "flag": "🇸🇦"},
    "UAE":          {"code": "UAE",     "flag": "🇦🇪"},
    "Qatar":        {"code": "QATAR",   "flag": "🇶🇦"},
    "Kuwait":       {"code": "KUWAIT",  "flag": "🇰🇼"},
    "Oman":         {"code": "OMAN",    "flag": "🇴🇲"},
    "Bahrain":      {"code": "BAHRAIN", "flag": "🇧🇭"},
    "GCC":          {"code": "GCC",     "flag": "🌍"},
}

# Maps Python weekday (Mon=0 … Sun=6) → country to post
WEEKDAY_COUNTRY = {
    0: "Saudi Arabia",  # Monday
    1: "UAE",           # Tuesday
    2: "Qatar",         # Wednesday
    3: "Kuwait",        # Thursday
    4: "Oman",          # Friday
    5: "Bahrain",       # Saturday
    6: "GCC",           # Sunday
}


def get_country_for_today() -> str:
    """Return the country name to post about today based on UTC weekday."""
    weekday = datetime.now(timezone.utc).weekday()  # Mon=0 … Sun=6
    return WEEKDAY_COUNTRY.get(weekday, "GCC")


def get_daily_article(country_name: str):
    """Fetch one unposted article for the given country from Neon DB."""
    conn = psycopg2.connect(DB_URL)
    cur = conn.cursor()
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
    return row  # (id, title, summary, source_url) or None


def mark_article_posted(article_id: int):
    """Mark the article as posted so it won't be reused."""
    conn = psycopg2.connect(DB_URL)
    cur = conn.cursor()
    cur.execute(
        "UPDATE articles SET linkedin_posted = TRUE WHERE id = %s;",
        (article_id,),
    )
    conn.commit()
    cur.close()
    conn.close()


def generate_post_content(title: str, summary: str) -> str:
    """Call Gemini REST API to generate a LinkedIn post, with retry on 429."""
    url = (
        "https://generativelanguage.googleapis.com/v1beta/models/"
        f"gemini-2.0-flash:generateContent?key={API_KEY}"
    )
    prompt = (
        f"Write a professional LinkedIn post for an Arab startup ecosystem audience.\n"
        f"Article title: {title}\n"
        f"Summary: {summary}\n\n"
        f"Requirements:\n"
        f"- Start with a compelling hook (no generic openers like 'Exciting news')\n"
        f"- 3-5 short paragraphs\n"
        f"- End with 4-6 relevant hashtags\n"
        f"- Tone: insightful, professional, engaging\n"
        f"- CRITICAL: Do NOT use markdown formatting. Never use asterisks (**) for bolding or emphasis. Output pure plain text only."
    )
    payload = {"contents": [{"parts": [{"text": prompt}]}]}

    for attempt in range(3):
        response = requests.post(url, json=payload, timeout=30)
        if response.status_code == 200:
            data = response.json()
            return data["candidates"][0]["content"]["parts"][0]["text"].strip()
        elif response.status_code == 429:
            wait = 35 * (attempt + 1)
            print(f"Rate limited (429). Waiting {wait}s before retry {attempt + 1}/3…")
            time.sleep(wait)
        else:
            print(f"Gemini API Error {response.status_code}: {response.text}")
            sys.exit(1)

    print("Gemini API still rate-limited after 3 retries. Check quota.")
    sys.exit(1)


def get_title_fallback(post_text: str, country_name: str, flag: str) -> str:
    """Build a title from the first 2 lines of the generated post if DB title is empty."""
    lines = [ln.strip() for ln in post_text.splitlines() if ln.strip()]
    snippet = " ".join(lines[:2])
    if len(snippet) > 80:
        snippet = snippet[:77].rstrip() + "…"
    return f"{flag} {snippet} | {country_name}"


def main():
    country_name = get_country_for_today()
    country_data = COUNTRY_MAP.get(country_name, {"code": "GCC", "flag": "🌍"})
    flag = country_data["flag"]
    print(f"Today's country: {country_name} {flag}")

    article = get_daily_article(country_name)
    if not article:
        print(f"No unposted articles found for {country_name}. Exiting.")
        sys.exit(0)

    article_id, db_title, summary, source_url = article
    print(f"Article ID {article_id}: {db_title or '(no title)'}")

    post_text = generate_post_content(db_title or summary or country_name, summary or "")

    if db_title and db_title.strip():
        final_title = f"{flag} {db_title.strip()} | {country_name}"
    else:
        final_title = get_title_fallback(post_text, country_name, flag)
    print(f"Final title: {final_title}")

    # FIX: Always use the clean country graphic JPG file hosted on GitHub
    thumbnail_url = f"{GITHUB_BASE}{country_data['code']}.jpg"
    print(f"Target Graphic URL: {thumbnail_url}")

    payload = {
        "text": post_text,
        "url": source_url or "",
        "title": final_title,
        "thumbnail_url": thumbnail_url,
        "country": country_name,
        "flag": flag,
    }

    print("Sending to Make.com webhook…")
    res = requests.post(WEBHOOK_URL, json=payload, timeout=30)

    if res.status_code in [200, 201, 204]:
        print("✅ Successfully sent to Make.com.")
        mark_article_posted(article_id)
    else:
        print(f"❌ Webhook failed — status {res.status_code}: {res.text}")
        sys.exit(1)


if __name__ == "__main__":
    main()
