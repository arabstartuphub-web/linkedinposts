import os
import sys
import time
import requests
import psycopg2
from datetime import datetime, timezone, date

# --- CONFIG ---
DB_URL = os.environ.get("DATABASE_URL")
GROQ_API_KEY = os.environ.get("GROQ_API_KEY")
GEMINI_API_KEYS = list(filter(None, [
    os.environ.get("GEMINI_API_KEY"),
    os.environ.get("GEMINI_API_KEY_BACKUP1"),
    os.environ.get("GEMINI_API_KEY_BACKUP2"),
]))
WEBHOOK_URL = os.environ.get("MAKE_WEBHOOK_URL")

# GitHub raw URL base for country banner images
GITHUB_BASE = "https://raw.githubusercontent.com/arabstartuphub-web/linkedinposts/main/"

# ── TEST MODE ──────────────────────────────────────────────────────────────────
# Set this to today's date when you want to test (bypasses country filter).
# Set to None to run normally.
TEST_DATE = None  # e.g. date(2026, 6, 11)
# ──────────────────────────────────────────────────────────────────────────────

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
    weekday = datetime.now(timezone.utc).weekday()
    return WEEKDAY_COUNTRY.get(weekday, "GCC")


def get_daily_article(country_name: str):
    conn = psycopg2.connect(DB_URL)
    cur = conn.cursor()

    current_utc_date = datetime.now(timezone.utc).date()

    if TEST_DATE and current_utc_date == TEST_DATE:
        print(f"⚠️  TEST MODE ON ({TEST_DATE}): Fetching most recent unposted article regardless of country.")
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
    cur = conn.cursor()
    cur.execute(
        "UPDATE articles SET linkedin_posted = TRUE WHERE id = %s;",
        (article_id,),
    )
    conn.commit()
    cur.close()
    conn.close()


def generate_with_groq(prompt: str) -> str:
    if not GROQ_API_KEY:
        raise ValueError("GROQ_API_KEY is missing from environment.")

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

    for attempt in range(3):
        try:
            response = requests.post(url, json=payload, headers=headers, timeout=20)
            if response.status_code == 200:
                return response.json()["choices"][0]["message"]["content"].strip()
            elif response.status_code in [429, 503]:
                wait = 15 * (attempt + 1)
                print(f"   [Groq Attempt {attempt + 1}/3] Status {response.status_code}. Retrying in {wait}s...")
                time.sleep(wait)
            else:
                raise RuntimeError(f"Groq API error {response.status_code}: {response.text}")
        except requests.exceptions.RequestException as e:
            wait = 15 * (attempt + 1)
            print(f"   [Groq Attempt {attempt + 1}/3] Connection issue: {e}. Retrying in {wait}s...")
            time.sleep(wait)

    raise RuntimeError("Groq exhausted all 3 retry attempts.")


def generate_with_gemini(prompt: str) -> str:
    if not GEMINI_API_KEYS:
        raise ValueError("No Gemini API keys found in environment.")

    for key_index, api_key in enumerate(GEMINI_API_KEYS):
        url = (
            "https://generativelanguage.googleapis.com/v1beta/models/"
            f"gemini-2.0-flash:generateContent?key={api_key}"
        )
        for attempt in range(3):
            try:
                response = requests.post(
                    url,
                    json={"contents": [{"parts": [{"text": prompt}]}]},
                    timeout=20
                )
                if response.status_code == 200:
                    return response.json()["candidates"][0]["content"]["parts"][0]["text"].strip()
                elif response.status_code == 429:
                    print(f"   [Gemini Key {key_index + 1}] Rate limited. Trying next key...")
                    break
                elif response.status_code == 503:
                    wait = 35 * (attempt + 1)
                    print(f"   [Gemini Key {key_index + 1} Attempt {attempt + 1}/3] Status 503. Retrying in {wait}s...")
                    time.sleep(wait)
                else:
                    raise RuntimeError(f"Gemini API error {response.status_code}: {response.text}")
            except requests.exceptions.RequestException as e:
                wait = 35 * (attempt + 1)
                print(f"   [Gemini Key {key_index + 1} Attempt {attempt + 1}/3] Connection issue: {e}. Retrying in {wait}s...")
                time.sleep(wait)

    raise RuntimeError("All Gemini API keys exhausted.")


def generate_post_content(title: str, summary: str) -> str:
    prompt = (
        f"Write a professional LinkedIn post for an Arab startup ecosystem audience.\n"
        f"Article title: {title}\n"
        f"Summary: {summary}\n\n"
        f"Requirements:\n"
        f"- Start with a compelling hook (no generic openers like 'Exciting news')\n"
        f"- 3-5 short paragraphs\n"
        f"- End with 4-6 relevant hashtags\n"
        f"- Tone: insightful, professional, engaging\n"
        f"- CRITICAL: Do NOT use markdown formatting. Never use asterisks (**) for bolding. Output pure plain text only."
    )

    print("🚀 Primary Engine: Groq (Llama-3.3)...")
    try:
        return generate_with_groq(prompt)
    except Exception as groq_error:
        print(f"⚠️  Groq failed: {groq_error}")
        print("🔄 Fallback: Google Gemini...")
        try:
            return generate_with_gemini(prompt)
        except Exception as gemini_error:
            print(f"❌ Gemini also failed: {gemini_error}")
            print("🚨 Both AI engines failed.")
            sys.exit(1)


def get_title_fallback(post_text: str, country_name: str, flag: str) -> str:
    lines = [ln.strip() for ln in post_text.splitlines() if ln.strip()]
    snippet = " ".join(lines[:2])
    if len(snippet) > 80:
        snippet = snippet[:77].rstrip() + "…"
    return f"{flag} {snippet} | {country_name}"


def main():
    country_name = get_country_for_today()
    country_data = COUNTRY_MAP.get(country_name, {"code": "GCC", "flag": "🌍"})
    flag = country_data["flag"]
    image_code = country_data["code"]
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

    # Always the GitHub banner — never the article URL
    thumbnail_url = f"{GITHUB_BASE}{image_code}.jpg"

    # ── DEBUG LOG ──────────────────────────────────────────────────────────────
    print("=" * 50)
    print(f"DEBUG country_name  : {country_name}")
    print(f"DEBUG image_code    : {image_code}")
    print(f"DEBUG source_url    : {source_url}")
    print(f"DEBUG thumbnail_url : {thumbnail_url}")
    print(f"DEBUG final_title   : {final_title}")
    print("=" * 50)
    # ──────────────────────────────────────────────────────────────────────────

    payload = {
        "text":          post_text,
        "url":           source_url or "",
        "title":         final_title,
        "thumbnail_url": thumbnail_url,
        "country":       country_name,
        "flag":          flag,
    }

    print("📤 Sending to Make.com webhook...")
    res = requests.post(WEBHOOK_URL, json=payload, timeout=30)

    if res.status_code in [200, 201, 204]:
        print("✅ Successfully sent to Make.com.")
        mark_article_posted(article_id)
    else:
        print(f"❌ Webhook failed — status {res.status_code}: {res.text}")
        sys.exit(1)


if __name__ == "__main__":
    main()
