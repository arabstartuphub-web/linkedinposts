import os
import sys
import datetime
import psycopg2
import requests
import warnings
import re

# Suppress Google deprecation/FutureWarnings to keep your GitHub Action execution logs clean
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
    """Fetches exactly ONE latest unposted article for the specified country from Neon DB."""
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
    """Scrapes the target webpage to pull the Open Graph thumbnail image link with robust fallbacks."""
    try:
        # Use a realistic desktop browser header to prevent premium sites like Forbes from blocking the request
        headers = {
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.5"
        }
        res = requests.get(url, headers=headers, timeout=10)
        if res.status_code == 200:
            html = res.text
            
            # 1. Primary Check: Standard og:image properties
            img_match = re.search(r'<meta\s+[^>]*property=["\']og:image["\'][^>]*content=["\']([^"\']+)["\']', html, re.IGNORECASE)
            if not img_match:
                img_match = re.search(r'<meta\s+[^>]*content=["\']([^"\']+)["\'][^>]*property=["\']og:image["\']', html, re.IGNORECASE)
            
            # 2. Secondary Check: Twitter image card if og:image is missing
            if not img_match:
                img_match = re.search(r'<meta\s+[^>]*name=["\']twitter:image["\']\s+[^>]*content=["\']([^"\']+)["\']', html, re.IGNORECASE)
                
            if img_match:
                img_url = img_match.group(1).strip()
                # Fix relative protocol paths (e.g., //cdn.com/img.jpg -> https://cdn.com/img.jpg)
                if img_url.startswith("//"):
                    img_url = "https:" + img_url
                return img_url
    except Exception as e:
        print(f"Warning: Could not scrape Open Graph thumbnail from page: {e}")
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
    """Routes the compiled post data and asset links over to Make.com."""
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

    art_id, db_title, summary, source_url, country = article
    print(f"Found article ID {art_id}: '{db_title}'")
    
    # Clean and enforce a solid article title backup string
    if db_title and db_title.strip():
        final_title = db_title.strip()
    else:
        final_title = "GCC Startup Ecosystem Update"

    print(f"Inspecting source webpage for thumbnail elements...")
    scraped_thumb = extract_og_image(source_url)
    
    # Safe validation check: If scraper comes up completely empty, assign fallback image URL
    if scraped_thumb and scraped_thumb.strip():
        final_thumbnail_url = scraped_thumb.strip()
        print(f"Successfully scraped thumbnail URL: {final_thumbnail_url}")
    else:
        # Premium fallback workspace asset link so Make never catches an empty variable block
        final_thumbnail_url = "https://images.unsplash.com/photo-1522071820081-009f0129c71c?auto=format&fit=crop&w=1200&h=627&q=80"
        print(f"Using default premium ecosystem preview asset fallback: {final_thumbnail_url}")
    
    # Roll-over fallback array to safeguard operations against API account limit variations
    models_to_try = ['gemini-2.5-flash', 'gemini-3.5-flash', 'gemini-3.1-flash-lite', 'gemini-2.0-flash']
    linkedin_text = None
    
    for model_name in models_to_try:
        try:
            print(f"Attempting content generation via {model_name}...")
            linkedin_text = generate_linkedin_content(model_name, final_title, summary, country)
            break
        except Exception as e:
            error_str = str(e).lower()
            if "429" in error_str or "quota" in error_str or "limit" in error_str:
                print(f"⚠️ {model_name} rate thresholds hit. Moving to alternative model selection...")
                continue
            else:
                print(f"Generation aborted under model variant {model_name}: {e}")
                sys.exit(1)
                
    if not linkedin_text:
        print("Fatal Error: All fallback engine variants are currently non-responsive or rate-limited.")
        sys.exit(1)
        
    print(f"Forwarding payload bundle parameters down to Make.com integration stream...")
    success = post_to_linkedin_via_webhook(linkedin_text, source_url, final_title, final_thumbnail_url)
    
    if success:
        update_db_status(art_id)
        print(f"Success! System data published cleanly.")
    else:
        print(f"Pipeline tracking stopped due to external endpoint connection errors.")

if __name__ == "__main__":
    main()
