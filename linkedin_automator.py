import os
import sys
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
    weekday = datetime.now(timezone.utc).weekday()
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
    return row


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


def generate_with_gemini(api_key: str, model_name: str, prompt: str) -> str:
    """Executes an instant REST request with a tight timeout window."""
    url = (
        "https://generativelanguage.googleapis.com/v1beta/models/"
        f"{model_name}:generateContent?key={api_key}"
    )
    payload = {"contents": [{"parts": [{"text": prompt}]}]}
    
    # 10 second timeout ensures GitHub Action never hangs on bad or dead endpoints
    response = requests.post(url, json=payload, timeout=10)
    
    if response.status_code == 200:
        data = response.json()
        return data["candidates"][0]["content"]["parts"][0]["text"].strip()
    else:
        raise RuntimeError(f"Status {response.status_code}")


def generate_post_content(title: str, summary: str) -> str:
    """
    Priority Matrix Strategy:
    Tier 1: Try gemini-3.5-flash across Account 1 -> Account 2 -> Account 3.
    Tier 2: Try gemini-3.1-flash-lite across Account 1 -> Account 2 -> Account 3.
    Fails over instantly without using time.sleep() delays.
    """
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

    # Model hierarchy selection
    models = ["gemini-3.5-flash", "gemini-3.1-flash-lite"]

    # Account token layout
    gemini_keys = [
        os.environ.get("GEMINI_API_KEY"),
        os.environ.get("GEMINI_API_KEY_BACKUP1"),
        os.environ.get("GEMINI_API_KEY_BACKUP2")
    ]
    active_keys = [k for k in gemini_keys if k]

    if not active_keys:
        print("❌ Critical Error: No Gemini API keys found in environment variables.")
        sys.exit(1)

    # Sequence Strategy Loop
    for model in models:
        print(f"--- Evaluative Priority Tier: {model} ---")
        for idx, key in enumerate(active_keys):
            account_label = "Primary" if idx == 0 else f"Backup {idx}"
            print(f"Sending prompt to {model} via {account_label} Account...")
            
            try:
                return generate_with_gemini(key, model, prompt)
            except Exception as e:
                # Catch failures and instantly jump to the next account variant
                print(f"⚠️ [{account_label}] failed using {model} ({e}). Advancing instantly...")
                continue

    print("❌ Critical Error: All accounts and model tiers are fully exhausted for today.")
    sys.exit(1)


def get_title_fallback(post_text: str, country_name: str, flag: str) -> str:
    """Build a title from the first 2 lines of the generated post text."""
    lines = [ln.strip() for ln in post_text.splitlines() if ln.strip()]
    snippet = " ".join(lines[:2])
    if len(snippet) > 80:
        snippet = snippet[:77].rstrip() + "…"
    return f"{flag} {snippet} | {country_name}"


def main():
    # 1. Select targeted country segment
    country_name = get_country_for_today()
    country_data = COUNTRY_MAP.get(country_name, {"code": "GCC", "flag": "🌍"})
    flag = country_data["flag"]
    print(f"Today's country segment: {country_name} {flag}")

    # 2. Extract context asset from Neon DB
    article = get_daily_article(country_name)
    if not article:
        print(f"No unposted articles found for {country_name}. Script shutdown.")
        sys.exit(0)

    article_id, db_title, summary, source_url = article
    print(f"Processing Article ID {article_id}: {db_title or '(No Title Available)'}")

    # 3. Request multi-tier post content generation
    post_text = generate_post_content(db_title or summary or country_name, summary or "")

    # 4. Resolve explicit title processing constraints
    if db_title and db_title.strip():
        final_title = f"{flag} {db_title.strip()} | {country_name}"
    else:
        final_title = get_title_fallback(post_text, country_name, flag)
    print(f"Resolved Final Title: {final_title}")

    # 5. Extract fallback routing urls
    fallback_thumb = f"{GITHUB_BASE}{country_data['code']}.jpg"
    thumbnail_url = source_url if (source_url and source_url.startswith("http")) else fallback_thumb

    # 6. Dispatch parameters downstream to Make.com
    payload = {
        "text": post_text,
        "url": source_url or "",
        "title": final_title,
        "thumbnail_url": thumbnail_url,
        "fallback_thumbnail_url": fallback_thumb,
        "country": country_name,
        "flag": flag,
    }

    print("Forwarding parameters to Make.com Webhook...")
    res = requests.post(WEBHOOK_URL, json=payload, timeout=20)

    if res.status_code in [200, 201, 204]:
        print("✅ Delivery acknowledged by Make.com.")
        mark_article_posted(article_id)
        print(f"✅ DB Update Complete: Article ID {article_id} marked posted.")
    else:
        print(f"❌ Automation pipeline terminal error — Code {res.status_code}: {res.text}")
        sys.exit(1)


if __name__ == "__main__":
    main()
