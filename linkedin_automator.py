import os
import sys
import requests
import json
import psycopg2
import google.generativeai as genai

# --- CONFIG ---
DB_URL = os.environ.get("DATABASE_URL")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
WEBHOOK_URL = os.environ.get("MAKE_WEBHOOK_URL")

# GitHub raw URL base
GITHUB_BASE = "https://raw.githubusercontent.com/arabstartuphub-web/linkedinposts/main/"

COUNTRY_MAP = {
    "Saudi Arabia": {"code": "KSA", "flag": "🇸🇦"},
    "UAE": {"code": "UAE", "flag": "🇦🇪"},
    "Qatar": {"code": "QATAR", "flag": "🇶🇦"},
    "Kuwait": {"code": "KUWAIT", "flag": "🇰🇼"},
    "Oman": {"code": "OMAN", "flag": "🇴🇲"},
    "Bahrain": {"code": "BAHRAIN", "flag": "🇧🇭"},
    "GCC": {"code": "GCC", "flag": "🌍"}
}

def get_daily_article(country_name):
    conn = psycopg2.connect(DB_URL)
    cur = conn.cursor()
    cur.execute("SELECT title, summary, source_url FROM articles WHERE linkedin_posted = FALSE AND country = %s LIMIT 1;", (country_name,))
    article = cur.fetchone()
    cur.close()
    conn.close()
    return article

def main():
    # 1. Setup country context
    country_name = "Qatar" # Ensure this matches your DB
    country_data = COUNTRY_MAP.get(country_name, {"code": "GCC", "flag": "🌍"})
    
    article = get_daily_article(country_name)
    if not article:
        print("No article found.")
        return
    
    db_title, summary, source_url = article
    
    # 2. Dynamic Title Formatting
    final_title = f"{country_data['flag']} {db_title} - {country_name}"
    
    # 3. GitHub Image Path
    final_thumb = f"{GITHUB_BASE}{country_data['code']}.jpg"
    
    # 4. Content Generation (Correct SDK usage)
    genai.configure(api_key=GEMINI_API_KEY)
    model = genai.GenerativeModel('gemini-1.5-flash')
    response = model.generate_content(f"Write a LinkedIn post about: {db_title}")
    
    # 5. Send to Webhook
    payload = {
        "text": response.text,
        "url": source_url,
        "title": final_title,
        "thumbnail_url": final_thumb
    }
    
    res = requests.post(WEBHOOK_URL, json=payload)
    if res.status_code in [200, 201]:
        print("Payload sent successfully.")
    else:
        print(f"Webhook failed with status {res.status_code}")

if __name__ == "__main__":
    main()
