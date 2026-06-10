import os
import sys
import requests
import psycopg2
import json

# --- CONFIG ---
DB_URL = os.environ.get("DATABASE_URL")
API_KEY = os.environ.get("GEMINI_API_KEY") # Ensure this is your Google AI Studio API Key
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

def generate_post_content(title):
    """Direct REST API call to Gemini - No SDK required."""
    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={API_KEY}"
    payload = {
        "contents": [{
            "parts": [{"text": f"Write a professional LinkedIn post about: {title}. Include hashtags."}]
        }]
    }
    response = requests.post(url, json=payload)
    
    if response.status_code == 200:
        data = response.json()
        return data['candidates'][0]['content']['parts'][0]['text']
    else:
        print(f"Gemini API Error: {response.text}")
        sys.exit(1)

def main():
    # 1. Setup country context
    country_name = "Qatar" 
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
    
    # 4. Content Generation
    post_text = generate_post_content(db_title)
    
    # 5. Send to Make.com Webhook
    payload = {
        "text": post_text,
        "url": source_url,
        "title": final_title,
        "thumbnail_url": final_thumb
    }
    
    res = requests.post(WEBHOOK_URL, json=payload)
    if res.status_code in [200, 201]:
        print("Success! Data sent to Make.com.")
    else:
        print(f"Webhook failed with status {res.status_code}")

if __name__ == "__main__":
    main()
