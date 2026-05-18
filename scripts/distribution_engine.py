#!/usr/bin/env python3
"""ArchonHQ Distribution Engine — cross-posts to dev.to, generates social copy.

Usage:
    python3 distribution_engine.py [--article PATH] [--all-published]

Generates platform-specific copy for HN, Reddit, LinkedIn, X/Twitter.
Auto-publishes to dev.to via API.
"""

import argparse
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

import requests

# ── Config ──────────────────────────────────────────────────────────────────

ROOT = Path(os.path.expanduser("~/archonhq-content"))
ARTICLES_DIR = Path(os.environ.get("CONTENT_ENGINE_ARTICLES_DIR", "articles"))
SOCIAL_DIR = ROOT / "social"
ENV_FILE = Path(os.path.expanduser("~/.hermes/.env"))

DEVTO_API_URL = "https://dev.to/api/articles"
DEVTO_API_KEY_ENV = "ARCHONHQ_DEVTO_API_KEY"
DEVTO_USERNAME = "michal_szalinski_91bf893d"

LLM_MODEL = os.environ.get("DISTRIBUTE_MODEL", "deepseek/deepseek-chat-v3-0324")
LLM_URL = "https://openrouter.ai/api/v1/chat/completions"

SOCIAL_PLATFORMS = ["hn", "reddit", "linkedin", "x"]


def load_env():
    env = {}
    if ENV_FILE.exists():
        for line in ENV_FILE.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip()
    return env


def log(msg):
    print(f"[distribute] {msg}", file=sys.stderr)


def read_article(path):
    """Split article into frontmatter and body."""
    text = path.read_text()
    if text.startswith("---"):
        parts = text.split("---", 2)
        if len(parts) >= 3:
            return parts[1].strip(), parts[2].strip()
    return "", text


# ── Social Copy Generation ──────────────────────────────────────────────────

SOCIAL_PROMPT = """Generate platform-specific social media copy for this article.
The voice is: first-person, technical but accessible, opinionated, problem-first, lean.
Wit over filler. Strong opinions loosely held.

Article title: {title}
Thesis: {thesis}

Generate:

## HN
Title for HN submission (no clickbait, just honest and specific):
2-line summary for the text field:

## Reddit
Post title for r/LocalLLaMA (technical audience, show your work):
3-4 line hook (link to article at the end):

## LinkedIn
Professional post (different framing for SMB/entrepreneur audience, 3-5 sentences):

## X/Twitter
Thread of 5-7 tweets (hook → thesis → key insight → insight → insight → CTA):
Each tweet on its own line starting with a number:

Output only the copy, no explanations."""

def generate_social_copy(env, title, thesis):
    """Use LLM to generate platform-specific social copy."""
    api_key = env.get("OPENROUTER_API_KEY")
    if not api_key:
        log("  ✗ No OPENROUTER_API_KEY")
        return None

    prompt = SOCIAL_PROMPT.format(title=title, thesis=thesis)

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://archonhq.ai",
    }
    payload = {
        "model": LLM_MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.8,
        "max_tokens": 2000,
    }

    try:
        r = requests.post(LLM_URL, headers=headers, json=payload, timeout=120)
        r.raise_for_status()
        return r.json()["choices"][0]["message"]["content"].strip()
    except Exception as e:
        log(f"  ✗ Social copy generation failed: {e}")
        return None


def save_social_copy(article_stem, copy):
    """Save social copy to individual platform files."""
    SOCIAL_DIR.mkdir(parents=True, exist_ok=True)

    # Split by platform sections
    sections = re.split(r'^##\s+', copy, flags=re.MULTILINE)

    # If we got platform sections
    if len(sections) > 1:
        for section in sections[1:]:
            lines = section.strip().split('\n')
            platform = lines[0].strip().lower().replace("/", "")
            if platform in ["hn", "reddit", "linkedin", "x", "x/twitter", "twitter"]:
                platform_key = "x" if platform in ["x/twitter", "twitter"] else platform
                content = "\n".join(lines[1:]).strip()
                out_file = SOCIAL_DIR / f"{article_stem}_{platform_key}.md"
                out_file.write_text(content)
                log(f"  ✓ {platform_key}: {out_file.name}")
    else:
        # Save as single file
        out_file = SOCIAL_DIR / f"{article_stem}_all.md"
        out_file.write_text(copy)
        log(f"  ✓ All social copy: {out_file.name}")


# ── dev.to Cross-posting ───────────────────────────────────────────────────

def crosspost_devto(env, title, body, canonical_url=None):
    """Cross-post article to dev.to via API."""
    devto_key = env.get(DEVTO_API_KEY_ENV)
    if not devto_key:
        log("  ✗ No ARCHONHQ_DEVTO_API_KEY set. Skipping dev.to cross-post.")
        log("  Get one at https://dev.to/settings/extensions")
        return False

    headers = {
        "api-key": devto_key,
        "Content-Type": "application/json",
    }

    # Convert markdown body to dev.to format
    payload = {
        "article": {
            "title": title,
            "body_markdown": body,
            "published": False,  # Draft first
            "tags": ["ai", "automation", "localhost"],
        }
    }

    if canonical_url:
        payload["article"]["canonical_url"] = canonical_url

    try:
        r = requests.post(DEVTO_API_URL, headers=headers, json=payload, timeout=30)
        r.raise_for_status()
        result = r.json()
        log(f"  ✓ Cross-posted to dev.to (draft): {result.get('url', 'pending')}")
        return True
    except Exception as e:
        log(f"  ✗ dev.to cross-post failed: {e}")
        return False


# ── Main ────────────────────────────────────────────────────────────────────

def find_published_drafts():
    """Find articles with status 'published_draft'."""
    if not ARTICLES_DIR.exists():
        return []
    results = []
    for f in ARTICLES_DIR.glob("*.md"):
        text = f.read_text()
        if re.search(r'^status:\s*published_draft\s*$', text, re.MULTILINE):
            results.append(f)
    return results


def run(article_path=None, all_published=False):
    env = load_env()

    if article_path:
        paths = [Path(article_path)]
    elif all_published:
        paths = find_published_drafts()
        log(f"Found {len(paths)} published drafts to distribute")
    else:
        paths = find_published_drafts()

    if not paths:
        log("No articles to distribute.")
        return

    for path in paths:
        log(f"Processing: {path.name}")
        frontmatter, body = read_article(path)

        # Extract metadata
        title_m = re.search(r'title:\s*"(.+?)"', frontmatter)
        paywall_m = re.search(r'paywall:\s*(\w+)', frontmatter)

        title = title_m.group(1) if title_m else path.stem
        paywall = paywall_m.group(1) if paywall_m else "paid"

        # Try to get thesis from ideas queue
        thesis = ""
        ideas_queue = ROOT / "ideas_queue.json"
        if ideas_queue.exists():
            ideas = json.loads(ideas_queue.read_text()).get("ideas", [])
            for idea in ideas:
                if idea.get("title") == title or title in idea.get("title", ""):
                    thesis = idea.get("thesis", "")
                    break

        # Generate social copy
        log(f"  Generating social copy for: {title[:60]}")
        copy = generate_social_copy(env, title, thesis)
        if copy:
            save_social_copy(path.stem, copy)
        else:
            log(f"  ⚠ Social copy generation failed for {path.name}")

        # Cross-post to dev.to (free-tier articles only, or all as draft)
        if paywall == "free":
            crosspost_devto(env, title, body)
        else:
            # Paid articles: cross-post as draft with teaser
            teaser = body[:500] + "\n\n*[Continue reading on ArchonHQ →](https://archonhq.ai)*\n"
            crosspost_devto(env, title, teaser)

        log(f"  ✓ Distribution complete for {path.name}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="ArchonHQ Distribution Engine")
    parser.add_argument("--article", type=str, help="Specific article to distribute")
    parser.add_argument("--all-published", action="store_true", help="Distribute all published drafts")
    args = parser.parse_args()

    run(article_path=args.article, all_published=args.all_published)
