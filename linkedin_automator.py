import os
import sys
import time
import requests
import psycopg2
from datetime import datetime, timezone

# --- CONFIG ---
DB_URL = os.environ.get("DATABASE_URL")
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


def generate_with_gemini(api_key: str, prompt: str) -> str:
    """Executes the specific API REST post request for a single key."""
    url = (
        "https://generativelanguage.googleapis.com/v1beta/models/"
        f"gemini-2.0-flash-lite:generateContent?key={api_key}"
    )
    payload = {"contents": [{"parts": [{"text": prompt}]}]}
    response = requests.post(url, json=payload, timeout=30)
    
    if response.status_code == 200:
        data = response.json()
        return data["candidates"][0]["content"]["parts"][0]["text"].strip()
    elif response.status_code == 429:
        raise RuntimeWarning("429 Rate Limit Hit")
    else:
        raise RuntimeError(f"API Error {response.status_code}: {response.text}")


def generate_post_content(title: str, summary: str) -> str:
    """Loops sequentially through available Gemini accounts to generate content."""
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

    # Compile keys in priority order
    gemini_keys = [
        os.environ.get("GEMINI_API_KEY"),
        os.environ.get("GEMINI_API_KEY_BACKUP1"),
        os.environ.get("GEMINI_API_KEY_BACKUP2")
    ]
    # Filter out empty or unassigned keys
    active_keys = [k for k in gemini_keys if k]

    if not active_keys:
        print("❌ Critical Error: No Gemini API keys found in runtime environment.")
        sys.exit(1)

    # Sequence execution through keys
    for idx, key in enumerate(active_keys):
        account_label = "Primary" if idx == 0 else f"Backup {idx}"
        print(f"Attempting post generation with Gemini {account_label} Account...")
        
        for attempt in range(2):
            try:
                return generate_with_gemini(key, prompt)
            except RuntimeWarning:
                wait = 20 * (attempt + 1)
                print(f"[{account_label}] Rate limited (429). Waiting {wait}s before retry...")
                time.sleep(wait)
            except Exception as e:
                print(f"[{account_label}] Failed with error: {e}. Moving to next account structure.")
                break  # Break inner retry loop to immediately swap to next backup key

    print("❌ Critical Error: All configured Gemini accounts have exhausted their limits or failed.")
    sys.exit(1)


def get_title_fallback(post_text: str, country_name: str, flag: str) -> str:
    """
    Build a title from the first 2 lines of the generated post
    + country name + flag, used when the DB title is empty.
    """
    lines = [ln.strip() for ln in post_text.splitlines() if ln.strip()]
    snippet = " ".join(lines[:2])
    if len(snippet) > 80:
        snippet = snippet[:77].rstrip() + "…"
    return f"{flag} {snippet} | {country_name}"


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

    # 3. Generate LinkedIn post content via Gemini rotating array
    post_text = generate_post_content(db_title or summary or country_name, summary or "")

    # 4. Resolve final title fallback logic
    if db_title and db_title.strip():
        final_title = f"{flag} {db_title.strip()} | {country_name}"
    else:
        final_title = get_title_fallback(post_text, country_name, flag)
    print(f"Final title: {final_title}")

    # 5. Resolve thumbnail links safely
    fallback_thumb = f"{GITHUB_BASE}{country_data['code']}.jpg"
    thumbnail_url = source_url if (source_url and source_url.startswith("http")) else fallback_thumb
    print(f"Thumbnail URL: {thumbnail_url}")
    print(f"Fallback thumbnail: {fallback_thumb}")

    # 6. Send to Make.com webhook payload package
    payload = {
        "text": post_text,
        "url": source_url or "",
        "title": final_title,
        "thumbnail_url": thumbnail_url,
        "fallback_thumbnail_url": fallback_thumb,
        "country": country_name,
        "flag": flag,
    }

    print("Sending to Make.com webhook…")
    res = requests.post(WEBHOOK_URL, json=payload, timeout=30)

    if res.status_code in [200, 201, 204]:
        print("✅ Successfully sent to Make.com.")
        mark_article_posted(article_id)
        print(f"✅ Article ID {article_id} marked as posted.")
    else:
        print(f"❌ Webhook failed — status {res.status_code}: {res.text}")
        sys.exit(1)


if __name__ == "__main__":
    main()
