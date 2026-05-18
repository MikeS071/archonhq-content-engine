#!/usr/bin/env python3
"""ArchonHQ Idea Generator — scans sources for buildable AI skills/CLIs, generates article ideas.

Usage:
    python3 idea_generator.py [--dry-run] [--limit N] [--output PATH]

Outputs ideas to ~/archonhq-content/ideas_queue.json by default.
"""

import argparse
import json
import os
import re
import subprocess
import sys
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

# ── Config ──────────────────────────────────────────────────────────────────
from config import get_config, get_path, get_model, get_api, load_env, update_pipeline_state

_cfg = get_config()

ROOT = get_path('content_root', _cfg)
ARTICLES_DIR = get_path('articles_dir', _cfg)
SKILLS_DIR = get_path('skills_dir', _cfg)
ENV_FILE = get_path('env_file', _cfg)
DEFAULT_OUTPUT = get_path('ideas_queue', _cfg)

REDDIT_SUBS = ["LocalLLaMA", "ChatGPTCoding", "SideProject", "EntrepreneurRideAlong", "selfhosted", "automation"]
DEVTO_TAG = "ai"
HN_TOP_N = 30
GITHUB_PER_PAGE = 30
PRODUCTHUNT_RSS = "https://www.producthunt.com/feed"
PYPI_RSS = "https://pypi.org/rss/updates.xml"

# X/Twitter search queries — targeting buildable AI skills/CLIs
X_SEARCH_QUERIES = [
    "AI CLI tool",
    "MCP server build",
    "AI agent workflow",
    "local LLM automation",
    "AI skill build",
]

LLM_MODEL = get_model('idea_model', _cfg)
LLM_URL = get_api('openrouter_url', _cfg)
LLM_MAX_ANGLES = 5  # max article angles per finding
LLM_TIMEOUT = 120

REQUEST_TIMEOUT = 30
USER_AGENT = "ArchonHQ-IdeaBot/1.0 (+https://archonhq.ai)"

# ── Helpers ─────────────────────────────────────────────────────────────────

def log(msg):
    print(f"[idea_gen] {msg}", file=sys.stderr)


def fetch_json(url, headers=None, params=None):
    try:
        r = requests.get(url, headers=headers, params=params, timeout=REQUEST_TIMEOUT)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        log(f"  ✗ {url}: {e}")
        return None


def fetch_text(url, headers=None):
    try:
        r = requests.get(url, headers=headers, timeout=REQUEST_TIMEOUT)
        r.raise_for_status()
        return r.text
    except Exception as e:
        log(f"  ✗ {url}: {e}")
        return None


def existing_titles():
    """Get set of existing article/skill titles to deduplicate against."""
    titles = set()
    # Articles
    if ARTICLES_DIR.exists():
        for f in ARTICLES_DIR.glob("*.md"):
            titles.add(f.stem.lower().strip())
    # Skills
    for cat_dir in SKILLS_DIR.iterdir():
        if cat_dir.is_dir():
            for skill_dir in cat_dir.iterdir():
                if skill_dir.is_dir():
                    titles.add(skill_dir.name.lower().replace("-", " "))
    return titles


# ── Source: Hacker News ─────────────────────────────────────────────────────

def fetch_hn():
    log("Fetching Hacker News top stories...")
    ids = fetch_json("https://hacker-news.firebaseio.com/v0/topstories.json")
    if not ids:
        return []
    items = []
    headers = {"User-Agent": USER_AGENT}
    for iid in ids[:HN_TOP_N]:
        data = fetch_json(f"https://hacker-news.firebaseio.com/v0/item/{iid}.json", headers=headers)
        if data and data.get("type") == "story" and data.get("title"):
            items.append({
                "title": data["title"],
                "url": data.get("url", f"https://news.ycombinator.com/item?id={iid}"),
                "score": data.get("score", 0),
                "source": "hackernews",
            })
    log(f"  → {len(items)} HN stories")
    return items


# ── Source: Reddit RSS ──────────────────────────────────────────────────────

def fetch_reddit():
    log("Fetching Reddit hot posts...")
    headers = {"User-Agent": USER_AGENT}
    items = []
    for sub in REDDIT_SUBS:
        text = fetch_text(f"https://www.reddit.com/r/{sub}/hot.rss", headers=headers)
        if not text:
            continue
        try:
            root = ET.fromstring(text)
            ns = {"a": "http://www.w3.org/2005/Atom"}
            for entry in root.findall("a:entry", ns)[:10]:
                title_el = entry.find("a:title", ns)
                link_el = entry.find("a:link", ns)
                title = title_el.text if title_el is not None else ""
                link = link_el.get("href", "") if link_el is not None else ""
                if title:
                    items.append({"title": title, "url": link, "source": f"reddit/r/{sub}"})
        except ET.ParseError:
            log(f"  ✗ Failed to parse Reddit r/{sub} RSS")
    log(f"  → {len(items)} Reddit posts")
    return items


# ── Source: GitHub Trending ─────────────────────────────────────────────────

def fetch_github():
    log("Fetching GitHub trending repos...")
    since = (datetime.now(timezone.utc) - timedelta(days=7)).strftime("%Y-%m-%d")
    url = "https://api.github.com/search/repositories"
    params = {
        "q": f"created:>{since} stars:>50",
        "sort": "stars",
        "order": "desc",
        "per_page": GITHUB_PER_PAGE,
    }
    headers = {"User-Agent": USER_AGENT, "Accept": "application/vnd.github.v3+json"}
    data = fetch_json(url, headers=headers, params=params)
    if not data or "items" not in data:
        return []
    items = []
    for repo in data["items"]:
        desc = repo.get("description", "") or ""
        items.append({
            "title": repo["full_name"],
            "url": repo["html_url"],
            "score": repo.get("stargazers_count", 0),
            "description": desc[:300],
            "source": "github_trending",
        })
    log(f"  → {len(items)} GitHub repos")
    return items


# ── Source: Product Hunt RSS ────────────────────────────────────────────────

def fetch_producthunt():
    log("Fetching Product Hunt feed...")
    text = fetch_text(PRODUCTHUNT_RSS, headers={"User-Agent": USER_AGENT})
    if not text:
        return []
    items = []
    try:
        root = ET.fromstring(text)
        ns = {"a": "http://www.w3.org/2005/Atom"}
        for entry in root.findall("a:entry", ns)[:20]:
            title_el = entry.find("a:title", ns)
            link_el = entry.find("a:link", ns)
            title = title_el.text if title_el is not None else ""
            link = link_el.get("href", "") if link_el is not None else ""
            if title:
                items.append({"title": title, "url": link, "source": "producthunt"})
    except ET.ParseError:
        log("  ✗ Failed to parse Product Hunt RSS")
    log(f"  → {len(items)} Product Hunt items")
    return items


# ── Source: PyPI new releases ──────────────────────────────────────────────

def fetch_pypi():
    log("Fetching PyPI recent releases...")
    text = fetch_text(PYPI_RSS, headers={"User-Agent": USER_AGENT})
    if not text:
        return []
    items = []
    try:
        root = ET.fromstring(text)
        for item in root.findall(".//item")[:20]:
            title_el = item.find("title")
            link_el = item.find("link")
            title = title_el.text if title_el is not None else ""
            link = link_el.text if link_el is not None else ""
            if title:
                items.append({"title": title, "url": link, "source": "pypi"})
    except ET.ParseError:
        log("  ✗ Failed to parse PyPI RSS")
    log(f"  → {len(items)} PyPI releases")
    return items


# ── Source: dev.to ──────────────────────────────────────────────────────────

def fetch_devto():
    log("Fetching dev.to AI articles...")
    data = fetch_json(
        "https://dev.to/api/articles",
        headers={"User-Agent": USER_AGENT, "Accept": "application/vnd.forem.api.v1+json"},
        params={"tag": DEVTO_TAG, "per_page": 20},
    )
    if not data:
        return []
    items = []
    for article in data:
        items.append({
            "title": article.get("title", ""),
            "url": article.get("url", ""),
            "score": article.get("positive_reactions_count", 0),
            "source": "devto",
        })
    log(f"  → {len(items)} dev.to articles")
    return items


# ── Source: X/Twitter ──────────────────────────────────────────────────────

def fetch_x():
    """Search X/Twitter for AI tool/CLI discussions via xurl CLI.
    Gracefully skips if xurl is not installed or not authenticated."""
    log("Fetching X/Twitter trends...")
    items = []

    # Check xurl is available and authenticated
    # Note: xurl auth status can show "oauth2: (none)" even when tokens exist
    # (known display bug). So we test with whoami instead.
    try:
        result = subprocess.run(
            ["xurl", "whoami"],
            capture_output=True, text=True, timeout=15
        )
        if result.returncode != 0 or "username" not in result.stdout.lower():
            log("  ⊘ xurl not authenticated — skipping X. Run 'xurl auth oauth2 --app <name>' to enable.")
            return []
    except FileNotFoundError:
        log("  ⊘ xurl not installed — skipping X. Install with: curl -fsSL https://raw.githubusercontent.com/xdevplatform/xurl/main/install.sh | bash")
        return []
    except Exception as e:
        log(f"  ⊘ xurl check failed: {e} — skipping X")
        return []

    for query in X_SEARCH_QUERIES:
        try:
            result = subprocess.run(
                ["xurl", "search", query, "-n", "10"],
                capture_output=True, text=True, timeout=30
            )
            if result.returncode != 0:
                log(f"  ✗ X search '{query}' failed: {result.stderr[:100]}")
                continue

            data = json.loads(result.stdout)
            tweets = data.get("data", [])
            for tweet in tweets:
                text = tweet.get("text", "")
                author_id = tweet.get("author_id", "")
                tweet_id = tweet.get("id", "")
                # Extract meaningful content, skip RTs and very short tweets
                if text.startswith("RT @") or len(text) < 30:
                    continue
                items.append({
                    "title": text[:120].replace("\n", " ").strip(),
                    "url": f"https://x.com/i/status/{tweet_id}",
                    "score": int(tweet.get("public_metrics", {}).get("like_count", 0)),
                    "description": text[:300],
                    "source": "x_twitter",
                })
        except json.JSONDecodeError:
            log(f"  ✗ X search '{query}' returned invalid JSON")
        except subprocess.TimeoutExpired:
            log(f"  ✗ X search '{query}' timed out")
        except Exception as e:
            log(f"  ✗ X search '{query}' error: {e}")

    # Deduplicate by tweet text similarity
    seen = set()
    unique = []
    for item in items:
        key = item["title"][:60]
        if key not in seen:
            seen.add(key)
            unique.append(item)

    log(f"  → {len(unique)} X/Twitter posts")
    return unique


# ── Tier Classification ────────────────────────────────────────────────────

# Keywords that signal buildability
TIER1_KEYWORDS = [
    "cli", "command-line", "tool", "build", "framework", "sdk", "api client",
    "automation", "agent", "skill", "mcp server", "mcp", "workflow",
    "script", "pipeline", "bot", "cron", "scheduler", "runner",
    "open source", "just shipped", "built a",
]
TIER2_KEYWORDS = [
    "pattern", "architecture", "approach", "method", "technique", "strategy",
    "setup", "integration", "hybrid", "local inference", "fine-tun",
    "rag", "retrieval", "embedding", "vector", "deploy",
]
TIER3_KEYWORDS = [
    "raises", "funding", "acquired", "announced", "launches",
    "released model", "benchmark", "paper", "survey", "opinion",
]


def classify_tier(item):
    """Classify a finding into Tier 1, 2, or 3 based on title + description."""
    text = f"{item.get('title', '')} {item.get('description', '')}".lower()
    t1 = sum(1 for kw in TIER1_KEYWORDS if kw in text)
    t2 = sum(1 for kw in TIER2_KEYWORDS if kw in text)
    t3 = sum(1 for kw in TIER3_KEYWORDS if kw in text)
    if t1 >= 2 or (t1 >= 1 and t3 == 0):
        return 1
    if t1 >= 1 or t2 >= 2:
        return 2
    if t3 >= 1 and t1 == 0 and t2 == 0:
        return 3
    return 2  # default: promote rather than bury


def deduplicate(findings, existing):
    """Remove findings that are too similar to existing titles."""
    keep = []
    for f in findings:
        title_words = set(re.findall(r"\w+", f["title"].lower()))
        # Skip if >60% of significant words overlap with an existing title
        too_similar = False
        for ex in existing:
            ex_words = set(re.findall(r"\w+", ex))
            overlap = title_words & ex_words
            if len(overlap) > 0.6 * max(len(title_words), 1):
                too_similar = True
                break
        if not too_similar:
            keep.append(f)
    return keep


# ── LLM Idea Generation ────────────────────────────────────────────────────

SYSTEM_PROMPT = """You are the ArchonHQ content strategist. You identify buildable AI skills and CLIs that readers can construct themselves. The paid membership sells capability acquisition — every article must leave the reader able to DO something new.

For each finding, generate article angles. Each angle MUST include:
- title: working title (punchy, problem-first)
- thesis: one sentence stating what capability the reader gains
- skill_spec: what the reader will build and how it works (2-3 sentences, be specific about architecture)
- target: who this is for
- pain_point: what problem they're paying to solve
- paywall: "paid" if it teaches building something, "free" if it's commentary/why-it-matters
- sections: array of section headings following this canon: Hook → The Idea (60s) → Why This X, Not the Others → Walkthrough → Caveats → Philosophy → CTA
- score: 0-10 based on buildability, originality, audience fit

Respond with valid JSON only. Array of angles for each finding."""

def build_user_prompt(findings):
    lines = ["Generate article angles for these findings:\n"]
    for i, f in enumerate(findings, 1):
        tier = f.get("tier", 2)
        lines.append(f"\n--- Finding {i} (Tier {tier}) ---")
        lines.append(f"Title: {f['title']}")
        if f.get("description"):
            lines.append(f"Description: {f['description']}")
        lines.append(f"Source: {f['source']}")
        lines.append(f"URL: {f.get('url', 'N/A')}")
    lines.append("\nGenerate up to 3 angles per Tier 1 finding, up to 2 per Tier 2 finding. Skip Tier 3.")
    lines.append("Return a JSON array of angle objects.")
    return "\n".join(lines)


def call_llm(env, findings):
    """Call OpenRouter to generate article angles."""
    api_key = env.get("OPENROUTER_API_KEY")
    if not api_key:
        log("  ✗ No OPENROUTER_API_KEY found in .env")
        return []

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://archonhq.ai",
        "X-Title": "ArchonHQ Idea Generator",
    }
    payload = {
        "model": LLM_MODEL,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": build_user_prompt(findings)},
        ],
        "temperature": 0.8,
        "max_tokens": 4000,
    }

    log(f"  Calling LLM ({LLM_MODEL}) for {len(findings)} findings...")
    try:
        r = requests.post(LLM_URL, headers=headers, json=payload, timeout=LLM_TIMEOUT)
        r.raise_for_status()
        text = r.json()["choices"][0]["message"]["content"]
        # Extract JSON from potential markdown fences
        text = re.sub(r"^```json\s*", "", text)
        text = re.sub(r"\s*```$", "", text.strip())
        angles = json.loads(text)
        if isinstance(angles, list):
            return angles
        log(f"  ✗ LLM returned non-array: {type(angles)}")
        return []
    except Exception as e:
        log(f"  ✗ LLM call failed: {e}")
        return []


# ── Main Pipeline ───────────────────────────────────────────────────────────

def count_unpublished_qa_passed():
    """Count articles that are QA-passed but not yet published."""
    count = 0
    if not ARTICLES_DIR.exists():
        return 0
    for md_file in ARTICLES_DIR.rglob("*.md"):
        text = md_file.read_text(errors="ignore")
        if "status: qa_passed" in text and "published" not in text.split("paywall:")[0].split("status:")[-1]:
            count += 1
    return count


def run(dry_run=False, limit=3, output_path=None, force=False):
    env = load_env()
    now = datetime.now(timezone.utc).isoformat()

    # Gate: skip idea generation if backlog is sufficient
    UNPUBLISHED_THRESHOLD = 10
    unpublished = count_unpublished_qa_passed()
    if unpublished >= UNPUBLISHED_THRESHOLD and not force:
        log(f"Backlog sufficient: {unpublished} unpublished QA-passed articles (threshold: {UNPUBLISHED_THRESHOLD})")
        log("Skipping idea generation. Use --force to override.")
        return

    # 1. Fetch all sources
    all_findings = []
    all_findings.extend(fetch_hn())
    all_findings.extend(fetch_reddit())
    all_findings.extend(fetch_github())
    all_findings.extend(fetch_producthunt())
    all_findings.extend(fetch_pypi())
    all_findings.extend(fetch_devto())
    all_findings.extend(fetch_x())
    log(f"\nTotal raw findings: {len(all_findings)}")

    if not all_findings:
        log("No findings from any source. Exiting.")
        return

    # 2. Classify tiers
    for f in all_findings:
        f["tier"] = classify_tier(f)
    tier1 = [f for f in all_findings if f["tier"] == 1]
    tier2 = [f for f in all_findings if f["tier"] == 2]
    tier3 = [f for f in all_findings if f["tier"] == 3]
    log(f"Tiers: {len(tier1)} T1 / {len(tier2)} T2 / {len(tier3)} T3")

    # 3. Deduplicate against existing content
    existing = existing_titles()
    tier1 = deduplicate(tier1, existing)
    tier2 = deduplicate(tier2, existing)
    log(f"After dedup: {len(tier1)} T1 / {len(tier2)} T2")

    # Prioritize: T1 first, then T2, cap at 8 findings for LLM
    candidates = tier1[:5] + tier2[:3]

    if dry_run:
        log("\n=== DRY RUN — raw findings ===")
        for f in candidates:
            log(f"  [T{f['tier']}] {f['title'][:80]}  ({f['source']})")
        log(f"\nWould send {len(candidates)} findings to LLM for angle generation.")
        return

    if not candidates:
        log("No buildable findings after filtering. Exiting.")
        return

    # 4. Generate angles via LLM
    angles = call_llm(env, candidates)
    log(f"LLM returned {len(angles)} angles")

    if not angles:
        log("No angles generated. Exiting.")
        return

    # 5. Build idea objects
    ideas = []
    for i, angle in enumerate(sorted(angles, key=lambda a: float(a.get("score", 0)), reverse=True)[:limit]):
        idea_id = f"idea_{datetime.now(timezone.utc).strftime('%Y%m%d')}_{i+1:03d}"
        idea = {
            "id": idea_id,
            "title": angle.get("title", "Untitled"),
            "thesis": angle.get("thesis", ""),
            "skill_spec": angle.get("skill_spec", ""),
            "target": angle.get("target", ""),
            "pain_point": angle.get("pain_point", ""),
            "source": angle.get("source", ""),
            "tier": angle.get("tier", 2),
            "paywall": angle.get("paywall", "paid"),
            "score": float(angle.get("score", 5)),
            "status": "pending",
            "sections": angle.get("sections", ["Hook", "The Idea (60s)", "Why This Setup, Not the Others", "Walkthrough", "Caveats", "Philosophy", "CTA"]),
            "created_at": now,
        }
        ideas.append(idea)

    # 6. Write output
    output = {
        "generated_at": now,
        "ideas": ideas,
    }
    out_path = Path(output_path) if output_path else DEFAULT_OUTPUT
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(output, indent=2))
    log(f"\n✓ Wrote {len(ideas)} ideas to {out_path}")
    for idea in ideas:
        log(f"  [{idea['paywall'].upper()}] {idea['title']} (score: {idea['score']})")


# ── CLI ─────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="ArchonHQ Idea Generator")
    parser.add_argument("--dry-run", action="store_true", help="Fetch sources only, skip LLM calls")
    parser.add_argument("--limit", type=int, default=3, help="Max ideas to generate (default: 3)")
    parser.add_argument("--output", type=str, default=None, help="Output JSON path")
    parser.add_argument("--force", action="store_true", help="Force generation even with 10+ unpublished articles")
    args = parser.parse_args()

    run(dry_run=args.dry_run, limit=args.limit, output_path=args.output, force=args.force)
