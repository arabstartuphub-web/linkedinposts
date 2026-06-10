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
WEBHOOK_URL = os.environ.get("MAKE_WEBHOOK_URL")

if not all([DB_URL, GEMINI_API_KEY, WEBHOOK_URL]):
    print("Error: Missing one or more required environment variables (DATABASE_URL, GEMINI_API_KEY, MAKE_WEBHOOK_URL).")
    sys.exit(1)

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

def post_to_linkedin_via_webhook(text, article_url):
    """Routes the generated content to your Make.com bridge to publish on your company page."""
    payload = {
        "text": text,
        "url": article_url
    }
    
    try:
        res = requests.post(WEBHOOK_URL, json=payload)
        # Accept both 200 OK and 201 Created responses as successes
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
        
    print(f"Routing generated content to Make.com Webhook...")
    success = post_to_linkedin_via_webhook(linkedin_text, source_url)
    
    if success:
        update_db_status(art_id)
        print(f"Success! Webhook fired and Database updated. Post has been passed to your company page.")
    else:
        print(f"Execution stopped due to Webhook Bridge rejection.")

if __name__ == "__main__":
    main()
