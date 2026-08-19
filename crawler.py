import os, json, time, re, requests
from datetime import datetime, timezone, timedelta

SUPABASE_URL  = os.environ['SUPABASE_URL']
SUPABASE_KEY  = os.environ['SUPABASE_KEY']
GROQ_API_KEY  = os.environ['GROQ_API_KEY']
BREVO_API_KEY = os.environ['BREVO_API_KEY']
ALERT_EMAIL   = os.environ['ALERT_EMAIL']
FROM_EMAIL    = os.environ.get('FROM_EMAIL', 'editor@edureport.in')
GNEWS_KEY     = os.environ.get('GNEWS_KEY', '')

# ── Always use broad education query — catches all categories ──
query_text = "education india"
default_category = "Higher Education" 

SUPABASE_HEADERS = {
    "apikey": SUPABASE_KEY,
    "Authorization": f"Bearer {SUPABASE_KEY}",
    "Content-Type": "application/json",
    "Prefer": "return=minimal",
}

def fetch_gnews(query, max_results=10):
    if not GNEWS_KEY:
        print("  No GNews key set")
        return []
    try:
        r = requests.get(
            "https://gnews.io/api/v4/search",
            params={"q": query, "token": GNEWS_KEY, "lang": "en",
                    "country": "in", "in": "title", "max": max_results, "sortby": "publishedAt"},
            timeout=15
        )
        if r.status_code == 429:
            print("  GNews daily limit reached — skipping")
            return []
        if not r.ok:
            print(f"  GNews error {r.status_code}")
            return []
        return r.json().get("articles", [])
    except Exception as e:
        print(f"  GNews fetch error: {e}")
        return []

def story_exists(title):
    try:
        short = title[:50].replace("'", "''")
        r = requests.get(
            f"{SUPABASE_URL}/rest/v1/articles",
            headers={**SUPABASE_HEADERS, "Prefer": ""},
            params={"select": "id", "title": f"ilike.*{short}*", "limit": "1"},
        )
        return r.ok and len(r.json()) > 0
    except:
        return False

def rewrite_with_groq(title, description, category):
    prompt = f"""You are a journalist at EduReport.in, India's education news platform.

Rewrite this story as a publication-ready article.

HEADLINE: {title}
DESCRIPTION: {description}
CATEGORY: {category}

Write 350-450 words. Use <p> and <h2> tags. Do not fabricate quotes.

Return ONLY valid JSON, nothing else:
{{"title":"headline under 90 chars","deck":"one sentence under 160 chars","body":"<p>...</p><h2>...</h2><p>...</p>","meta_title":"under 60 chars","meta_description":"under 160 chars","tags":["tag1","tag2","tag3"]}}"""

    for attempt in range(3):
        try:
            if attempt > 0:
                wait = 20 * attempt
                print(f"  Waiting {wait}s before retry...")
                time.sleep(wait)

            r = requests.post(
                "https://api.groq.com/openai/v1/chat/completions",
                headers={"Authorization": f"Bearer {GROQ_API_KEY}", "Content-Type": "application/json"},
                json={"model": "openai/gpt-oss-20b", "max_tokens": 2000, "temperature": 0.7,
                      "messages": [{"role": "user", "content": prompt}]},
                timeout=60,
            )
            if r.status_code == 429:
                print(f"  Groq rate limit — waiting 30s")
                time.sleep(30)
                continue
            if not r.ok:
                print(f"  Groq error {r.status_code}")
                return None

            text = r.json()["choices"][0]["message"]["content"]
            text = re.sub(r'<think>[\s\S]*?</think>', '', text).strip()
            # Extract JSON
            match = re.search(r'\{[\s\S]*\}', text)
            if not match:
                print(f"  No JSON found in response")
                return None
            return json.loads(match.group())

        except json.JSONDecodeError as e:
            print(f"  JSON parse error: {e}")
            return None
        except Exception as e:
            print(f"  Groq error: {e}")
            return None
    return None

def save_to_supabase(article, source_url, category):
    slug = re.sub(r'[^a-z0-9]+', '-', article['title'].lower()).strip('-')[:90]
    slug = f"{slug}-{int(time.time())}"
    bg_map = {'Higher Education':'bg-higher','K-12':'bg-k12','Policy & Regulatory':'bg-policy',
              'Exams & Results':'bg-exam','EdTech':'bg-edtech','International':'bg-intl','Rankings & Awards':'bg-rank'}
    words = len(article['body'].replace('<',' <').split())
    payload = {
        "title": article['title'], "slug": slug, "deck": article.get('deck',''),
        "body": article['body'], "category": category, "tags": article.get('tags',[]),
        "status": "draft", "ai_generated": True, "author": "EduReport Crawler",
        "read_time": max(3, words//200), "source_url": source_url, "source_name": "Auto-Crawler",
        "bg_class": bg_map.get(category,'bg-higher'), "img_label": "", "featured_position": "none",
        "meta_title": article.get('meta_title', article['title'])[:60],
        "meta_description": article.get('meta_description', article.get('deck',''))[:160],
        "published_at": None,
    }
    r = requests.post(f"{SUPABASE_URL}/rest/v1/articles", headers=SUPABASE_HEADERS, json=payload)
    return r.status_code in (200, 201)

def send_alert(new_stories):
    rows = ''.join([
        f"<tr><td style='padding:10px;border-bottom:1px solid #eee;'>"
        f"<strong style='font-size:14px;'>{s['title']}</strong><br>"
        f"<span style='color:#888;font-size:11px;'>{s['category']} · {s['source']}</span></td></tr>"
        for s in new_stories
    ])
    ist = datetime.now(timezone(timedelta(hours=5, minutes=30))).strftime('%d %b %Y, %I:%M %p')
    html = f"""<div style="font-family:Georgia,serif;max-width:600px;margin:0 auto;">
      <div style="background:#111;padding:20px 28px;border-bottom:3px solid #d95f0e;">
        <div style="color:#fff;font-size:22px;font-weight:bold;">Edu<span style="color:#d95f0e;">Report</span>.in</div>
        <div style="color:rgba(255,255,255,0.4);font-size:11px;letter-spacing:2px;margin-top:4px;">AUTO CRAWLER ALERT · {ist} IST</div>
      </div>
      <div style="padding:24px 28px;background:#fff;">
        <p style="font-size:15px;"><strong>{len(new_stories)} new {'story' if len(new_stories)==1 else 'stories'}</strong> saved as drafts and ready for review.</p>
        <table style="width:100%;border-collapse:collapse;margin-top:16px;">{rows}</table>
        <div style="margin-top:24px;">
          <a href="https://edureport.in" style="background:#d95f0e;color:#fff;padding:12px 24px;text-decoration:none;font-size:12px;font-weight:bold;letter-spacing:1px;text-transform:uppercase;">Review &amp; Publish →</a>
        </div>
      </div>
      <div style="padding:16px 28px;background:#f5f0e8;font-size:11px;color:#999;text-align:center;">
        EduReport Auto-Crawler · Runs every 30 min · <a href="https://edureport.in" style="color:#d95f0e;">edureport.in</a>
      </div>
    </div>"""

    r = requests.post(
        "https://api.brevo.com/v3/smtp/email",
        headers={"api-key": BREVO_API_KEY, "Content-Type": "application/json"},
        json={"sender": {"name": "EduReport Crawler", "email": FROM_EMAIL},
              "to": [{"email": ALERT_EMAIL}], "subject": f"🔔 EduReport: {len(new_stories)} new {'story' if len(new_stories)==1 else 'stories'} found",
              "htmlContent": html}
    )
    print(f"  Alert email: {'✓ Sent' if r.ok else f'✗ Failed ({r.status_code}): {r.text[:100]}'}")

def main():
    print(f"\n{'='*50}")
    print(f"EduReport Crawler — {datetime.now().strftime('%Y-%m-%d %H:%M:%S UTC')}")
    print(f"Query: {query_text}")
    print(f"{'='*50}\n")

    articles = fetch_gnews(query_text, max_results=10)
    print(f"Found {len(articles)} articles from GNews\n")

    new_stories = []
    seen = set()

    for art in articles:
        title = (art.get('title') or '').strip()
        if not title or len(title) < 15 or title in seen:
            continue
        seen.add(title)

        if story_exists(title):
            print(f"↷ Exists: {title[:65]}")
            continue

        # Guess category from title
        t = title.lower()
        if any(w in t for w in ['neet','jee','cuet','result','exam','board result']):
            cat = 'Exams & Results'
        elif any(w in t for w in ['school','cbse','ncert','k-12','student']):
            cat = 'K-12'
        elif any(w in t for w in ['ugc','nep','aicte','policy','regulation']):
            cat = 'Policy & Regulatory'
        elif any(w in t for w in ['edtech','startup','app','platform','online learning']):
            cat = 'EdTech'
        elif any(w in t for w in ['rank','nirf','qs ','times higher']):
            cat = 'Rankings & Awards'
        else:
            cat = default_category

        print(f"✎ Rewriting: {title[:65]}")
        desc = (art.get('description') or '')[:500]
        rewritten = rewrite_with_groq(title, desc, cat)

        if not rewritten:
            print(f"✗ Rewrite failed\n")
            continue

        if save_to_supabase(rewritten, art.get('url',''), cat):
            print(f"✓ Saved: {rewritten['title'][:65]}\n")
            new_stories.append({'title': rewritten['title'], 'category': cat,
                                 'source': (art.get('source') or {}).get('name','GNews')})
        else:
            print(f"✗ Supabase save failed\n")

        time.sleep(15)  # 15s between rewrites — avoids Groq TPM limit

    print(f"{'='*50}")
    print(f"Done. {len(new_stories)} new stories saved.")

    if new_stories:
        print("Sending alert...")
        send_alert(new_stories)
    else:
        print("Nothing new — no alert sent.")

if __name__ == '__main__':
    main()
