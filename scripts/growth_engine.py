#!/usr/bin/env python3
"""Growth Engine: LLM-powered recommendation system for ArchonHQ content distribution.

Analyzes content inventory, publishing history, and platform signals to recommend
the highest-impact next action for growth and retention.

Usage:
  python3 growth_engine.py                    # Full analysis + recommendations
  python3 growth_engine.py --article M01      # Recommendations for specific article
  python3 growth_engine.py --compact          # Just the top 3 actions, no context
"""

import json
import os
import sys
from pathlib import Path
from datetime import date, timedelta

# ─── Configuration ───────────────────────────────────────────────

CONTENT_DIR = Path(os.environ.get("CONTENT_ENGINE_ARTICLES_DIR", "articles"))
CALENDAR_PATH = Path("/home/hermes/archonhq-content/publishing_calendar.json")
OUTPUT_DIR = Path("/home/hermes/archonhq-content/growth_reports")
OUTPUT_DIR.mkdir(exist_ok=True)

SERIES_META = {
    "caliber": {"prefix": "M", "name": "Caliber", "emoji": "🎯", "tagline": "Business Growth for Solo Operators"},
    "shipyard": {"prefix": "S", "name": "Shipyard", "emoji": "🔧", "tagline": "Build-It-Yourself AI Tools"},
    "signal": {"prefix": "G", "name": "Signal", "emoji": "⚡", "tagline": "Opinionated Engineering Deep-Dives"},
    "forge": {"prefix": "F", "name": "Forge", "emoji": "🔨", "tagline": "MCP Server Engineering"},
    "crucible": {"prefix": "C", "name": "Crucible", "emoji": "🔥", "tagline": "AI Reliability Engineering"},
    "bastion": {"prefix": "B", "name": "Bastion", "emoji": "🛡️", "tagline": "Privacy-First AI"},
    "keystone": {"prefix": "K", "name": "Keystone", "emoji": "🔑", "tagline": "Enterprise Architecture"},
    "atlas": {"prefix": "A", "name": "Atlas", "emoji": "🗺️", "tagline": "Knowledge Architecture"},
}

PLATFORM_PROFILES = {
    "dev_to": {
        "name": "Dev.to",
        "automated": True,
        "best_series": ["shipyard", "signal", "forge", "crucible"],
        "content_type": "full_republish",
        "seo_value": "high",
        "conversion_rate": "low",
        "effort": "automated",
        "rules": [
            "Republish free articles only (canonical URL to Substack)",
            "Shipyard articles perform best — devs search for build guides",
            "Add Dev.to-specific tags: #ai, #tutorial, #python, #cli",
        ],
    },
    "x_post": {
        "name": "X (single post)",
        "automated": True,
        "best_series": ["caliber", "signal", "bastion"],
        "content_type": "hook + link",
        "seo_value": "none",
        "conversion_rate": "low",
        "effort": "automated",
        "rules": [
            "Hook paragraph + hero image + Substack link",
            "Opinionated takes (Signal, Caliber) get engagement",
            "Technical tools (Shipyard) get less reach on X",
        ],
    },
    "x_thread": {
        "name": "X (thread)",
        "automated": True,
        "best_series": ["signal", "caliber", "keystone"],
        "content_type": "5-8 tweet thread from article",
        "seo_value": "none",
        "conversion_rate": "medium",
        "effort": "semi-automated",
        "rules": [
            "Break article into 5-8 tweets, each a standalone insight",
            "End thread with: 'Full guide + code → [Substack link]'",
            "Signal opinionated takes and Caliber frameworks make best threads",
            "Thread engagement > single post engagement by 3-5x",
        ],
    },
    "hacker_news": {
        "name": "Hacker News",
        "automated": False,
        "best_series": ["shipyard", "signal"],
        "content_type": "submit link",
        "seo_value": "none",
        "conversion_rate": "high_spike",
        "effort": "manual_5min",
        "rules": [
            "ONLY for strongest articles — submitting weak ones hurts your account",
            "Shipyard 'Build Your Own X' articles = natural Show HN material",
            "Signal opinionated takes can hit front page if thesis is provocative",
            "Must engage in comments for 2 hours after posting",
            "Title must be the article title, not clickbait",
            "Best time: weekday morning US time (8-10am EST)",
            "NEVER automate — HN community will ban you",
        ],
    },
    "reddit": {
        "name": "Reddit",
        "automated": False,
        "best_series": ["shipyard", "bastion", "crucible"],
        "content_type": "text_post + link",
        "seo_value": "low",
        "conversion_rate": "medium",
        "effort": "manual_10min",
        "rules": [
            "Post core insight as text, link to Substack for 'full build guide'",
            "Target subreddits: r/LocalLLaMA (Bastion, local inference), r/MachineLearning (Signal), r/devops (Crucible), r/MCP (Forge)",
            "Reddit punishes bare self-promotion — must provide value in the post itself",
            "Best for articles that answer a common question or solve a known pain",
        ],
    },
    "github": {
        "name": "GitHub Repo",
        "automated": False,
        "best_series": ["shipyard"],
        "content_type": "repo + README linking to ArchonHQ",
        "seo_value": "very_high",
        "conversion_rate": "medium",
        "effort": "manual_30min",
        "rules": [
            "ONLY for Shipyard articles — each ships a working CLI",
            "README must include: problem statement, install command, usage example, 'Full guide → ArchonHQ' link",
            "GitHub repos rank in Google for '[tool] cli' searches — permanent discovery",
            "Tag with topics: ai, cli, python, mcp, etc.",
            "This is the compound growth play — repos keep getting found months later",
        ],
    },
    "substack_chat": {
        "name": "Substack Chat",
        "automated": False,
        "best_series": ["crucible", "caliber", "signal"],
        "content_type": "discussion question",
        "seo_value": "none",
        "conversion_rate": "retention_only",
        "effort": "manual_2min",
        "rules": [
            "Post a question related to the article's theme",
            "Drives email open rates for next issue",
            "Best for articles that describe a common pain (Crucible) or decision (Caliber)",
            "Example: 'What's your biggest reliability nightmare with AI agents?'",
        ],
    },
}

# ─── Data Gathering ──────────────────────────────────────────────

def load_inventory():
    """Load full article inventory with metadata."""
    articles = []
    for d in sorted(CONTENT_DIR.iterdir()):
        if not d.is_dir() or d.name in ("hero-images", "prompts"):
            continue
        series_key = d.name.replace("-series", "")
        for f in sorted(d.glob("*.md")):
            content = f.read_text()
            meta = {}
            for line in content.split('\n')[:20]:
                if ':' in line and not line.startswith(' '):
                    key, val = line.split(':', 1)
                    meta[key.strip()] = val.strip().strip('"').strip("'")
            
            articles.append({
                "series": series_key,
                "id": f.name.split("-")[0],
                "title": meta.get("title", f.stem),
                "status": meta.get("status", "unknown"),
                "paywall": meta.get("paywall", "paid"),
                "filename": f.name,
                "path": str(f),
            })
    return articles


def load_calendar():
    """Load publishing calendar."""
    if CALENDAR_PATH.exists():
        return json.loads(CALENDAR_PATH.read_text())
    return {"entries": []}


def get_published_urls():
    """Try to get published article URLs from Substack RSS."""
    import urllib.request
    urls = {}
    try:
        req = urllib.request.Request(
            "https://archonhq.ai/feed",
            headers={"User-Agent": "Mozilla/5.0"}
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            import xml.etree.ElementTree as ET
            tree = ET.fromstring(resp.read())
            ns = {"atom": "http://www.w3.org/2005/Atom"}
            for entry in tree.findall(".//atom:entry", ns):
                title_el = entry.find("atom:title", ns)
                link_el = entry.find("atom:link", ns)
                if title_el is not None and link_el is not None:
                    urls[title_el.text] = link_el.get("href", "")
    except Exception:
        pass
    return urls


def get_devto_stats():
    """Get recent Dev.to article stats if available."""
    key = os.environ.get("ARCHONHQ_DEVTO_API_KEY", "")
    if not key:
        return []
    try:
        import urllib.request
        req = urllib.request.Request(
            "https://dev.to/api/articles/me?per_page=10",
            headers={"api-key": key, "User-Agent": "ArchonHQ/1.0"}
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read())
    except Exception:
        return []


def get_x_recent():
    """Get recent X posts for engagement data."""
    try:
        import subprocess
        result = subprocess.run(
            ["xurl", "timeline", "-n", "10"],
            capture_output=True, text=True, timeout=15
        )
        if result.returncode == 0:
            return json.loads(result.stdout)
    except Exception:
        pass
    return []


# ─── Analysis Engine ─────────────────────────────────────────────

def analyze_inventory(inventory):
    """Compute inventory-level statistics."""
    from collections import Counter
    
    total = len(inventory)
    by_status = Counter(a["status"] for a in inventory)
    by_series = {}
    for a in inventory:
        s = a["series"]
        if s not in by_series:
            by_series[s] = {"total": 0, "published": 0, "qa_passed": 0, "free": 0, "paid": 0}
        by_series[s]["total"] += 1
        if a["status"] == "published":
            by_series[s]["published"] += 1
        if a["status"] == "qa_passed":
            by_series[s]["qa_passed"] += 1
        if a["paywall"] == "free":
            by_series[s]["free"] += 1
        else:
            by_series[s]["paid"] += 1
    
    return {
        "total": total,
        "by_status": dict(by_status),
        "by_series": by_series,
        "free_count": sum(1 for a in inventory if a["paywall"] == "free"),
        "paid_count": sum(1 for a in inventory if a["paywall"] == "paid"),
    }


def analyze_calendar(calendar):
    """Analyze publishing calendar for gaps and upcoming."""
    entries = calendar.get("entries", [])
    today = date.today()
    
    upcoming = []
    overdue = []
    published_count = 0
    
    for e in entries:
        pub_date = date.fromisoformat(e["date"])
        if pub_date < today:
            published_count += 1
        elif pub_date == today:
            upcoming.append({**e, "when": "today"})
        elif pub_date <= today + timedelta(days=4):
            upcoming.append({**e, "when": f"in {(pub_date - today).days} days"})
    
    return {
        "total": len(entries),
        "published_or_past": published_count,
        "upcoming": upcoming,
        "remaining": len(entries) - published_count,
    }


def score_platform_fit(article, platform_key, platform):
    """Score how well an article fits a platform (0-10)."""
    score = 0
    series = article["series"]
    paywall = article["paywall"]
    status = article["status"]
    
    # Series fit
    if series in platform["best_series"]:
        score += 4
    elif series in ["signal"]:  # Signal fits everywhere
        score += 2
    
    # Paywall rules
    if platform_key in ["medium", "dev_to"] and paywall == "free":
        score += 3
    elif platform_key in ["medium", "dev_to"] and paywall == "paid":
        score -= 10  # Don't cross-post paid articles for free
    
    # Content type fit
    if platform_key == "github" and series == "shipyard":
        score += 4  # GitHub is ONLY for Shipyard
    elif platform_key == "github" and series != "shipyard":
        score -= 10  # No repos for non-tool articles
    
    # Status
    if status == "published":
        score += 2
    elif status == "qa_passed":
        score += 1
    
    return max(0, min(10, score))


# ─── LLM Reasoning Layer ────────────────────────────────────────

def build_prompt(inventory_analysis, calendar_analysis, devto_stats, x_recent, 
                 published_urls, target_article=None, platform_data=PLATFORM_PROFILES):
    """Build the LLM prompt for growth recommendations."""
    
    # Article inventory summary
    inv = inventory_analysis
    series_summary = []
    for s, stats in inv["by_series"].items():
        meta = SERIES_META.get(s, {"name": s.title(), "emoji": "?"})
        series_summary.append(
            f"  {meta['emoji']} {meta['name']}: {stats['total']} articles "
            f"({stats['published']} published, {stats['qa_passed']} ready, "
            f"{stats['free']} free, {stats['paid']} paid)"
        )
    
    # Calendar
    cal = calendar_analysis
    upcoming_lines = []
    for u in cal.get("upcoming", []):
        upcoming_lines.append(f"  {u['date']} {u['id']}: {u.get('title', '?')} ({u['when']})")
    
    # Dev.to stats
    devto_lines = []
    if devto_stats:
        for a in devto_stats[:5]:
            devto_lines.append(
                f"  '{a.get('title', '?')[:50]}' — {a.get('page_views_count', 0)} views, "
                f"{a.get('positive_reactions_count', 0)} reactions, "
                f"{a.get('comments_count', 0)} comments"
            )
    
    # X recent
    x_lines = []
    if x_recent and isinstance(x_recent, dict) and "data" in x_recent:
        for t in x_recent["data"][:5]:
            metrics = t.get("public_metrics", {})
            x_lines.append(
                f"  '{t.get('text', '?')[:50]}' — "
                f"{metrics.get('like_count', 0)} likes, "
                f"{metrics.get('retweet_count', 0)} RTs, "
                f"{metrics.get('reply_count', 0)} replies"
            )
    
    # Platform rules summary
    platform_rules = []
    for pk, p in platform_data.items():
        rules_str = "; ".join(p["rules"][:3])
        platform_rules.append(f"  {p['name']} (best: {', '.join(p['best_series'])}, effort: {p['effort']}): {rules_str}")

    target_section = ""
    if target_article:
        target_section = f"""
## TARGET ARTICLE
You must focus your recommendations on this specific article:
- ID: {target_article['id']}
- Title: {target_article['title']}
- Series: {target_article['series']}
- Paywall: {target_article['paywall']}
- Status: {target_article['status']}

Score its fit for each platform and recommend the 3 best actions.
"""

    prompt = f"""You are the ArchonHQ Growth Engine. Analyse the content inventory, publishing calendar, and platform data below. Recommend the highest-impact next actions for growth (new subscribers) and retention (existing subscriber value).

## CONTENT INVENTORY
Total: {inv['total']} articles ({inv['free_count']} free, {inv['paid_count']} paid)
Status: {json.dumps(inv['by_status'])}

By series:
{chr(10).join(series_summary)}

## PUBLISHING CALENDAR
{cal['total']} articles scheduled, {cal['published_or_past']} already past, {cal['remaining']} remaining.
Upcoming:
{chr(10).join(upcoming_lines) if upcoming_lines else "  No upcoming in next 4 days"}

## PLATFORM PERFORMANCE
Dev.to recent articles:
{chr(10).join(devto_lines) if devto_lines else "  No data available"}

X recent posts:
{chr(10).join(x_lines) if x_lines else "  No data available"}

Published URLs:
{json.dumps(list(published_urls.values())[:5], indent=2) if published_urls else "  Could not fetch RSS"}

## PLATFORM RULES
{chr(10).join(platform_rules)}
{target_section}
## OUTPUT FORMAT

Return a JSON object with this exact structure:
{{
  "summary": "One-paragraph assessment of current growth position",
  "actions": [
    {{
      "priority": 1,
      "action": "Specific action to take",
      "platform": "platform_key",
      "article_id": "M01 or null",
      "rationale": "Why this is the best next move",
      "expected_impact": "low/medium/high/high_spike",
      "effort": "automated/5min/10min/30min",
      "timing": "When to do this"
    }}
  ],
  "content_gaps": [
    "Specific content gap identified"
  ],
  "retention_moves": [
    "Specific retention action"
  ]
}}

Prioritise:
1. Actions with highest expected_impact relative to effort
2. Compound growth plays (GitHub repos, SEO) over one-time spikes
3. Actions that leverage existing content (cross-posting) over new content creation
4. Retention moves that increase perceived subscription value

Think strategically. Be specific. Every recommendation must name a specific article, platform, and action."""
    
    return prompt


def call_llm(prompt):
    """Call LLM via OpenRouter for reasoning."""
    api_key = os.environ.get("OPENROUTER_API_KEY", "")
    if not api_key:
        return None
    
    import urllib.request
    
    payload = json.dumps({
        "model": "z-ai/glm-5.1",
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.3,
        "max_tokens": 2000,
    }).encode()
    
    req = urllib.request.Request(
        "https://openrouter.ai/api/v1/chat/completions",
        data=payload,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://archonhq.ai",
        }
    )
    
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read())
            content = data["choices"][0]["message"]["content"]
            # Extract JSON from response (may have markdown wrapping)
            if "```json" in content:
                content = content.split("```json")[1].split("```")[0]
            elif "```" in content:
                content = content.split("```")[1].split("```")[0]
            return json.loads(content.strip())
    except Exception as e:
        print(f"LLM call failed: {e}", file=sys.stderr)
        return None


def fallback_recommendations(inventory, inventory_analysis, calendar_analysis):
    """Rule-based fallback if LLM is unavailable."""
    actions = []
    
    # Find published articles that haven't been cross-posted
    published = [a for a in inventory if a["status"] == "published"]
    free_published = [a for a in published if a["paywall"] == "free"]
    shipyard_published = [a for a in published if a["series"] == "shipyard"]
    
    # Rule: Free articles should go to Dev.to + Medium
    for a in free_published[:3]:
        actions.append({
            "priority": len(actions) + 1,
            "action": f"Cross-post '{a['title'][:50]}' to Dev.to and Medium",
            "platform": "dev_to",
            "article_id": a["id"],
            "rationale": f"Free {a['series']} article — maximise reach via SEO platforms",
            "expected_impact": "medium",
            "effort": "automated",
            "timing": "Immediately after Substack publish",
        })
    
    # Rule: Shipyard articles need GitHub repos
    for a in shipyard_published[:2]:
        actions.append({
            "priority": len(actions) + 1,
            "action": f"Create GitHub repo for '{a['title'][:50]}'",
            "platform": "github",
            "article_id": a["id"],
            "rationale": "Shipyard CLI tools get permanent discovery via GitHub SEO",
            "expected_impact": "high",
            "effort": "30min",
            "timing": "Within 48 hours of publish",
        })
    
    # Rule: Opinionated articles should be X threads
    signal_published = [a for a in published if a["series"] == "signal"]
    for a in signal_published[:2]:
        actions.append({
            "priority": len(actions) + 1,
            "action": f"Create X thread from '{a['title'][:50]}'",
            "platform": "x_thread",
            "article_id": a["id"],
            "rationale": "Signal opinionated takes get 3-5x more engagement as threads",
            "expected_impact": "medium",
            "effort": "semi-automated",
            "timing": "Day of publish, peak hours (8-10am or 5-7pm)",
        })
    
    return {
        "summary": f"Rule-based recommendations for {len(published)} published articles across {len(inventory_analysis['by_series'])} series.",
        "actions": actions[:5],
        "content_gaps": [
            "Keystone and Atlas series have zero articles — enterprise and knowledge architecture audiences untouched",
            "Only 5 free articles out of 50+ — consider making series intros free as reader magnets",
        ],
        "retention_moves": [
            "Activate Substack Chat with a discussion prompt after each Crucible article",
            "Add 'Previously on...' callbacks between series articles for continuity",
        ],
    }


# ─── Main ────────────────────────────────────────────────────────

def main():
    import argparse
    parser = argparse.ArgumentParser(description="ArchonHQ Growth Engine")
    parser.add_argument("--article", help="Target specific article ID (e.g. M01)")
    parser.add_argument("--compact", action="store_true", help="Just top 3 actions")
    parser.add_argument("--no-llm", action="store_true", help="Rule-based only, skip LLM")
    parser.add_argument("--save", action="store_true", help="Save report to file")
    args = parser.parse_args()
    
    # Gather data
    inventory = load_inventory()
    calendar = load_calendar()
    inventory_analysis = analyze_inventory(inventory)
    calendar_analysis = analyze_calendar(calendar)
    published_urls = get_published_urls()
    devto_stats = get_devto_stats()
    x_recent = get_x_recent()
    
    # Target article?
    target_article = None
    if args.article:
        target_article = next((a for a in inventory if a["id"].lower() == args.article.lower()), None)
        if not target_article:
            print(f"Article {args.article} not found")
            sys.exit(1)
    
    # Try LLM reasoning
    result = None
    if not args.no_llm:
        prompt = build_prompt(inventory_analysis, calendar_analysis, devto_stats, 
                              x_recent, published_urls, target_article)
        result = call_llm(prompt)
    
    if result is None:
        result = fallback_recommendations(inventory, inventory_analysis, calendar_analysis)
    
    # Output
    if args.compact:
        for i, action in enumerate(result["actions"][:3], 1):
            print(f"{i}. [{action.get('expected_impact', '?').upper()}] {action['action']}")
            print(f"   Platform: {action.get('platform', '?')} | Effort: {action.get('effort', '?')} | {action.get('rationale', '')[:80]}")
    else:
        print(f"## Growth Engine Report — {date.today().isoformat()}")
        print()
        print(result["summary"])
        print()
        
        print("### Top Actions")
        for i, action in enumerate(result["actions"], 1):
            print(f"\n**{i}. {action['action']}**")
            print(f"   Platform: {action.get('platform', '?')} | Impact: {action.get('expected_impact', '?')} | Effort: {action.get('effort', '?')}")
            print(f"   Rationale: {action.get('rationale', '')}")
            print(f"   Timing: {action.get('timing', '')}")
        
        if result.get("content_gaps"):
            print("\n### Content Gaps")
            for gap in result["content_gaps"]:
                print(f"  • {gap}")
        
        if result.get("retention_moves"):
            print("\n### Retention Moves")
            for move in result["retention_moves"]:
                print(f"  • {move}")
    
    # Save report
    if args.save:
        report_path = OUTPUT_DIR / f"growth_{date.today().isoformat()}.json"
        report_path.write_text(json.dumps(result, indent=2))
        print(f"\nReport saved to {report_path}")
    
    return result


if __name__ == "__main__":
    main()
