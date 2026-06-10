import os
import sys
import time
import requests
import psycopg2
from datetime import datetime, timezone, date

# --- CONFIG ---
DB_URL = os.environ.get("DATABASE_URL")
GROQ_API_KEY = os.environ.get("GROQ_API_KEY")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
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
    
    # --- TEMPORARY TEST RULE FOR TODAY ---
    # If today is June 10, 2026, pull the newest available article regardless of country.
    current_utc_date = datetime.now(timezone.utc).date()
    test_date = date(2026, 6, 10)
    
    if current_utc_date == test_date:
        print("⚠️ TODAY ONLY: Bypassing country filter to fetch the most recent unposted article for testing.")
        cur.execute(
            """
            SELECT id, title, summary, source_url
            FROM articles
            WHERE linkedin_posted = FALSE
            ORDER BY created_at DESC
            LIMIT 1;
            """
        )
    else:
        # --- PERMANENT AUTOMATION RULE (Resumes automatically tomorrow) ---
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


def generate_with_groq(prompt: str) -> str:
    """Call Groq Cloud REST API using Llama 3.3 70B."""
    if not GROQ_API_KEY:
        raise ValueError("GROQ_API_KEY is not configured in environment secrets.")
        
    url = "https://api.groq.com/openai/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {GROQ_API_KEY}",
        "Content-Type": "application/json"
    }
    payload = {
        "model": "llama-3.3-70b-versatile",
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.7
    }
    
    response = requests.post(url, json=payload, headers=headers, timeout=20)
    if response.status_code == 200:
        return response.json()["choices"][0]["message"]["content"].strip()
    else:
        raise RuntimeError(f"Groq API responded with status {response.status_code}: {response.text}")


def generate_with_gemini(prompt: str) -> str:
    """Call Gemini REST API using Gemini 2.0 Flash."""
    if not GEMINI_API_KEY:
        raise ValueError("GEMINI_API_KEY is not configured in environment secrets.")
        
    url = (
        "https://generativelanguage.googleapis.com/v1beta/models/"
        f"gemini-2.0-flash:generateContent?key={GEMINI_API_KEY}"
    )
    payload = {"contents": [{"parts": [{"text": prompt}]}]}
    
    response = requests.post(url, json=payload, timeout=20)
    if response.status_code == 200:
        data = response.json()
        return data["candidates"][0]["content"]["parts"][0]["text"].strip()
    else:
        raise RuntimeError(f"Gemini API responded with status {response.status_code}: {response.text}")


def generate_post_content(title: str, summary: str) -> str:
    """Attempts generation via Groq first; cleanly cascades to Gemini on failure."""
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

    # --- ATTEMPT 1: GROQ ---
    print("🚀 Attempting content generation using Primary Model: Groq (Llama-3.3)...")
    try:
        return generate_with_groq(prompt)
    except Exception as groq_error:
        print(f"⚠️ Primary Model (Groq) failed: {groq_error}")
        print("🔄 Gracefully routing to Secondary Fallback Model: Google Gemini...")
        
        # --- ATTEMPT 2: GEMINI FALLBACK ---
        try:
            return generate_with_gemini(prompt)
        except Exception as gemini_error:
            print(f"❌ Fallback Model (Gemini) also failed: {gemini_error}")
            print("🚨 Critical: Both AI generation models are currently unreachable.")
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
    print(f"Today's scheduled country: {country_name} {flag}")

    article = get_daily_article(country_name)
    if not article:
        print(f"No unposted articles found. Exiting.")
        sys.exit(0)

    article_id, db_title, summary, source_url = article
    print(f"Article ID {article_id}: {db_title or '(no title)'}")

    post_text = generate_post_content(db_title or summary or country_name, summary or "")

    if db_title and db_title.strip():
        final_title = f"{flag} {db_title.strip()} | {country_name}"
    else:
        final_title = get_title_fallback(post_text, country_name, flag)
    print(f"Final title: {final_title}")

    # Always use the clean country graphic JPG file hosted on GitHub
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
