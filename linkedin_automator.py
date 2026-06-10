import os
import sys
import psycopg2
from google import genai  # Modernized Google GenAI SDK
import requests

# 1. Load Environment Variables
DB_URL = os.environ.get("DATABASE_URL")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
LINKEDIN_ACCESS_TOKEN = os.environ.get("LINKEDIN_ACCESS_TOKEN")
LINKEDIN_ORG_ID = os.environ.get("LINKEDIN_ORG_ID")

if not all([DB_URL, GEMINI_API_KEY, LINKEDIN_ACCESS_TOKEN, LINKEDIN_ORG_ID]):
    print("Error: Missing one or more environment variables.")
    sys.exit(1)

# Initialize the modern Gemini Client
ai_client = genai.Client(api_key=GEMINI_API_KEY)

def get_articles_to_post():
    """
    Fetches exactly ONE unposted article for EACH country.
    Updated to match your exact Neon table schema column names.
    """
    query = """
        SELECT DISTINCT ON (country) id, title, summary, source_url, country 
        FROM articles 
        WHERE linkedin_posted = FALSE 
        ORDER BY country, created_at DESC;
    """
    conn = psycopg2.connect(DB_URL)
    cur = conn.cursor()
    cur.execute(query)
    articles = cur.fetchall()
    cur.close()
    conn.close()
    return articles

def generate_linkedin_content(title, summary, country):
    """Uses Gemini 1.5 Flash to generate a snappy, readable LinkedIn post."""
    prompt = f"""
    You are the social media voice for 'Arabian Startups Ecosystem', a platform highlighting startup ecosystems in GCC countries.
    Draft an engaging, insightful LinkedIn post based on this recent news from {country}.
    
    Title: {title}
    Context/Summary: {summary}
    
    Guidelines:
    - Keep it crisp and punchy (use clean line breaks for scannability).
    - Summarize the key impact or takeaway.
    - Include 2-3 relevant hashtags (e.g., #{country}Startups, #GCCStartups).
    - Do not use placeholders. Focus entirely on the hook and summary.
    - End with a brief engaging question or line to spark discussion.
    """
    
    response = ai_client.models.generate_content(
        model='gemini-1.5-flash',
        contents=prompt,
    )
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
    print("Fetching new articles from Neon DB...")
    try:
        articles = get_articles_to_post()
    except Exception as e:
        print(f"Database Query Error: {e}")
        print("Please verify your table column names match the script selection.")
        sys.exit(1)
    
    if not articles:
        print("No new unposted articles found for any GCC country.")
        return

    print(f"Found {len(articles)} country updates to post.")

    for art_id, title, summary, source_url, country in articles:
        print(f"\n--- Processing: {country} ---")
        print("Generating post copy via Gemini...")
        linkedin_text = generate_linkedin_content(title, summary, country)
        
        print(f"Publishing to LinkedIn page...")
        success = post_to_linkedin(linkedin_text, source_url)
        
        if success:
            update_db_status(art_id)
            print(f"Success! Database updated for {country} article.")
        else:
            print(f"Skipping DB flag update due to LinkedIn API failure.")

if __name__ == "__main__":
    main()
