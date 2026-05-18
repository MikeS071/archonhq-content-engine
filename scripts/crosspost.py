#!/usr/bin/env python3
"""Cross-post ArchonHQ articles to Dev.to, Medium, and X.

Usage:
  python3 crosspost.py M01                    # Cross-post article M01
  python3 crosspost.py M01 --platforms dev_to,x  # Specific platforms only
  python3 crosspost.py M01 --dry-run          # Preview without posting
  python3 crosspost.py --all-published         # Cross-post all published articles
"""

import argparse
import json
import os
import re
import subprocess
import sys
import urllib.request
from pathlib import Path
from datetime import date

ARTICLES_DIR = Path(os.environ.get("CONTENT_ENGINE_ARTICLES_DIR", "articles"))
HERO_IMAGES_DIR = ARTICLES_DIR / "hero-images"
ARCHONHQ_BASE = "https://archonhq.ai/p/"

SERIES_TAG_MAP = {
    "caliber": ["business", "ai", "growth", "solopreneur"],
    "shipyard": ["ai", "tutorial", "python", "cli", "build-it-yourself"],
    "signal": ["ai", "engineering", "opinion", "deep-dive"],
    "forge": ["ai", "mcp", "tools", "tutorial"],
    "crucible": ["ai", "reliability", "engineering", "testing"],
    "bastion": ["ai", "privacy", "self-hosted", "local-ai"],
    "keystone": ["enterprise-architecture", "ai", "governance"],
    "atlas": ["knowledge-base", "ai", "graph", "rag"],
}


def load_article(article_id):
    """Load article content and metadata."""
    # Find the article file
    for series_dir in ARTICLES_DIR.iterdir():
        if not series_dir.is_dir():
            continue
        for f in series_dir.glob(f"{article_id}-*.md"):
            content = f.read_text()
            meta = {}
            body_lines = []
            in_frontmatter = False
            fm_count = 0
            for line in content.split('\n'):
                if line.strip() == '---':
                    fm_count += 1
                    in_frontmatter = fm_count <= 2
                    continue
                if in_frontmatter and ':' in line:
                    key, val = line.split(':', 1)
                    meta[key.strip()] = val.strip().strip('"').strip("'")
                elif fm_count > 1:
                    body_lines.append(line)
            
            return {
                "id": article_id,
                "series": series_dir.name.replace("-series", ""),
                "title": meta.get("title", f.stem),
                "paywall": meta.get("paywall", "paid"),
                "status": meta.get("status", "unknown"),
                "filename": f.name,
                "path": str(f),
                "body": '\n'.join(body_lines),
                "meta": meta,
            }
    return None


def extract_hook_paragraph(body, max_chars=280):
    """Extract the first paragraph after ## Hook for social media."""
    lines = body.split('\n')
    capturing = False
    hook_lines = []
    
    for line in lines:
        if line.strip().lower().startswith('## hook'):
            capturing = True
            continue
        if capturing:
            if line.strip().startswith('## '):
                break
            # Skip image lines, empty lines, and code markers
            stripped = line.strip()
            if stripped and not stripped.startswith('![') and not stripped.startswith('```'):
                hook_lines.append(stripped)
    
    if not hook_lines:
        # Fallback: first non-empty, non-heading, non-image paragraph
        for line in lines:
            stripped = line.strip()
            if (stripped and not stripped.startswith('#') 
                and not stripped.startswith('---') 
                and not stripped.startswith('![')
                and not stripped.startswith('```')):
                hook_lines.append(stripped)
                if len(' '.join(hook_lines)) > 100:
                    break
    
    hook = ' '.join(hook_lines)
    # Strip markdown that won't render on social
    hook = re.sub(r'!\[.*?\]\(.*?\)', '', hook)  # Remove image syntax
    hook = re.sub(r'\[([^\]]+)\]\([^\)]+\)', r'\1', hook)  # Links → text only
    hook = hook.strip()
    
    if len(hook) > max_chars:
        hook = hook[:max_chars - 3].rsplit(' ', 1)[0] + '...'
    return hook


def find_hero_image(article_id):
    """Find hero image for the article."""
    if HERO_IMAGES_DIR.exists():
        for ext in ['png', 'jpg', 'webp']:
            path = HERO_IMAGES_DIR / f"{article_id}_hero.{ext}"
            if path.exists():
                return str(path)
    return None


# ─── Dev.to ──────────────────────────────────────────────────────

def crosspost_devto(article, dry_run=False):
    """Cross-post article to Dev.to."""
    api_key = os.environ.get("ARCHONHQ_DEVTO_API_KEY", "")
    if not api_key:
        # Try .env
        try:
            for line in open("/home/hermes/.hermes/.env"):
                if line.startswith("ARCHONHQ_DEVTO_API_KEY="):
                    api_key = line.strip().split("=", 1)[1]
                    break
        except:
            pass
    
    if not api_key:
        return {"status": "error", "message": "No ARCHONHQ_DEVTO_API_KEY set"}
    
    if article["paywall"] == "paid":
        return {"status": "skipped", "message": "Paid article — not cross-posting for free"}
    
    # Build Dev.to article
    series_tag = SERIES_TAG_MAP.get(article["series"], ["ai"])
    
    # Build canonical URL
    canonical = article.get("substack_url", article.get("substack_url", f"{ARCHONHQ_BASE}{article['filename'].replace('.md', '').lower()}"))
    
    # Build body with canonical link
    body = article["body"]
    devto_body = f"*Originally published on [ArchonHQ]({canonical})*\n\n{body}"
    
    payload = {
        "article": {
            "title": article["title"],
            "body_markdown": devto_body,
            "published": False,  # Draft first — manual review before publish
            "tags": series_tag[:4],  # Dev.to allows max 4 tags
            "canonical_url": canonical,
        }
    }
    
    if dry_run:
        return {
            "status": "dry_run",
            "platform": "dev_to",
            "title": article["title"],
            "tags": series_tag[:4],
            "canonical": canonical,
            "body_length": len(devto_body),
        }
    
    data = json.dumps(payload).encode()
    req = urllib.request.Request(
        "https://dev.to/api/articles",
        data=data,
        headers={
            "api-key": api_key,
            "Content-Type": "application/json",
            "User-Agent": "ArchonHQ/1.0",
        }
    )
    
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            result = json.loads(resp.read())
            return {
                "status": "success",
                "platform": "dev_to",
                "url": result.get("url", ""),
                "edit_url": f"https://dev.to/{result.get('user', {}).get('username', '')}/{result.get('slug', '')}/edit",
            }
    except urllib.error.HTTPError as e:
        error_body = e.read().decode()
        return {"status": "error", "platform": "dev_to", "message": f"HTTP {e.code}: {error_body[:200]}"}
    except Exception as e:
        return {"status": "error", "platform": "dev_to", "message": str(e)}


# ─── Medium ──────────────────────────────────────────────────────



# ─── X (Twitter) ────────────────────────────────────────────────

def crosspost_x_post(article, dry_run=False):
    """Post a single tweet with hook + hero image + link."""
    hook = extract_hook_paragraph(article["body"], max_chars=200)
    hero = find_hero_image(article["id"])
    
    # Build tweet text
    canonical = article.get("substack_url", f"{ARCHONHQ_BASE}{article['filename'].replace('.md', '').lower()}")
    tweet_text = f"{hook}\n\n🔗 {canonical}"
    
    if len(tweet_text) > 280:
        tweet_text = f"{hook[:200]}...\n\n🔗 {canonical}"
    
    if dry_run:
        return {
            "status": "dry_run",
            "platform": "x_post",
            "text": tweet_text,
            "hero_image": hero,
            "char_count": len(tweet_text),
        }
    
    # Upload hero image if available
    media_id = None
    if hero:
        try:
            result = subprocess.run(
                ["xurl", "media", "upload", hero, "--category", "tweet_image", "--media-type", "image/png"],
                capture_output=True, text=True, timeout=30
            )
            if result.returncode == 0:
                media_data = json.loads(result.stdout)
                media_id = media_data.get("data", {}).get("media_id_string")
        except Exception:
            pass
    
    # Post tweet
    cmd = ["xurl", "post", tweet_text]
    if media_id:
        cmd.extend(["--media-id", media_id])
    
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
        if result.returncode == 0:
            tweet_data = json.loads(result.stdout)
            return {
                "status": "success",
                "platform": "x_post",
                "tweet_id": tweet_data.get("data", {}).get("id"),
                "url": f"https://x.com/teaser380/status/{tweet_data.get('data', {}).get('id', '')}",
            }
        else:
            return {"status": "error", "platform": "x_post", "message": result.stderr[:200]}
    except Exception as e:
        return {"status": "error", "platform": "x_post", "message": str(e)}


def crosspost_x_thread(article, dry_run=False):
    """Post a thread (5-8 tweets) from the article."""
    body = article["body"]
    canonical = article.get("substack_url", f"{ARCHONHQ_BASE}{article['filename'].replace('.md', '').lower()}")
    
    # Split body into tweetable chunks
    # Strategy: extract key sections and their first paragraph
    sections = []
    current_heading = ""
    current_content = []
    
    for line in body.split('\n'):
        if line.strip().startswith('## ') and not line.strip().lower().startswith('## hook'):
            if current_content:
                sections.append({"heading": current_heading, "content": ' '.join(current_content)})
            current_heading = line.strip().lstrip('#').strip()
            current_content = []
        elif line.strip() and not line.startswith('```') and not line.startswith('<!--'):
            current_content.append(line.strip())
    
    if current_content:
        sections.append({"heading": current_heading, "content": ' '.join(current_content)})
    
    # Build tweets from sections
    tweets = []
    
    # First tweet: hook
    hook = extract_hook_paragraph(body, max_chars=220)
    tweets.append(f"{hook} 🧵👇")
    
    # Middle tweets: key insights from sections
    for section in sections[:6]:
        content = section["content"]
        if len(content) > 260:
            content = content[:257] + "..."
        heading = section["heading"]
        if heading:
            tweet = f"**{heading}**\n\n{content}"
        else:
            tweet = content
        tweets.append(tweet[:280])
    
    # Last tweet: CTA
    tweets.append(f"Full guide + code → {canonical}\n\nSubscribe for weekly AI engineering deep-dives: {ARCHONHQ_BASE}")
    
    if dry_run:
        return {
            "status": "dry_run",
            "platform": "x_thread",
            "tweets": tweets,
            "tweet_count": len(tweets),
        }
    
    # Post thread using xurl
    # First tweet
    try:
        first_result = subprocess.run(
            ["xurl", "post", tweets[0]],
            capture_output=True, text=True, timeout=15
        )
        if first_result.returncode != 0:
            return {"status": "error", "platform": "x_thread", "message": f"First tweet failed: {first_result.stderr[:100]}"}
        
        first_data = json.loads(first_result.stdout)
        first_id = first_data.get("data", {}).get("id")
        
        if not first_id:
            return {"status": "error", "platform": "x_thread", "message": "No tweet ID returned"}
        
        # Reply to create thread
        for i, tweet_text in enumerate(tweets[1:], 1):
            result = subprocess.run(
                ["xurl", "reply", first_id, tweet_text],
                capture_output=True, text=True, timeout=15
            )
            if result.returncode != 0:
                return {
                    "status": "partial",
                    "platform": "x_thread",
                    "message": f"Thread posted {i}/{len(tweets)} tweets before failure",
                    "first_tweet_id": first_id,
                }
        
        return {
            "status": "success",
            "platform": "x_thread",
            "first_tweet_id": first_id,
            "url": f"https://x.com/teaser380/status/{first_id}",
            "tweet_count": len(tweets),
        }
    except Exception as e:
        return {"status": "error", "platform": "x_thread", "message": str(e)}


# ─── Main ────────────────────────────────────────────────────────

PLATFORM_HANDLERS = {
    "dev_to": crosspost_devto,
    "x_post": crosspost_x_post,
    "x_thread": crosspost_x_thread,
}


def main():
    parser = argparse.ArgumentParser(description="Cross-post ArchonHQ articles")
    parser.add_argument("article_id", nargs="?", help="Article ID (e.g. M01)")
    parser.add_argument("--platforms", default="dev_to,x_post", 
                        help="Platforms (comma-separated): dev_to,x_post,x_thread")
    parser.add_argument("--dry-run", action="store_true", help="Preview without posting")
    parser.add_argument("--all-published", action="store_true", help="Cross-post all published articles")
    parser.add_argument("--url", help="Actual Substack URL (overrides generated canonical)")
    args = parser.parse_args()
    
    # Load env
    env_file = Path("/home/hermes/.hermes/.env")
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            if '=' in line and not line.startswith('#'):
                key, val = line.split('=', 1)
                os.environ.setdefault(key.strip(), val.strip())
    
    if args.all_published:
        # Find all published articles
        articles = []
        for d in ARTICLES_DIR.iterdir():
            if not d.is_dir() or d.name in ("hero-images", "prompts"):
                continue
            for f in d.glob("*.md"):
                content = f.read_text()
                if 'status: published' in content or 'status: "published"' in content:
                    aid = f.name.split("-")[0]
                    articles.append(aid)
        
        print(f"Found {len(articles)} published articles")
        for aid in articles:
            article = load_article(aid)
            if article:
                print(f"\n{'='*60}")
                print(f"📰 {article['id']}: {article['title']}")
                print(f"{'='*60}")
                platforms = [p.strip() for p in args.platforms.split(',')]
                for platform in platforms:
                    handler = PLATFORM_HANDLERS.get(platform)
                    if handler:
                        result = handler(article, dry_run=args.dry_run)
                        status = result.get("status", "unknown")
                        if status == "skipped":
                            print(f"  ⏭️  {platform}: {result.get('message', 'skipped')}")
                        elif status == "dry_run":
                            print(f"  🔍 {platform}: would post")
                            for k, v in result.items():
                                if k not in ("status", "platform"):
                                    print(f"     {k}: {str(v)[:100]}")
                        elif status == "success":
                            print(f"  ✅ {platform}: {result.get('url', 'posted')}")
                        else:
                            print(f"  ❌ {platform}: {result.get('message', 'unknown error')}")
        return
    
    if not args.article_id:
        parser.error("Specify article ID or --all-published")
    
    article = load_article(args.article_id.upper())
    if not article:
        print(f"Article {args.article_id} not found")
        sys.exit(1)
    
    # Override canonical URL if provided
    if args.url:
        # Inject into article for handlers to use
        article["substack_url"] = args.url
    
    print(f"📰 {article['id']}: {article['title']}")
    print(f"   Series: {article['series']} | Paywall: {article['paywall']} | Status: {article['status']}")
    
    platforms = [p.strip() for p in args.platforms.split(',')]
    results = {}
    
    for platform in platforms:
        handler = PLATFORM_HANDLERS.get(platform)
        if not handler:
            print(f"  ⚠️ Unknown platform: {platform}")
            continue
        
        result = handler(article, dry_run=args.dry_run)
        results[platform] = result
        status = result.get("status", "unknown")
        
        if status == "skipped":
            print(f"  ⏭️  {platform}: {result.get('message', 'skipped')}")
        elif status == "manual":
            print(f"  📋 {platform}: Manual action required")
            for step in result.get("instructions", []):
                print(f"     {step}")
        elif status == "dry_run":
            print(f"  🔍 {platform}:")
            for k, v in result.items():
                if k not in ("status", "platform"):
                    print(f"     {k}: {str(v)[:150]}")
        elif status == "success":
            print(f"  ✅ {platform}: {result.get('url', 'posted')}")
        elif status == "partial":
            print(f"  ⚠️ {platform}: {result.get('message', 'partial')}")
        else:
            print(f"  ❌ {platform}: {result.get('message', 'unknown error')}")
    
    return results


if __name__ == "__main__":
    main()
