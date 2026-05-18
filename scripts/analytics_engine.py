#!/usr/bin/env python3
"""ArchonHQ Analytics Engine — collect metrics from all platforms, generate reports.

Usage:
    python3 analytics_engine.py --collect             # Gather latest metrics
    python3 analytics_engine.py --report              # Generate weekly report
    python3 analytics_engine.py --collect --report     # Collect then report
    python3 analytics_engine.py --report --output FILE # Save report to file

Metrics sources:
  - Substack: scrape stats page or parse weekly digest email
  - X/Twitter: xurl CLI for engagement data
  - Dev.to: API for article views/reactions
  - Reddit: JSON API for comment counts on tracked threads
  - Pipeline: pipeline_state.json for internal funnel metrics
"""

import argparse
import json
import os
import subprocess
import sys
import urllib.request
import urllib.error
from datetime import datetime, timezone, timedelta
from pathlib import Path

# ── Config ──────────────────────────────────────────────────────────────────

from config import get_config, get_path, get_model, get_api, load_env

_cfg = get_config()

ROOT = get_path('content_root', _cfg)
METRICS_DIR = get_path('metrics_dir', _cfg)
PIPELINE_STATE_PATH = get_path('pipeline_state', _cfg)
IDEA_CATALOGUE_PATH = get_path('idea_catalogue', _cfg)

LLM_MODEL = os.environ.get("ANALYTICS_MODEL", get_model('growth_model', _cfg))
LLM_URL = get_api('openrouter_url', _cfg)
DEVTO_API_URL = get_api('devto_api_url', _cfg)

SUBSTACK_STATS_URL = "https://archonhq.ai/stats"
SUBSTACK_DIGEST_SENDER = "substack@substack.com"

REQUEST_TIMEOUT = 30


# ── Helpers ─────────────────────────────────────────────────────────────────

def log(msg):
    print(f"[analytics_engine] {msg}", file=sys.stderr)


def current_week_key():
    """Return ISO week key like '2026-W20'."""
    now = datetime.now(timezone.utc)
    return f"{now.isocalendar()[0]}-W{now.isocalendar()[1]:02d}"


def empty_metrics(period=None):
    """Return a metrics dict matching the canonical schema."""
    return {
        "period": period or current_week_key(),
        "substack": {
            "subscribers_free": 0,
            "subscribers_paid": 0,
            "new_subscribers": 0,
            "articles_published": 0,
            "total_views": 0,
            "top_article": {"title": "", "views": 0},
        },
        "social": {
            "x": {"followers": 0, "impressions": 0, "link_clicks": 0},
            "devto": {"followers": 0, "views": 0, "reactions": 0},
            "reddit": {"threads_answered": 0, "upvotes": 0},
        },
        "pipeline": {
            "ideas_generated": 0,
            "drafts_generated": 0,
            "qa_passed": 0,
            "published": 0,
            "cross_posted": 0,
        },
    }


def call_llm(prompt, max_tokens=2000, temperature=0.3):
    """Call LLM via OpenRouter for analysis tasks."""
    api_key = os.environ.get("OPENROUTER_API_KEY", "")
    if not api_key:
        env = load_env(_cfg)
        api_key = env.get("OPENROUTER_API_KEY", "")
    if not api_key:
        log("No OPENROUTER_API_KEY found — skipping LLM analysis")
        return None

    payload = json.dumps({
        "model": LLM_MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": temperature,
        "max_tokens": max_tokens,
    }).encode()

    req = urllib.request.Request(
        LLM_URL,
        data=payload,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://archonhq.ai",
            "X-Title": "ArchonHQ Analytics Engine",
        },
    )

    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = json.loads(resp.read())
            msg = data["choices"][0]["message"]
            content = msg.get("content") or msg.get("reasoning") or ""
            if not content:
                return None
            # Extract JSON if wrapped in markdown
            if "```json" in content:
                content = content.split("```json")[1].split("```")[0]
            elif "```" in content:
                content = content.split("```")[1].split("```")[0]
            return content.strip()
    except Exception as e:
        log(f"LLM call failed: {e}")
        return None


# ── Substack Collection ────────────────────────────────────────────────────

def collect_substack(env):
    """Collect Substack metrics via stats page scraping or digest email parsing.

    Since Substack has no API, we try:
    1. Browser automation to scrape https://archonhq.ai/stats (requires auth cookies)
    2. Fallback: parse the weekly Substack stats digest email if available
    3. Final fallback: manual placeholder values
    """
    metrics = {
        "subscribers_free": 0,
        "subscribers_paid": 0,
        "new_subscribers": 0,
        "articles_published": 0,
        "total_views": 0,
        "top_article": {"title": "", "views": 0},
    }

    # Try browser automation with curl to stats page
    substack_session = env.get("SUBSTACK_SESSION_COOKIE", "")
    if substack_session:
        log("Attempting Substack stats scrape with auth cookie...")
        try:
            req = urllib.request.Request(
                SUBSTACK_STATS_URL,
                headers={
                    "Cookie": f"substack.sid={substack_session}",
                    "User-Agent": "ArchonHQ-Analytics/1.0",
                },
            )
            with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT) as resp:
                html = resp.read().decode("utf-8", errors="replace")
                metrics = _parse_substack_html(html, metrics)
                log("  ✓ Substack stats scraped successfully")
                return metrics
        except Exception as e:
            log(f"  ✗ Substack scrape failed: {e}")
    else:
        log("No SUBSTACK_SESSION_COOKIE — skipping Substack stats scrape")

    # Try parsing digest email from local mail or .eml files
    digest_dir = ROOT / "digests"
    if digest_dir.exists():
        log("Looking for Substack digest emails...")
        for eml_file in sorted(digest_dir.glob("*.eml"), reverse=True):
            try:
                text = eml_file.read_text(errors="replace")
                parsed = _parse_substack_digest(text, metrics)
                if parsed:
                    log(f"  ✓ Parsed digest from {eml_file.name}")
                    return parsed
            except Exception as e:
                log(f"  ✗ Failed to parse {eml_file.name}: {e}")

    log("  ⚠ No automated Substack data available — using pipeline state fallback")

    # Fallback: count articles from pipeline state
    try:
        state = json.loads(PIPELINE_STATE_PATH.read_text())
        articles = state.get("articles", {})
        published_count = sum(
            1 for a in articles.values()
            if a.get("status") in ("published", "published_draft", "cross_posted")
        )
        metrics["articles_published"] = published_count
    except Exception:
        pass

    return metrics


def _parse_substack_html(html, metrics):
    """Parse Substack stats page HTML for metrics.

    The stats page contains JSON data in script tags or inline data.
    We try to extract subscriber counts and view metrics from the page.
    """
    import re

    # Try to find subscriber counts in the HTML
    # Substack stats page typically shows: "X free subscribers" and "Y paid subscribers"
    free_match = re.search(r'(\d[\d,]*)\s+free\s+subscriber', html, re.IGNORECASE)
    paid_match = re.search(r'(\d[\d,]*)\s+paid\s+subscriber', html, re.IGNORECASE)
    new_match = re.search(r'(\d[\d,]*)\s+new\s+subscriber', html, re.IGNORECASE)

    if free_match:
        metrics["subscribers_free"] = int(free_match.group(1).replace(",", ""))
    if paid_match:
        metrics["subscribers_paid"] = int(paid_match.group(1).replace(",", ""))
    if new_match:
        metrics["new_subscribers"] = int(new_match.group(1).replace(",", ""))

    # Try to find total views
    views_match = re.search(r'(\d[\d,]*)\s+(?:total\s+)?views?', html, re.IGNORECASE)
    if views_match:
        metrics["total_views"] = int(views_match.group(1).replace(",", ""))

    # Try to find top article data from embedded JSON
    json_match = re.search(r'window\._preloads\s*=\s*JSON\.parse\("(.+?)"\)', html)
    if not json_match:
        json_match = re.search(r'\"topPost\"[^}]*\"title\":\s*\"([^\"]+)\"[^}]*\"views\":\s*(\d+)', html)

    if json_match and json_match.lastindex >= 2:
        metrics["top_article"] = {
            "title": json_match.group(1),
            "views": int(json_match.group(2).replace(",", "")),
        }

    return metrics


def _parse_substack_digest(text, metrics):
    """Parse a Substack weekly digest email for key metrics."""
    import re

    # Look for subscriber counts in digest format
    free_match = re.search(r'(\d[\d,]*)\s+free\s+subscriber', text, re.IGNORECASE)
    paid_match = re.search(r'(\d[\d,]*)\s+paid\s+subscriber', text, re.IGNORECASE)
    new_match = re.search(r'[+](\d[\d,]*)\s+new', text, re.IGNORECASE)

    if free_match:
        metrics["subscribers_free"] = int(free_match.group(1).replace(",", ""))
    if paid_match:
        metrics["subscribers_paid"] = int(paid_match.group(1).replace(",", ""))
    if new_match:
        metrics["new_subscribers"] = int(new_match.group(1).replace(",", ""))

    # Only return if we found at least one metric
    if metrics["subscribers_free"] or metrics["subscribers_paid"]:
        return metrics
    return None


# ── X/Twitter Collection ───────────────────────────────────────────────────

def collect_x_metrics(env):
    """Collect X/Twitter metrics using xurl CLI.

    xurl is a CLI tool for X/Twitter API access.
    Falls back to manual entry if xurl is unavailable.
    """
    x_metrics = {"followers": 0, "impressions": 0, "link_clicks": 0}

    # Check if xurl is available
    try:
        result = subprocess.run(
            ["xurl", "--version"],
            capture_output=True, text=True, timeout=10,
        )
        has_xurl = result.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        has_xurl = False

    if not has_xurl:
        log("xurl CLI not found — X metrics unavailable")
        return x_metrics

    log("Collecting X/Twitter metrics via xurl...")

    # Get follower count
    try:
        result = subprocess.run(
            ["xurl", "me", "--format", "json"],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode == 0:
            data = json.loads(result.stdout)
            x_metrics["followers"] = data.get("followers_count", 0)
            log(f"  ✓ X followers: {x_metrics['followers']}")
    except Exception as e:
        log(f"  ✗ Failed to get X follower count: {e}")

    # Get recent tweet engagement (impressions + link clicks)
    try:
        result = subprocess.run(
            ["xurl", "tweets", "--limit", "20", "--format", "json"],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode == 0:
            tweets = json.loads(result.stdout)
            if isinstance(tweets, list):
                total_impressions = 0
                total_link_clicks = 0
                for tweet in tweets:
                    metrics_data = tweet.get("metrics", tweet.get("public_metrics", {}))
                    total_impressions += metrics_data.get("impression_count",
                                               metrics_data.get("impressions", 0))
                    total_link_clicks += metrics_data.get("url_link_clicks",
                                             metrics_data.get("link_clicks", 0))
                x_metrics["impressions"] = total_impressions
                x_metrics["link_clicks"] = total_link_clicks
                log(f"  ✓ X impressions: {total_impressions}, link clicks: {total_link_clicks}")
    except Exception as e:
        log(f"  ✗ Failed to get X tweet metrics: {e}")

    return x_metrics


# ── Dev.to Collection ──────────────────────────────────────────────────────

def collect_devto_metrics(env):
    """Collect Dev.to metrics via API.

    Uses the Dev.to API to get article views, reactions, and follower count.
    API key from ARCHONHQ_DEVTO_API_KEY env var.
    """
    devto_metrics = {"followers": 0, "views": 0, "reactions": 0}

    api_key = env.get("ARCHONHQ_DEVTO_API_KEY", "")
    if not api_key:
        api_key = os.environ.get("ARCHONHQ_DEVTO_API_KEY", "")

    if not api_key:
        log("No ARCHONHQ_DEVTO_API_KEY — skipping Dev.to metrics")
        return devto_metrics

    log("Collecting Dev.to metrics...")

    headers = {"api-key": api_key}

    # Get user info for follower count
    try:
        req = urllib.request.Request(
            "https://dev.to/api/users/me",
            headers=headers,
        )
        with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT) as resp:
            user = json.loads(resp.read())
            devto_metrics["followers"] = user.get("followers_count", 0)
            log(f"  ✓ Dev.to followers: {devto_metrics['followers']}")
    except Exception as e:
        log(f"  ✗ Failed to get Dev.to user info: {e}")

    # Get articles for aggregate views/reactions
    try:
        username = "michal_szalinski_91bf893d"  # From distribution_engine.py
        req = urllib.request.Request(
            f"{DEVTO_API_URL}?username={username}&per_page=50",
            headers=headers,
        )
        with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT) as resp:
            articles = json.loads(resp.read())
            if isinstance(articles, list):
                total_views = sum(a.get("page_views_count", 0) for a in articles)
                total_reactions = sum(
                    a.get("positive_reactions_count", 0) for a in articles
                )
                devto_metrics["views"] = total_views
                devto_metrics["reactions"] = total_reactions
                log(f"  ✓ Dev.to views: {total_views}, reactions: {total_reactions}")
    except Exception as e:
        log(f"  ✗ Failed to get Dev.to articles: {e}")

    return devto_metrics


# ── Reddit Collection ──────────────────────────────────────────────────────

def collect_reddit_metrics(env):
    """Collect Reddit metrics via JSON API.

    Checks tracked threads for comment counts and upvotes.
    Thread URLs are read from the social copy directory.
    """
    reddit_metrics = {"threads_answered": 0, "upvotes": 0}

    # Load tracked Reddit threads from social directory or a tracking file
    tracked_file = ROOT / "reddit_threads.json"
    threads = []

    if tracked_file.exists():
        try:
            data = json.loads(tracked_file.read_text())
            threads = data.get("threads", [])
        except Exception as e:
            log(f"  ✗ Failed to read tracked Reddit threads: {e}")

    if not threads:
        # Fallback: scan social directory for Reddit thread references
        social_dir = ROOT / "social"
        if social_dir.exists():
            import re
            for f in social_dir.glob("*reddit*.md"):
                text = f.read_text(errors="replace")
                url_matches = re.findall(
                    r'https?://(?:www\.)?reddit\.com/r/\w+/comments/(\w+)',
                    text,
                )
                for thread_id in url_matches:
                    threads.append({"id": thread_id, "subreddit": ""})

    if not threads:
        log("No tracked Reddit threads found — skipping Reddit metrics")
        return reddit_metrics

    log(f"Collecting Reddit metrics for {len(threads)} threads...")

    for thread in threads[:20]:  # Limit to 20 threads to avoid rate limiting
        thread_id = thread.get("id", "")
        subreddit = thread.get("subreddit", "")

        try:
            url = f"https://www.reddit.com/comments/{thread_id}.json"
            req = urllib.request.Request(
                url,
                headers={"User-Agent": "ArchonHQ-Analytics/1.0 (+https://archonhq.ai)"},
            )
            with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT) as resp:
                data = json.loads(resp.read())
                if isinstance(data, list) and len(data) >= 1:
                    post = data[0]["data"]["children"][0]["data"]
                    upvotes = post.get("score", 0)
                    num_comments = post.get("num_comments", 0)

                    reddit_metrics["upvotes"] += upvotes
                    if num_comments > 0:
                        reddit_metrics["threads_answered"] += 1

        except (urllib.error.HTTPError, urllib.error.URLError, KeyError, IndexError):
            continue
        except Exception:
            continue

    log(f"  ✓ Reddit threads answered: {reddit_metrics['threads_answered']}, "
        f"upvotes: {reddit_metrics['upvotes']}")
    return reddit_metrics


# ── Pipeline Metrics ───────────────────────────────────────────────────────

def collect_pipeline_metrics():
    """Collect pipeline funnel metrics from pipeline_state.json."""
    pipeline = {
        "ideas_generated": 0,
        "drafts_generated": 0,
        "qa_passed": 0,
        "published": 0,
        "cross_posted": 0,
    }

    try:
        state = json.loads(PIPELINE_STATE_PATH.read_text())
        pipe = state.get("pipeline", {})

        pipeline["ideas_generated"] = pipe.get("idea_generation", {}).get("ideas_generated", 0)
        pipeline["drafts_generated"] = pipe.get("draft_generation", {}).get("drafts_generated", 0)
        pipeline["qa_passed"] = pipe.get("qa_check", {}).get("articles_passed", 0)
        pipeline["published"] = pipe.get("publishing", {}).get("articles_published", 0)

        # Cross-posted: sum across platforms
        platforms_posted = pipe.get("cross_posting", {}).get("platforms_posted", {})
        pipeline["cross_posted"] = sum(platforms_posted.values())

        # Also count from articles dict for more accuracy
        articles = state.get("articles", {})
        for article in articles.values():
            status = article.get("status", "")
            if status == "idea":
                pipeline["ideas_generated"] += 1
            elif status == "draft":
                pipeline["drafts_generated"] += 1
            elif status == "qa_passed":
                pipeline["qa_passed"] += 1
            elif status in ("published", "published_draft"):
                pipeline["published"] += 1
            elif status == "cross_posted":
                pipeline["cross_posted"] += 1

    except Exception as e:
        log(f"  ✗ Failed to read pipeline state: {e}")

    return pipeline


# ── Collection Orchestration ───────────────────────────────────────────────

def collect_all():
    """Gather metrics from all platforms and save."""
    env = load_env(_cfg)
    period = current_week_key()

    log(f"Collecting metrics for {period}...")

    metrics = empty_metrics(period)

    # Substack
    metrics["substack"] = collect_substack(env)

    # X/Twitter
    metrics["social"]["x"] = collect_x_metrics(env)

    # Dev.to
    metrics["social"]["devto"] = collect_devto_metrics(env)

    # Reddit
    metrics["social"]["reddit"] = collect_reddit_metrics(env)

    # Pipeline
    metrics["pipeline"] = collect_pipeline_metrics()

    # Save to metrics directory
    METRICS_DIR.mkdir(parents=True, exist_ok=True)
    out_path = METRICS_DIR / f"weekly_{period}.json"
    out_path.write_text(json.dumps(metrics, indent=2))
    log(f"✓ Metrics saved to {out_path}")

    return metrics


# ── Report Generation ──────────────────────────────────────────────────────

def generate_report(output_path=None):
    """Generate a weekly report from collected metrics data."""
    # Load all weekly metric files
    METRICS_DIR.mkdir(parents=True, exist_ok=True)
    weekly_files = sorted(METRICS_DIR.glob("weekly_*.json"))

    if not weekly_files:
        log("No weekly metrics files found — run --collect first")
        return

    # Load the latest week
    latest_data = json.loads(weekly_files[-1].read_text())
    # Derive period from the file if missing in JSON (compat with old tracker format)
    period = latest_data.get("period", "")
    if not period:
        # Try to derive from filename like weekly_2026-W21.json or weekly_20260514.json
        fname = weekly_files[-1].stem  # e.g. "weekly_2026-W21"
        if "-W" in fname:
            period = fname.split("weekly_", 1)[1]
        else:
            # Old format: weekly_YYYYMMDD — convert to approximate ISO week
            import re
            m = re.search(r'(\d{4})(\d{2})(\d{2})', fname)
            if m:
                dt = datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)))
                iso = dt.isocalendar()
                period = f"{iso[0]}-W{iso[1]:02d}"
            else:
                period = "unknown"

    # Load previous week for comparison (if available)
    prev_data = None
    if len(weekly_files) >= 2:
        prev_data = json.loads(weekly_files[-2].read_text())

    # Build report
    lines = [
        f"📊 ArchonHQ Weekly Analytics — {period}",
        "",
    ]

    # Substack section
    ss = latest_data.get("substack", {})
    lines.append("📰 Substack")
    lines.append(f"  Free subscribers: {ss.get('subscribers_free', 0):,}")
    lines.append(f"  Paid subscribers: {ss.get('subscribers_paid', 0):,}")
    lines.append(f"  New this week: {ss.get('new_subscribers', 0):,}")
    lines.append(f"  Articles published: {ss.get('articles_published', 0)}")
    lines.append(f"  Total views: {ss.get('total_views', 0):,}")
    top = ss.get("top_article", {})
    if top.get("title"):
        lines.append(f"  Top article: \"{top['title']}\" ({top.get('views', 0):,} views)")

    # Conversion rate
    free = ss.get("subscribers_free", 0)
    paid = ss.get("subscribers_paid", 0)
    if free > 0:
        conv = (paid / free) * 100
        lines.append(f"  Free→Paid conversion: {conv:.1f}%")

    # Week-over-week changes
    if prev_data:
        prev_ss = prev_data.get("substack", {})
        delta_free = ss.get("subscribers_free", 0) - prev_ss.get("subscribers_free", 0)
        delta_paid = ss.get("subscribers_paid", 0) - prev_ss.get("subscribers_paid", 0)
        if delta_free or delta_paid:
            lines.append(f"  WoW change: {delta_free:+d} free, {delta_paid:+d} paid")

    lines.append("")

    # Social section
    soc = latest_data.get("social", {})
    lines.append("📱 Social")
    x = soc.get("x", {})
    lines.append(f"  X: {x.get('followers', 0):,} followers, "
                 f"{x.get('impressions', 0):,} impressions, "
                 f"{x.get('link_clicks', 0):,} link clicks")
    devto = soc.get("devto", {})
    lines.append(f"  Dev.to: {devto.get('followers', 0):,} followers, "
                 f"{devto.get('views', 0):,} views, "
                 f"{devto.get('reactions', 0):,} reactions")
    reddit = soc.get("reddit", {})
    lines.append(f"  Reddit: {reddit.get('threads_answered', 0)} threads answered, "
                 f"{reddit.get('upvotes', 0):,} upvotes")

    lines.append("")

    # Pipeline section
    pipe = latest_data.get("pipeline", {})
    lines.append("🔧 Pipeline")
    lines.append(f"  Ideas generated: {pipe.get('ideas_generated', 0)}")
    lines.append(f"  Drafts generated: {pipe.get('drafts_generated', 0)}")
    lines.append(f"  QA passed: {pipe.get('qa_passed', 0)}")
    lines.append(f"  Published: {pipe.get('published', 0)}")
    lines.append(f"  Cross-posted: {pipe.get('cross_posted', 0)}")

    # Conversion rates
    ideas = pipe.get("ideas_generated", 0)
    if ideas > 0:
        draft_rate = pipe.get("drafts_generated", 0) / ideas * 100
        pub_rate = pipe.get("published", 0) / ideas * 100
        lines.append(f"  Idea→Draft rate: {draft_rate:.0f}%")
        lines.append(f"  Idea→Publish rate: {pub_rate:.0f}%")

    lines.append("")

    # LLM-powered insights (if API key available)
    env = load_env(_cfg)
    has_api_key = os.environ.get("OPENROUTER_API_KEY") or env.get("OPENROUTER_API_KEY")
    if has_api_key:
        log("Generating LLM insights...")
        insights = _generate_insights(latest_data, prev_data)
        if insights:
            lines.append("💡 Insights")
            for insight in insights:
                lines.append(f"  • {insight}")
            lines.append("")

    report_text = "\n".join(lines)

    # Output
    if output_path:
        out = Path(output_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(report_text)
        log(f"✓ Report saved to {out}")
    else:
        print(report_text)

    return report_text


def _generate_insights(current, previous):
    """Use LLM to generate insights from weekly metrics."""
    prompt = f"""Analyze these weekly content metrics for ArchonHQ (a technical Substack about AI engineering).

Current week:
{json.dumps(current, indent=2)}

{"Previous week for comparison:" + chr(10) + json.dumps(previous, indent=2) if previous else "No previous week data available."}

Generate 3-5 specific, actionable insights. Focus on:
1. What's working (high-performing content patterns)
2. Bottlenecks in the pipeline (ideas → published conversion)
3. Social platform performance (which drives most engagement)
4. Subscriber growth trends
5. Recommendations for next week

Format: Return a JSON array of strings, each string being one insight. No markdown wrapping."""

    try:
        result = call_llm(prompt, max_tokens=4000, temperature=0.3)
        if result:
            insights = json.loads(result)
            if isinstance(insights, list):
                return insights[:5]
    except (json.JSONDecodeError, Exception) as e:
        log(f"LLM insights failed: {e}")

    return None


# ── CLI ────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="ArchonHQ Analytics Engine — collect metrics and generate reports",
    )
    parser.add_argument(
        "--collect", action="store_true",
        help="Gather latest metrics from all platforms",
    )
    parser.add_argument(
        "--report", action="store_true",
        help="Generate weekly report from collected data",
    )
    parser.add_argument(
        "--output", type=str, default=None,
        help="Save report to file (default: stdout)",
    )

    args = parser.parse_args()

    # Default: if no flags, do both collect and report
    if not args.collect and not args.report:
        args.collect = True
        args.report = True

    if args.collect:
        collect_all()

    if args.report:
        generate_report(output_path=args.output)


if __name__ == "__main__":
    main()
