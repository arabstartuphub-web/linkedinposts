import os
import sys
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
    "Saudi Arabia": {"code": "KSA",    "flag": "🇸🇦"},
    "UAE":          {"code": "UAE",    "flag": "🇦🇪"},
    "Qatar":        {"code": "QATAR",  "flag": "🇶🇦"},
    "Kuwait":       {"code": "KUWAIT", "flag": "🇰🇼"},
    "Oman":         {"code": "OMAN",   "flag": "🇴🇲"},
    "Bahrain":      {"code": "BAHRAIN","flag": "🇧🇭"},
    "GCC":          {"code": "GCC",    "flag": "🌍"},
}

# Maps Python weekday (Mon=0 … Sun=6) → country to post
# Schedule intent:
#   Sun, Mon, Wed, Thu, Sat → GCC ecosystem countries (rotate through 5)
#   Tue, Fri               → UAE, Oman
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
    """
    Fetch one unposted article for the given country from Neon DB.
    Returns (id, title, summary, source_url) or None.
    """
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
    """Call Gemini REST API to generate a LinkedIn post."""
    url = (
        f"https://generativelanguage.googleapis.com/v1beta/models/"
        f"gemini-1.5-flash:generateContent?key={API_KEY}"
    )
    prompt = (
        f"Write a professional LinkedIn post for an Arab startup ecosystem audience.\n"
        f"Article title: {title}\n"
        f"Summary: {summary}\n\n"
        f"Requirements:\n"
        f"- Start with a compelling hook (no generic openers like 'Exciting news')\n"
        f"- 3-5 short paragraphs\n"
        f"- End with 4-6 relevant hashtags\n"
        f"- Tone: insightful, professional, engaging"
    )
    payload = {
        "contents": [{"parts": [{"text": prompt}]}]
    }
    response = requests.post(url, json=payload, timeout=30)

    if response.status_code == 200:
        data = response.json()
        return data["candidates"][0]["content"]["parts"][0]["text"].strip()
    else:
        print(f"Gemini API Error {response.status_code}: {response.text}")
        sys.exit(1)


def get_title_fallback(post_text: str, country_name: str, flag: str) -> str:
    """
    Build a title from the first 2-3 lines of the generated post
    + country name + flag, used when the DB title is empty.
    """
    lines = [ln.strip() for ln in post_text.splitlines() if ln.strip()]
    # Take up to the first 2 non-empty lines, cap at ~80 chars total
    snippet = " ".join(lines[:2])
    if len(snippet) > 80:
        snippet = snippet[:77].rstrip() + "…"
    return f"{flag} {snippet} | {country_name}"


def is_image_url_accessible(url: str) -> bool:
    """Quick HEAD check to see if the source thumbnail URL is reachable."""
    try:
        r = requests.head(url, timeout=8, allow_redirects=True)
        content_type = r.headers.get("Content-Type", "")
        return r.status_code == 200 and "image" in content_type
    except Exception:
        return False


def main():
    # 1. Determine which country to post about today
    country_name = get_country_for_today()
    country_data = COUNTRY_MAP.get(country_name, {"code": "GCC", "flag": "🌍"})
    flag = country_data["flag"]
    print(f"Today's country: {country_name} {flag}")

    # 2. Fetch article from DB
    article = get_daily_article(country_name)
    if not article:
        print(f"No unposted articles found for {country_name}. Exiting.")
        sys.exit(0)

    article_id, db_title, summary, source_url = article
    print(f"Article ID {article_id}: {db_title or '(no title)'}")

    # 3. Generate LinkedIn post content via Gemini
    post_text = generate_post_content(db_title or summary or country_name, summary or "")

    # 4. Resolve final title
    #    If DB title is empty/whitespace, derive from post content + country + flag
    if db_title and db_title.strip():
        final_title = f"{flag} {db_title.strip()} | {country_name}"
    else:
        final_title = get_title_fallback(post_text, country_name, flag)
    print(f"Final title: {final_title}")

    # 5. Resolve thumbnail
    #    Try the article's source URL for an OG/thumbnail image.
    #    Make.com's HTTP "Download a file" step will attempt to fetch it —
    #    we just need to pass the best URL we can. If source_url looks like
    #    a valid article page, pass it and let Make.com extract the image.
    #    As the absolute fallback, use the country image from this repo.
    fallback_thumb = f"{GITHUB_BASE}{country_data['code']}.jpg"

    if source_url and source_url.startswith("http"):
        # Pass the article source URL; Make.com's HTTP module will resolve
        # the OG image. If Make.com returns empty, the workflow should fall
        # back to the country image — wire that logic in Make.com too.
        thumbnail_url = source_url
    else:
        thumbnail_url = fallback_thumb

    print(f"Thumbnail URL: {thumbnail_url}")
    print(f"Fallback thumbnail: {fallback_thumb}")

    # 6. Send to Make.com webhook
    payload = {
        "text": post_text,
        "url": source_url or "",
        "title": final_title,
        "thumbnail_url": thumbnail_url,
        "fallback_thumbnail_url": fallback_thumb,   # Make.com can use this if HTTP step fails
        "country": country_name,
        "flag": flag,
    }

    print("Sending to Make.com webhook…")
    res = requests.post(WEBHOOK_URL, json=payload, timeout=30)

    if res.status_code in [200, 201, 204]:
        print("✅ Successfully sent to Make.com.")
        # 7. Mark article as posted so it won't repeat
        mark_article_posted(article_id)
        print(f"✅ Article ID {article_id} marked as posted.")
    else:
        print(f"❌ Webhook failed — status {res.status_code}: {res.text}")
        sys.exit(1)


if __name__ == "__main__":
    main()
