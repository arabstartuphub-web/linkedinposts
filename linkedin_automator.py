import os
import sys
import datetime
import psycopg2
import requests
import warnings
import re

# Suppress Google deprecation/FutureWarnings to keep logs clean
warnings.filterwarnings("ignore", category=FutureWarning)

import google.generativeai as genai

# 1. Load and Verify Environment Variables
DB_URL = os.environ.get("DATABASE_URL")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
WEBHOOK_URL = os.environ.get("MAKE_WEBHOOK_URL")

if not all([DB_URL, GEMINI_API_KEY, WEBHOOK_URL]):
    print("Error: Missing one or more required environment variables (DATABASE_URL, GEMINI_API_KEY, MAKE_WEBHOOK_URL).")
    sys.exit(1)

# Configure Gemini Connection
genai.configure(api_key=GEMINI_API_KEY)

# Daily Schedule Country Mapping
DAY_MAP = {
    "Monday": "Saudi Arabia",
    "Tuesday": "UAE",
    "Wednesday": "Qatar",
    "Thursday": "Kuwait",
    "Friday": "Oman",
    "Saturday": "Bahrain",
    "Sunday": "GCC"
}

def get_daily_article(country_name):
    """Fetches exactly ONE latest unposted article for the specified country."""
    query = """
        SELECT id, title, summary, source_url, country 
        FROM articles 
        WHERE linkedin_posted = FALSE AND country = %s
        ORDER BY created_at DESC
        LIMIT 1;
    """
    conn = psycopg2.connect(DB_URL)
    cur = conn.cursor()
    cur.execute(query, (country_name,))
    article = cur.fetchone()
    cur.close()
    conn.close()
    return article

def extract_og_image(url):
    """Scrapes the target webpage to pull the Open Graph thumbnail image link."""
    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/115.0.0.0 Safari/537.36"
        }
        res = requests.get(url, headers=headers, timeout=8)
        if res.status_code == 200:
            html = res.text
            # Regex patterns to locate og:image property content
            match = re.search(r'<meta\s+[^>]*property=["\']og:image["\'][^>]*content=["\']([^"\']+)["\']', html, re.IGNORECASE)
            if not match:
                match = re.search(r'<meta\s+[^>]*content=["\']([^"\']+)["\'][^>]*property=["\']og:image["\']', html, re.IGNORECASE)
            if match:
                return match.group(1)
    except Exception as e:
        print(f"Warning: Could not extract Open Graph thumbnail from page: {e}")
    return None

def generate_linkedin_content(model_name, title, summary, country):
    """Uses Gemini to generate structured LinkedIn post text."""
    model = genai.GenerativeModel(model_name)
    
    prompt = f"""
    You are the social media voice for 'Arabian Startups Ecosystem', a platform highlighting startup ecosystems in GCC countries.
    Draft an engaging, insightful LinkedIn post based on this recent news from {country}.
    
    Title: {title}
    Context/Summary: {summary}
    
    Guidelines:
    - Keep it crisp and punchy (use clean line breaks for scannability).
    - Summarize the key impact or takeaway.
    - Include 2-3 relevant hashtags (e.g., #{country.replace(' ', '')}Startups, #GCCStartups).
    - Do not use placeholders. Focus entirely on the hook and summary.
    - End with a brief engaging question or line to spark discussion.
    """
    
    response = model.generate_content(prompt)
    return response.text

def post_to_linkedin_via_webhook(text, article_url, title, thumbnail_url):
    """Routes the complete bundle data downstream to the Make.com Webhook."""
    payload = {
        "text": text,
        "url": article_url,
        "title": title,
        "thumbnail_url": thumbnail_url
    }
    
    try:
        res = requests.post(WEBHOOK_URL, json=payload)
        if res.status_code in [200, 201]:
            return True
        else:
            print(f"Webhook Bridge Error {res.status_code}: {res.text}")
            return False
    except Exception as e:
        print(f"Failed to connect to Webhook Bridge: {e}")
        return False

def update_db_status(article_id):
    """Marks the specific article row as posted in Neon DB."""
    conn = psycopg2.connect(DB_URL)
    cur = conn.cursor()
    cur.execute("UPDATE articles SET linkedin_posted = TRUE WHERE id = %s", (article_id,))
    conn.commit()
    cur.close()
    conn.close()

def main():
    current_day = datetime.datetime.now().strftime('%A')
    target_country = DAY_MAP.get(current_day)
    
    print(f"Today is {current_day}. Target ecosystem: {target_country}")
    
    try:
        article = get_daily_article(target_country)
    except Exception as e:
        print(f"Database Query Error: {e}")
        sys.exit(1)
        
    if not article:
        print(f"No new unposted articles found for {target_country} today. Skipping execution.")
        return

    art_id, title, summary, source_url, country = article
    print(f"Found article ID {art_id}: '{title}'")
    
    print(f"Inspecting source URL for Open Graph preview card assets...")
    scraped_thumb = extract_og_image(source_url)
    
    if scraped_thumb:
        thumbnail_url = scraped_thumb
        print(f"Scraped thumbnail image URL: {thumbnail_url}")
    else:
        # High-quality fallback stock workspace thumbnail to ensure HTTP module never catches an empty bundle parameter
        thumbnail_url = "https://images.unsplash.com/photo-1522071820081-009f0129c71c?auto=format&fit=crop&w=1200&h=627&q=80"
        print(f"No custom image found on source site. Using professional fallback: {thumbnail_url}")
    
    models_to_try = ['gemini-2.5-flash', 'gemini-3.5-flash', 'gemini-3.1-flash-lite', 'gemini-2.0-flash']
    linkedin_text = None
    
    for model_name in models_to_try:
        try:
            print(f"Attempting generation via {model_name}...")
            linkedin_text = generate_linkedin_content(model_name, title, summary, country)
            break
        except Exception as e:
            error_str = str(e).lower()
            if "429" in error_str or "quota" in error_str or "limit" in error_str:
                print(f"⚠️ {model_name} rate limit hit. Testing next fallback variant...")
                continue
            else:
                print(f"Generation Error under {model_name}: {e}")
                sys.exit(1)
                
    if not linkedin_text:
        print("Error: All fallback Gemini models exhausted for today.")
        sys.exit(1)
        
    print(f"Shipping compiled bundle over to Make.com Webhook...")
    success = post_to_linkedin_via_webhook(linkedin_text, source_url, title, thumbnail_url)
    
    if success:
        update_db_status(art_id)
        print(f"Success! Webhook fired and Database updated.")
    else:
        print(f"Execution stopped due to Webhook Bridge rejection.")

if __name__ == "__main__":
    main()
