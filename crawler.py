import os
import json
import time
import requests
from datetime import datetime, timezone, timedelta

# ── CONFIG FROM ENVIRONMENT ──
SUPABASE_URL  = os.environ['SUPABASE_URL']
SUPABASE_KEY  = os.environ['SUPABASE_KEY']
GROQ_API_KEY  = os.environ['GROQ_API_KEY']
BREVO_API_KEY = os.environ['BREVO_API_KEY']
ALERT_EMAIL   = os.environ['ALERT_EMAIL']
FROM_EMAIL    = os.environ.get('FROM_EMAIL', 'editor@edureport.in')
GNEWS_KEY     = os.environ.get('GNEWS_KEY', '')

CATEGORIES = [
    ("education india",                 "Higher Education"),
    ("school CBSE NCERT india",         "K-12"),
    ("JEE NEET CUET exam result india", "Exams & Results"),
    ("education policy UGC NEP india",  "Policy & Regulatory"),
    ("edtech education technology india","EdTech"),
    ("university ranking NIRF india",   "Rankings & Awards"),
]

SUPABASE_HEADERS = {
    "apikey":        SUPABASE_KEY,
    "Authorization": f"Bearer {SUPABASE_KEY}",
    "Content-Type":  "application/json",
    "Prefer":        "return=minimal",
}

# ── STEP 1: FETCH NEWS FROM GNEWS ──
def fetch_gnews(query, max_results=5):
    if not GNEWS_KEY:
        return []
    try:
        r = requests.get(
            "https://gnews.io/api/v4/search",
            params={
                "q":       query,
                "token":   GNEWS_KEY,
                "lang":    "en",
                "country": "in",
                "in":      "title",
                "max":     max_results,
                "sortby":  "publishedAt",
            },
            timeout=10
        )
        if not r.ok:
            print(f"GNews error {r.status_code}: {r.text[:100]}")
            return []
        data = r.json()
        return data.get("articles", [])
    except Exception as e:
        print(f"GNews fetch error: {e}")
        return []

# ── STEP 2: CHECK IF STORY ALREADY EXISTS ──
def story_exists(title):
    # Check by title similarity (first 80 chars)
    short = title[:80].replace("'", "''")
    r = requests.get(
        f"{SUPABASE_URL}/rest/v1/articles",
        headers=SUPABASE_HEADERS,
        params={"select": "id", "title": f"ilike.*{short[:40]}*", "limit": "1"},
    )
    if r.ok:
        data = r.json()
        return len(data) > 0
    return False

# ── STEP 3: REWRITE WITH GROQ ──
def rewrite_with_groq(title, description, category):
    prompt = f"""You are a senior journalist at EduReport.in, India's leading education news platform.

Rewrite the following story as a complete, original, publication-ready article.

HEADLINE: {title}
DESCRIPTION: {description}
CATEGORY: {category}

REQUIREMENTS:
- 400-500 words of completely original prose
- Strong opening paragraph that leads with the most important fact
- 2 H2 subheadings to structure the article
- Professional journalism style — like The Hindu or Business Standard
- Do NOT fabricate quotes or attribute invented statements to real named individuals
- HTML tags: <p> for paragraphs, <h2> for subheadings

Return ONLY a JSON object — nothing before or after the curly braces:
{{
  "title": "Rewritten SEO headline under 90 chars",
  "deck": "One punchy sentence under 160 chars",
  "body": "<p>...</p><h2>...</h2><p>...</p>",
  "meta_title": "SEO title under 60 chars",
  "meta_description": "SEO description under 160 chars",
  "tags": ["tag1","tag2","tag3","tag4"]
}}"""

    try:
        r = requests.post(
            "https://api.groq.com/openai/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {GROQ_API_KEY}",
                "Content-Type":  "application/json",
            },
            json={
                "model":       "openai/gpt-oss-20b",
                "max_tokens":  3000,
                "temperature": 0.7,
                "messages":    [{"role": "user", "content": prompt}],
            },
            timeout=60,
        )
        if not r.ok:
            print(f"Groq error {r.status_code}: {r.text[:200]}")
            return None

        text = r.json()["choices"][0]["message"]["content"]
        # Strip think tags
        import re
        text = re.sub(r'<think>[\s\S]*?</think>', '', text).strip()
        # Extract JSON
        match = re.search(r'\{[\s\S]*\}', text)
        if not match:
            print(f"No JSON in Groq response: {text[:100]}")
            return None
        return json.loads(match.group())

    except Exception as e:
        print(f"Groq rewrite error: {e}")
        return None

# ── STEP 4: SAVE TO SUPABASE ──
def save_to_supabase(article, source_url, category):
    import re
    slug = re.sub(r'[^a-z0-9]+', '-', article['title'].lower()).strip('-')[:100]
    slug = slug + '-' + str(int(time.time()))[-6:]  # ensure uniqueness

    bg_map = {
        'Higher Education':    'bg-higher',
        'K-12':                'bg-k12',
        'Policy & Regulatory': 'bg-policy',
        'Exams & Results':     'bg-exam',
        'EdTech':              'bg-edtech',
        'International':       'bg-intl',
        'Rankings & Awards':   'bg-rank',
    }

    word_count = len(article['body'].replace('<', ' <').split())
    read_time  = max(3, word_count // 200)

    payload = {
        "title":            article['title'],
        "slug":             slug,
        "deck":             article.get('deck', ''),
        "body":             article['body'],
        "category":         category,
        "tags":             article.get('tags', []),
        "status":           "draft",
        "ai_generated":     True,
        "author":           "EduReport Crawler",
        "read_time":        read_time,
        "source_url":       source_url,
        "source_name":      "Auto-Crawler",
        "bg_class":         bg_map.get(category, 'bg-higher'),
        "img_label":        "",
        "featured_position":"none",
        "meta_title":       article.get('meta_title', article['title'])[:60],
        "meta_description": article.get('meta_description', article.get('deck', ''))[:160],
        "published_at":     None,
    }

    r = requests.post(
        f"{SUPABASE_URL}/rest/v1/articles",
        headers=SUPABASE_HEADERS,
        json=payload,
    )
    return r.status_code in (200, 201)

# ── STEP 5: SEND BREVO ALERT ──
def send_alert(new_stories):
    if not new_stories:
        return

    rows = ''.join([
        f"<tr><td style='padding:8px;border-bottom:1px solid #eee;'>"
        f"<strong>{s['title']}</strong><br>"
        f"<span style='color:#666;font-size:12px;'>{s['category']} · {s['source']}</span>"
        f"</td></tr>"
        for s in new_stories
    ])

    html = f"""
    <div style="font-family:Georgia,serif;max-width:600px;margin:0 auto;">
      <div style="background:#111;padding:20px 28px;border-bottom:3px solid #d95f0e;">
        <div style="color:#fff;font-size:22px;font-weight:bold;">EduReport<span style="color:#d95f0e;">.in</span></div>
        <div style="color:rgba(255,255,255,0.4);font-size:11px;letter-spacing:2px;text-transform:uppercase;margin-top:4px;">Auto Crawler Alert</div>
      </div>
      <div style="padding:24px 28px;background:#fff;">
        <p style="font-size:15px;color:#333;">
          <strong>{len(new_stories)} new {'story' if len(new_stories)==1 else 'stories'}</strong> 
          found and saved as drafts — {datetime.now(timezone(timedelta(hours=5, minutes=30))).strftime('%d %b %Y, %I:%M %p')} IST
        </p>
        <table style="width:100%;border-collapse:collapse;margin-top:16px;">
          {rows}
        </table>
        <div style="margin-top:24px;">
          <a href="https://edureport.in/#admin-drafts" 
             style="background:#d95f0e;color:#fff;padding:12px 24px;text-decoration:none;font-size:12px;font-weight:bold;letter-spacing:1px;text-transform:uppercase;">
            Review &amp; Publish Drafts →
          </a>
        </div>
      </div>
      <div style="padding:16px 28px;background:#f5f0e8;font-size:11px;color:#999;text-align:center;">
        EduReport.in Auto-Crawler · Runs every 30 minutes · <a href="https://edureport.in" style="color:#d95f0e;">edureport.in</a>
      </div>
    </div>
    """

    r = requests.post(
        "https://api.brevo.com/v3/smtp/email",
        headers={"api-key": BREVO_API_KEY, "Content-Type": "application/json"},
        json={
            "sender":      {"name": "EduReport Crawler", "email": FROM_EMAIL},
            "to":          [{"email": ALERT_EMAIL}],
            "subject":     f"🔔 EduReport: {len(new_stories)} new {'story' if len(new_stories)==1 else 'stories'} found",
            "htmlContent": html,
        },
    )
    print(f"Alert email: {r.status_code}")

# ── MAIN ──
def main():
    print(f"\n{'='*50}")
    print(f"EduReport Crawler — {datetime.now().strftime('%Y-%m-%d %H:%M:%S UTC')}")
    print(f"{'='*50}")

    all_new = []
    seen_titles = set()

    for query, category in CATEGORIES:
        print(f"\nSearching: {query}")
        articles = fetch_gnews(query, max_results=5)
        print(f"  Found {len(articles)} articles")

        for art in articles:
            title = art.get('title', '').strip()
            if not title or len(title) < 15:
                continue
            if title in seen_titles:
                continue
            seen_titles.add(title)

            # Skip if already in database
            if story_exists(title):
                print(f"  ↷ Already exists: {title[:60]}")
                continue

            print(f"  ✎ Rewriting: {title[:60]}")
            description = art.get('description', '') or ''
            rewritten   = rewrite_with_groq(title, description, category)

            if not rewritten:
                print(f"  ✗ Rewrite failed")
                continue

            saved = save_to_supabase(rewritten, art.get('url', ''), category)
            if saved:
                print(f"  ✓ Saved: {rewritten['title'][:60]}")
                all_new.append({
                    'title':    rewritten['title'],
                    'category': category,
                    'source':   art.get('source', {}).get('name', 'GNews'),
                })
            else:
                print(f"  ✗ Save failed")

            time.sleep(3)  # Rate limit Groq

    print(f"\n{'='*50}")
    print(f"Done. {len(all_new)} new stories saved.")

    if all_new:
        print("Sending alert email...")
        send_alert(all_new)
    else:
        print("No new stories — no alert sent.")

if __name__ == '__main__':
    main()
