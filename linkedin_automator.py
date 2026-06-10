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
    """Executes an instant REST request with a fast timeout window."""
    url = (
        "https://generativelanguage.googleapis.com/v1beta/models/"
        f"{model_name}:generateContent?key={api_key}"
    )
    payload = {"contents": [{"parts": [{"text": prompt}]}]}
    
    # 10 second timeout ensures GitHub Action skips bad/exhausted endpoints instantly
    response = requests.post(url, json=payload, timeout=10)
    
    if response.status_code == 200:
        data = response.json()
        return data["candidates"][0]["content"]["parts"][0]["text"].strip()
    else:
        raise RuntimeError(f"Status {response.status_code}")


def generate_post_content(title: str, summary: str) -> str:
    """
    Priority Matrix Architecture:
    Tier 1: Try gemini-3.5-flash across Account 1 -> Account 2 -> Account 3 sequentially.
    Tier 2: Only if all Tier 1 keys fail, try gemini-3.1-flash-lite across Account 1 -> Account 2 -> Account 3.
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

    models = ["gemini-3.5-flash", "gemini-3.1-flash-lite"]

    gemini_keys = [
        os.environ.get("GEMINI_API_KEY"),
        os.environ.get("GEMINI_API_KEY_BACKUP1"),
        os.environ.get("GEMINI_API_KEY_BACKUP2")
    ]
    active_keys = [k for k in gemini_keys if k]

    if not active_keys:
        print("❌ Critical Error: No Gemini API keys found in runtime environment.")
        return ""

    for model in models:
        print(f"--- Sweeping Priority Tier Model: {model} ---")
        for idx, key in enumerate(active_keys):
            account_label = "Primary" if idx == 0 else f"Backup {idx}"
            print(f"Requesting generation via {model} on {account_label} Account...")
            
            try:
                return generate_with_gemini(key, model, prompt)
            except Exception as e:
                print(f"⚠️ [{account_label}] failed for {model} ({e}). Moving to next key instantly.")
                continue

    return ""


def main():
    # 1. Determine targeted country context
    country_name = get_country_for_today()
    country_data = COUNTRY_MAP.get(country_name, {"code": "GCC", "flag": "🌍"})
    flag = country_data["flag"]
    print(f"Today's localized target: {country_name} {flag}")

    # 2. Extract article source asset
    article = get_daily_article(country_name)
    if not article:
        print(f"No unposted entries available for {country_name}. Shutting down execution.")
        sys.exit(0)

    article_id, db_title, summary, source_url = article
    print(f"Processing database item ID {article_id}: {db_title or '(Blank Title)'}")

    # 3. Trigger prioritized Gemini generation sequence
    post_text = generate_post_content(db_title or summary or country_name, summary or "")
    
    # Safety fallback text block if all 3 Gemini API accounts are completely dead
    if not post_text:
        print("⚠️ Warning: Complete Gemini API lockout. Constructing fallback text.")
        post_text = f"Check out the latest updates from the startup ecosystem in {country_name}! {flag}\n\n{summary or db_title or ''}"

    # 4. RULE FIX: Extract first 2 lines of post text + flag + country name for final title
    lines = [ln.strip() for ln in post_text.splitlines() if ln.strip()]
    title_snippet = " ".join(lines[:2]) if len(lines) >= 2 else (lines[0] if lines else (db_title or "Update"))
    if len(title_snippet) > 80:
        title_snippet = title_snippet[:77] + "…"
    final_title = f"{flag} {title_snippet} | {country_name}"
    print(f"Resolved Title Payload: {final_title}")

    # 5. RULE FIX: If source URL is empty/missing, assign matching raw GitHub image
    fallback_thumb = f"{GITHUB_BASE}{country_data['code']}.jpg"
    if source_url and source_url.strip().startswith("http"):
        thumbnail_url = source_url.strip()
    else:
        thumbnail_url = fallback_thumb
    print(f"Resolved Thumbnail URL Payload: {thumbnail_url}")

    # 6. Post structured parameters directly to Make.com Webhook
    payload = {
        "text": post_text,
        "url": source_url or "",
        "title": final_title,
        "thumbnail_url": thumbnail_url,
        "country": country_name,
        "flag": flag,
    }

    print("Forwarding payload package down to Make.com...")
    res = requests.post(WEBHOOK_URL, json=payload, timeout=20)

    if res.status_code in [200, 201, 204]:
        print("✅ Transmission successfully acknowledged by Make.com module.")
        mark_article_posted(article_id)
        print(f"✅ DB Update Verified: Article ID {article_id} locked down.")
    else:
        print(f"❌ Target terminal error — Code {res.status_code}: {res.text}")
        sys.exit(1)


if __name__ == "__main__":
    main()
