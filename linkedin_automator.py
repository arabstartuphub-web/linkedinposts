import os
import sys
import datetime
import psycopg2
import requests
import warnings

# Suppress the Google deprecation/FutureWarnings to keep your GitHub Action logs clean
warnings.filterwarnings("ignore", category=FutureWarning)

import google.generativeai as genai

# 1. Load Environment Variables
DB_URL = os.environ.get("DATABASE_URL")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
LINKEDIN_ACCESS_TOKEN = os.environ.get("LINKEDIN_ACCESS_TOKEN")
RAW_ORG_ID = os.environ.get("LINKEDIN_ORG_ID")

if not all([DB_URL, GEMINI_API_KEY, LINKEDIN_ACCESS_TOKEN, RAW_ORG_ID]):
    print("Error: Missing one or more environment variables.")
    sys.exit(1)

# Clean and format the LinkedIn Organization ID safely
CLEAN_ORG_ID = RAW_ORG_ID.strip().replace('"', '').replace("'", "")
if not CLEAN_ORG_ID.startswith("urn:li:organization:"):
    LINKEDIN_ORG_ID = f"urn:li:organization:{CLEAN_ORG_ID}"
else:
    LINKEDIN_ORG_ID = CLEAN_ORG_ID

# Configure Gemini
genai.configure(api_key=GEMINI_API_KEY)

# Daily Schedule Mapping: Allocates one specific tag to each day of the week
DAY_MAP = {
    "Monday": "Saudi Arabia",
    "Tuesday": "UAE",
    "Wednesday": "Qatar",
    "Thursday": "Kuwait",
    "Friday": "Oman",
    "Saturday": "Bahrain",
    "Sunday": "GCC"
}

def get_best_available_model():
    """Dynamically scans your API key to find an active text generation model."""
    preferred_models = ['gemini-2.5-flash', 'gemini-2.0-flash', 'gemini-1.5-flash']
    try:
        available_models = []
        for m in genai.list_models():
            if 'generateContent' in m.supported_generation_methods:
                available_models.append(m.name.replace('models/', ''))
        
        for model_name in preferred_models:
            if model_name in available_models:
                return model_name
        if available_models:
            return available_models[0]
    except Exception:
        pass
    return 'gemini-2.5-flash'

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

def generate_linkedin_content(model_name, title, summary, country):
    """Uses Gemini to generate a structured LinkedIn post copy."""
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

def post_to_linkedin(text, article_url):
    """Sends the structured payload to the LinkedIn UGC API."""
    url = "https://api.linkedin.com/v2/ugcPosts"
    headers = {
        "Authorization": f"Bearer {LINKEDIN_ACCESS_TOKEN}",
        "X-Restli-Protocol-Version": "2.0.0",
        "Content-Type": "application/json"
    }
    
    post_data = {
        "author": LINKEDIN_ORG_ID,
        "lifecycleState": "PUBLISHED",
        "specificContent": {
            "com.linkedin.ugc.ShareContent": {
                "shareCommentary": {"text": text},
                "shareMediaCategory": "ARTICLE",
                "media": [{
                    "status": "READY",
                    "originalUrl": article_url,
                    "title": {"text": "Read the full update on Arabian Startups Ecosystem"}
                }]
            }
        },
        "visibility": {"com.linkedin.ugc.MemberNetworkVisibility": "PUBLIC"}
    }
    
    res = requests.post(url, headers=headers, json=post_data)
    if res.status_code == 201:
        return True
    else:
        print(f"LinkedIn API Error {res.status_code}: {res.text}")
        return False

def update_db_status(article_id):
    """Marks the specific article row as posted."""
    conn = psycopg2.connect(DB_URL)
    cur = conn.cursor()
    cur.execute("UPDATE articles SET linkedin_posted = TRUE WHERE id = %s", (article_id,))
    conn.commit()
    cur.close()
    conn.close()

def main():
    # Detect the day of the week
    current_day = datetime.datetime.now().strftime('%A')
    target_country = DAY_MAP.get(current_day)
    
    print(f"Today is {current_day}. Target ecosystem: {target_country}")
    print(f"Querying Neon DB for the latest unposted update...")
    
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
    
    selected_model = get_best_available_model()
    print(f"Generating post copy via {selected_model}...")
    
    try:
        linkedin_text = generate_linkedin_content(selected_model, title, summary, country)
    except Exception as e:
        print(f"Gemini Generation Error: {e}")
        sys.exit(1)
        
    print(f"Publishing to LinkedIn page using author handle: {LINKEDIN_ORG_ID}...")
    success = post_to_linkedin(linkedin_text, source_url)
    
    if success:
        update_db_status(art_id)
        print(f"Success! Database updated. Post is live for {country}.")
    else:
        print(f"Execution stopped due to LinkedIn API rejection.")

if __name__ == "__main__":
    main()
