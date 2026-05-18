#!/usr/bin/env python3
"""ArchonHQ Analytics Tracker — weekly metrics report.

Usage:
    python3 analytics_tracker.py [--output PATH]

Collects metrics from Substack (manual for now) and generates a weekly report.
Delivered to Telegram via Hermes cron.
"""

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

# ── Config ──────────────────────────────────────────────────────────────────

ROOT = Path(os.path.expanduser("~/archonhq-content"))
METRICS_DIR = ROOT / "metrics"
ARTICLES_DIR = Path(os.environ.get("CONTENT_ENGINE_ARTICLES_DIR", "articles"))


def log(msg):
    print(f"[analytics] {msg}", file=sys.stderr)


def count_articles_by_status():
    """Count articles by their status from frontmatter."""
    counts = {}
    if not ARTICLES_DIR.exists():
        return counts
    for f in ARTICLES_DIR.glob("*.md"):
        text = f.read_text()
        import re
        m = re.search(r'^status:\s*(\w+)', text, re.MULTILINE)
        if m:
            status = m.group(1)
            counts[status] = counts.get(status, 0) + 1
        else:
            counts["unknown"] = counts.get("unknown", 0) + 1
    return counts


def count_social_copies():
    """Count generated social copies."""
    social_dir = ROOT / "social"
    if not social_dir.exists():
        return 0, {}
    files = list(social_dir.glob("*.md"))
    by_platform = {}
    for f in files:
        platform = f.stem.split("_")[-1] if "_" in f.stem else "unknown"
        by_platform[platform] = by_platform.get(platform, 0) + 1
    return len(files), by_platform


def count_hero_images():
    """Count generated hero images."""
    images_dir = ROOT / "images"
    if not images_dir.exists():
        return 0
    return len(list(images_dir.glob("*.png")))


def count_ideas():
    """Count ideas by status."""
    ideas_file = ROOT / "ideas_queue.json"
    if not ideas_file.exists():
        return {}
    data = json.loads(ideas_file.read_text())
    ideas = data.get("ideas", [])
    counts = {}
    for idea in ideas:
        status = idea.get("status", "unknown")
        counts[status] = counts.get(status, 0) + 1
    return counts


def run(output_path=None):
    METRICS_DIR.mkdir(parents=True, exist_ok=True)
    
    now = datetime.now(timezone.utc)
    week_key = now.strftime("%Y%m%d")
    
    report = {
        "report_date": now.isoformat(),
        "report_type": "weekly",
        "pipeline": {
            "ideas": count_ideas(),
            "articles_by_status": count_articles_by_status(),
            "social_copies_total": count_social_copies()[0],
            "social_copies_by_platform": count_social_copies()[1],
            "hero_images": count_hero_images(),
        },
        # Manual metrics (update these from Substack dashboard)
        "audience": {
            "free_subscribers": "MANUAL — check Substack dashboard",
            "paid_subscribers": "MANUAL — check Substack dashboard",
            "monthly_revenue": "MANUAL — check Substack dashboard",
        },
        "content_performance": {
            "note": "Update with per-article stats from Substack analytics",
            "articles": [],
        },
    }
    
    # Summary for human
    summary_lines = [
        f"📊 ArchonHQ Weekly Report — {now.strftime('%b %d, %Y')}",
        "",
        "Pipeline Status:",
    ]
    
    # Ideas
    ideas = report["pipeline"]["ideas"]
    if ideas:
        summary_lines.append(f"  Ideas: {dict(ideas)}")
    
    # Articles
    articles = report["pipeline"]["articles_by_status"]
    if articles:
        summary_lines.append(f"  Articles: {dict(articles)}")
    
    # Social
    social_total = report["pipeline"]["social_copies_total"]
    summary_lines.append(f"  Social copies: {social_total}")
    
    # Images
    summary_lines.append(f"  Hero images: {report['pipeline']['hero_images']}")
    
    summary_lines.extend([
        "",
        "⚠️ Manual metrics need updating from Substack dashboard:",
        "  • Free subscribers",
        "  • Paid subscribers",
        "  • Per-article views & open rates",
        "",
        "Revenue target: $1,000/mo = 66 paid subs @ $17/mo",
    ])
    
    summary = "\n".join(summary_lines)
    report["summary"] = summary
    
    # Save JSON report
    out_path = Path(output_path) if output_path else METRICS_DIR / f"weekly_{week_key}.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(report, indent=2))
    
    log(f"✓ Report saved to {out_path}")
    print(summary)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="ArchonHQ Analytics Tracker")
    parser.add_argument("--output", type=str, help="Output JSON path")
    args = parser.parse_args()

    run(output_path=args.output)
