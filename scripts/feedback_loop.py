#!/usr/bin/env python3
"""ArchonHQ Feedback Loop — use analytics data to improve idea scoring.

Usage:
    python3 feedback_loop.py                   # Apply adjustments to idea catalogue
    python3 feedback_loop.py --dry-run         # Show changes without saving
    python3 feedback_loop.py --verbose         # Print detailed reasoning

Adjusts idea scores based on:
  1. Series performance multiplier: if a series' articles get 2x average views, boost +10%
  2. Topic freshness penalty: if 3+ articles on similar topics published recently, -5% per duplicate
  3. Social engagement bonus: if articles cross-posted to X get above-average engagement, +5%
"""

import argparse
import json
import os
import sys
import urllib.request
from collections import defaultdict
from datetime import datetime, timezone, timedelta
from pathlib import Path

# ── Config ──────────────────────────────────────────────────────────────────

from config import get_config, get_path, get_model, get_api, load_env, load_pipeline_state

_cfg = get_config()

ROOT = get_path('content_root', _cfg)
METRICS_DIR = get_path('metrics_dir', _cfg)
PIPELINE_STATE_PATH = get_path('pipeline_state', _cfg)
IDEA_CATALOGUE_PATH = get_path('idea_catalogue', _cfg)
SERIES_THEMES_DIR = get_path('series_themes_dir', _cfg)

LLM_MODEL = os.environ.get("FEEDBACK_MODEL", get_model('growth_model', _cfg))
LLM_URL = get_api('openrouter_url', _cfg)

# Adjustment factors
SERIES_PERF_BOOST = 0.10        # +10% for series with 2x average views
TOPIC_FRESHNESS_PENALTY = 0.05  # -5% per duplicate beyond threshold
TOPIC_DUPLICATE_THRESHOLD = 3   # 3+ similar articles triggers penalty
SOCIAL_ENGAGEMENT_BONUS = 0.05  # +5% for ideas similar to high-engagement content


# ── Helpers ─────────────────────────────────────────────────────────────────

def log(msg):
    print(f"[feedback_loop] {msg}", file=sys.stderr)


def call_llm(prompt, max_tokens=2000, temperature=0.3):
    """Call LLM via OpenRouter for semantic analysis."""
    api_key = os.environ.get("OPENROUTER_API_KEY", "")
    if not api_key:
        env = load_env(_cfg)
        api_key = env.get("OPENROUTER_API_KEY", "")
    if not api_key:
        log("No OPENROUTER_API_KEY — skipping LLM analysis")
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
            "X-Title": "ArchonHQ Feedback Loop",
        },
    )

    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = json.loads(resp.read())
            msg = data["choices"][0]["message"]
            content = msg.get("content") or msg.get("reasoning") or ""
            if not content:
                return None
            if "```json" in content:
                content = content.split("```json")[1].split("```")[0]
            elif "```" in content:
                content = content.split("```")[1].split("```")[0]
            return content.strip()
    except Exception as e:
        log(f"LLM call failed: {e}")
        return None


# ── Data Loading ────────────────────────────────────────────────────────────

def load_all_weekly_metrics():
    """Load all weekly metrics files from the metrics directory."""
    METRICS_DIR.mkdir(parents=True, exist_ok=True)
    all_metrics = []
    for f in sorted(METRICS_DIR.glob("weekly_*.json")):
        try:
            data = json.loads(f.read_text())
            all_metrics.append(data)
        except Exception as e:
            log(f"Failed to load {f.name}: {e}")
    return all_metrics


def load_idea_catalogue():
    """Load the idea catalogue."""
    return json.loads(IDEA_CATALOGUE_PATH.read_text())


def save_idea_catalogue(cat):
    """Save the idea catalogue."""
    IDEA_CATALOGUE_PATH.write_text(json.dumps(cat, indent=2))


def load_series_themes():
    """Load all series theme definitions."""
    themes = {}
    for f in sorted(SERIES_THEMES_DIR.glob("*.json")):
        try:
            data = json.loads(f.read_text())
            themes[data["series"]] = data
        except Exception as e:
            log(f"Failed to load theme {f.name}: {e}")
    return themes


# ── Performance Analysis ───────────────────────────────────────────────────

def compute_series_performance(all_metrics, pipeline_state):
    """Compute average views per series from metrics and pipeline state.

    Returns:
        dict: {series_name: {"avg_views": float, "article_count": int}}
    """
    # Build a mapping of series -> article list from pipeline state
    series_articles = defaultdict(list)
    articles = pipeline_state.get("articles", {})

    for article_id, article_data in articles.items():
        series = article_data.get("series", "")
        if series:
            series_articles[series].append(article_data)

    # Collect view data from weekly metrics (top_article is the main signal)
    series_views = defaultdict(list)

    for week in all_metrics:
        top = week.get("substack", {}).get("top_article", {})
        if top.get("title") and top.get("views", 0) > 0:
            # Try to match the top article to a series
            title = top["title"].lower()
            for series_name, articles_list in series_articles.items():
                for art in articles_list:
                    art_title = art.get("title", "").lower()
                    if art_title and (art_title in title or title in art_title):
                        series_views[series_name].append(top["views"])
                        break

    # Also count total views attributed to each series from pipeline data
    # If we have no per-article view data, use article count as a proxy
    series_perf = {}
    total_views_all = sum(
        w.get("substack", {}).get("total_views", 0) for w in all_metrics
    )
    total_articles = sum(len(v) for v in series_articles.values())

    for series_name, articles_list in series_articles.items():
        views = series_views.get(series_name, [])
        count = len(articles_list)

        if views:
            avg_views = sum(views) / len(views)
        elif total_articles > 0 and total_views_all > 0:
            # Estimate proportional views based on article count
            avg_views = total_views_all / total_articles
        else:
            avg_views = 0

        series_perf[series_name] = {
            "avg_views": avg_views,
            "article_count": count,
            "total_views": sum(views) if views else 0,
        }

    return series_perf


def compute_overall_avg_views(series_perf):
    """Compute the overall average views across all series."""
    all_views = [v["avg_views"] for v in series_perf.values() if v["avg_views"] > 0]
    return sum(all_views) / len(all_views) if all_views else 0


def get_published_topics(pipeline_state, recent_weeks=4):
    """Extract topics/keywords from recently published articles.

    Returns list of topic strings from article titles and series.
    """
    topics = []
    articles = pipeline_state.get("articles", {})
    cutoff = datetime.now(timezone.utc) - timedelta(weeks=recent_weeks)

    for article_id, article_data in articles.items():
        status = article_data.get("status", "")
        if status not in ("published", "published_draft", "cross_posted"):
            continue

        # Check recency via timestamps
        timestamps = article_data.get("timestamps", {})
        pub_time = timestamps.get("published", timestamps.get("published_draft", ""))
        if pub_time:
            try:
                pub_dt = datetime.fromisoformat(pub_time)
                if pub_dt < cutoff:
                    continue
            except (ValueError, TypeError):
                pass

        # Extract topic keywords from title
        title = article_data.get("title", "")
        series = article_data.get("series", "")
        if title:
            topics.append({
                "title": title,
                "series": series,
                "keywords": _extract_keywords(title),
            })

    return topics


def _extract_keywords(text):
    """Extract meaningful keywords from text (stop word removal)."""
    stop_words = {
        "a", "an", "the", "and", "or", "but", "in", "on", "at", "to", "for",
        "of", "with", "by", "from", "is", "it", "that", "this", "how", "why",
        "your", "you", "can", "will", "be", "are", "was", "were", "been",
        "do", "does", "did", "has", "have", "had", "not", "no", "get", "got",
        "its", "my", "we", "our", "their", "they", "them", "what", "when",
        "where", "who", "which", "if", "then", "than", "so", "just", "about",
        "also", "more", "most", "some", "any", "all", "each", "every",
    }
    words = text.lower().split()
    # Remove punctuation
    words = [w.strip(".,;:!?()-[]{}\"'") for w in words]
    return [w for w in words if len(w) > 2 and w not in stop_words]


def compute_social_engagement(all_metrics):
    """Determine which content types get above-average social engagement.

    Returns:
        dict: {"x_above_avg": bool, "avg_x_impressions": float, "high_engagement_series": set}
    """
    if not all_metrics:
        return {"x_above_avg": False, "avg_x_impressions": 0, "high_engagement_series": set()}

    # Average X impressions across all weeks
    x_impressions = [w.get("social", {}).get("x", {}).get("impressions", 0) for w in all_metrics]
    avg_x = sum(x_impressions) / len(x_impressions) if x_impressions else 0

    # Check if recent weeks are above average
    recent_impressions = x_impressions[-4:] if len(x_impressions) >= 4 else x_impressions
    recent_avg = sum(recent_impressions) / len(recent_impressions) if recent_impressions else 0
    x_above_avg = recent_avg > avg_x * 1.2 if avg_x > 0 else False

    return {
        "x_above_avg": x_above_avg,
        "avg_x_impressions": avg_x,
        "recent_avg_impressions": recent_avg,
        "high_engagement_series": set(),  # Populated by LLM or pattern matching
    }


# ── Topic Similarity (LLM-based) ──────────────────────────────────────────

def compute_topic_similarity_llm(idea_title, published_topics):
    """Use LLM to determine how many published articles are similar to this idea.

    Returns:
        int: count of similar published articles
    """
    if not published_topics:
        return 0

    # Build a concise list for the LLM
    topic_list = [f"- \"{t['title']}\" (series: {t['series']})" for t in published_topics[:30]]

    prompt = f"""You are a content analytics assistant. Determine how many of these published article titles are topically similar to the new idea.

New idea: "{idea_title}"

Published articles:
{chr(10).join(topic_list)}

Two articles are "similar" if they cover the same core technology, concept, or approach (e.g., "Build a RAG pipeline" and "RAG with vector stores" are similar; "Build a RAG pipeline" and "Cost optimization for LLM APIs" are not).

Return a JSON object: {{"similar_count": N, "similar_titles": ["title1", "title2", ...]}}"""

    try:
        result = call_llm(prompt, max_tokens=500, temperature=0.1)
        if result:
            parsed = json.loads(result)
            return parsed.get("similar_count", 0)
    except (json.JSONDecodeError, Exception) as e:
        log(f"Topic similarity LLM call failed: {e}")

    return 0


def compute_topic_similarity_keyword(idea_title, published_topics):
    """Keyword-based topic similarity (fallback when no LLM available).

    Returns:
        int: count of similar published articles
    """
    idea_keywords = set(_extract_keywords(idea_title))
    if not idea_keywords:
        return 0

    similar_count = 0
    for topic in published_topics:
        topic_keywords = set(topic.get("keywords", []))
        # Count overlap — if 2+ keywords match, consider it similar
        overlap = idea_keywords & topic_keywords
        if len(overlap) >= 2:
            similar_count += 1
        # Also match on single long/specific keywords (e.g., "mcp", "rag", "substack")
        elif overlap and any(len(kw) >= 4 for kw in overlap):
            similar_count += 1

    return similar_count


# ── Score Adjustment Logic ─────────────────────────────────────────────────

def adjust_idea_scores(dry_run=False, verbose=False):
    """Main feedback loop: adjust idea scores based on analytics data."""
    log("Loading data...")

    all_metrics = load_all_weekly_metrics()
    pipeline_state = load_pipeline_state(_cfg)
    catalogue = load_idea_catalogue()
    series_themes = load_series_themes()

    if not all_metrics:
        log("No weekly metrics found — run analytics_engine.py --collect first")
        return

    # Compute performance baselines
    series_perf = compute_series_performance(all_metrics, pipeline_state)
    overall_avg = compute_overall_avg_views(series_perf)
    published_topics = get_published_topics(pipeline_state)
    social_eng = compute_social_engagement(all_metrics)

    if verbose:
        log(f"Series performance: {json.dumps({k: {kk: vv for kk, vv in v.items() if kk != 'total_views'} for k, v in series_perf.items()}, indent=2)}")
        log(f"Overall avg views: {overall_avg:.1f}")
        log(f"Published topics (recent): {len(published_topics)}")
        log(f"Social engagement: X above avg = {social_eng['x_above_avg']}")

    # Check if LLM is available for topic similarity
    env = load_env(_cfg)
    has_llm = bool(os.environ.get("OPENROUTER_API_KEY") or env.get("OPENROUTER_API_KEY"))

    # Process each idea
    ideas = catalogue.get("ideas", [])
    adjustments = []

    for idea in ideas:
        idea_id = idea.get("id", "?")
        title = idea.get("title", "")
        current_pct = idea.get("best_fit_pct", 0)
        best_fit = idea.get("best_fit", "")

        if not title:
            continue

        adjustment_reasons = []
        total_multiplier = 1.0

        # ── 1. Series performance multiplier ──
        if best_fit and best_fit in series_perf:
            perf = series_perf[best_fit]
            avg_views = perf["avg_views"]

            if overall_avg > 0 and avg_views >= 2 * overall_avg:
                boost = SERIES_PERF_BOOST
                total_multiplier += boost
                adjustment_reasons.append(
                    f"Series '{best_fit}' performs 2x+ above average "
                    f"({avg_views:.0f} vs {overall_avg:.0f} avg views): +{boost*100:.0f}%"
                )
            elif verbose and avg_views > 0:
                adjustment_reasons.append(
                    f"Series '{best_fit}' at {avg_views:.0f} avg views "
                    f"(overall avg: {overall_avg:.0f}): no boost"
                )

        # ── 2. Topic freshness penalty ──
        if published_topics:
            if has_llm:
                # Use LLM for semantic similarity (with caching to avoid excessive calls)
                similar_count = compute_topic_similarity_llm(title, published_topics)
            else:
                similar_count = compute_topic_similarity_keyword(title, published_topics)

            if similar_count >= TOPIC_DUPLICATE_THRESHOLD:
                penalty = TOPIC_FRESHNESS_PENALTY * (similar_count - TOPIC_DUPLICATE_THRESHOLD + 1)
                total_multiplier -= min(penalty, 0.25)  # Cap at -25%
                adjustment_reasons.append(
                    f"{similar_count} similar articles published recently "
                    f"(threshold: {TOPIC_DUPLICATE_THRESHOLD}): "
                    f"-{min(penalty, 0.25)*100:.0f}%"
                )
            elif verbose:
                adjustment_reasons.append(
                    f"{similar_count} similar articles (below threshold {TOPIC_DUPLICATE_THRESHOLD}): no penalty"
                )

        # ── 3. Social engagement bonus ──
        if social_eng["x_above_avg"]:
            # Check if this idea's series has been cross-posted to X
            if best_fit:
                # Find articles in this series that were cross-posted
                articles = pipeline_state.get("articles", {})
                series_x_posts = sum(
                    1 for a in articles.values()
                    if a.get("series", "").lower() == best_fit.lower()
                    and a.get("status") == "cross_posted"
                    and "x" in [p.lower() for p in a.get("cross_post_platforms", [])]
                )
                if series_x_posts > 0:
                    total_multiplier += SOCIAL_ENGAGEMENT_BONUS
                    adjustment_reasons.append(
                        f"Series '{best_fit}' has {series_x_posts} X cross-posts "
                        f"with above-average engagement: +{SOCIAL_ENGAGEMENT_BONUS*100:.0f}%"
                    )
                elif verbose:
                    adjustment_reasons.append(
                        f"X engagement above average but series '{best_fit}' has no X cross-posts: no bonus"
                    )

        # Apply adjustment
        new_pct = round(current_pct * total_multiplier, 1)
        delta = new_pct - current_pct

        if delta != 0:
            adjustments.append({
                "id": idea_id,
                "title": title,
                "series": best_fit,
                "old_pct": current_pct,
                "new_pct": new_pct,
                "delta": delta,
                "multiplier": total_multiplier,
                "reasons": adjustment_reasons,
            })

            # Update the idea catalogue (unless dry run)
            if not dry_run:
                idea["best_fit_pct"] = new_pct
                # Also update the fit_scores pct for the best_fit series
                if best_fit and best_fit in idea.get("fit_scores", {}):
                    idea["fit_scores"][best_fit]["pct"] = new_pct
                # Store feedback metadata
                if "feedback_adjustments" not in idea:
                    idea["feedback_adjustments"] = []
                idea["feedback_adjustments"].append({
                    "date": datetime.now(timezone.utc).isoformat(),
                    "multiplier": total_multiplier,
                    "delta": delta,
                    "reasons": adjustment_reasons,
                })

        if verbose and adjustment_reasons:
            sign = "+" if delta >= 0 else ""
            log(f"  {idea_id}: {title[:60]}")
            log(f"    {current_pct}% → {new_pct}% ({sign}{delta:.1f}%)")
            for reason in adjustment_reasons:
                log(f"    • {reason}")

    # Save updated catalogue
    if not dry_run and adjustments:
        save_idea_catalogue(catalogue)
        log(f"✓ Updated {len(adjustments)} idea scores in catalogue")
    elif dry_run and adjustments:
        log(f"[DRY RUN] Would update {len(adjustments)} idea scores:")
        for adj in adjustments:
            sign = "+" if adj["delta"] >= 0 else ""
            log(f"  {adj['id']}: {adj['title'][:60]}")
            log(f"    {adj['old_pct']}% → {adj['new_pct']}% ({sign}{adj['delta']:.1f}%)")
            for reason in adj["reasons"]:
                log(f"    • {reason}")
    else:
        log("No score adjustments needed")

    return adjustments


# ── CLI ────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="ArchonHQ Feedback Loop — adjust idea scores based on analytics",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Show what would change without updating catalogue",
    )
    parser.add_argument(
        "--verbose", action="store_true",
        help="Print detailed reasoning for each score adjustment",
    )

    args = parser.parse_args()
    adjust_idea_scores(dry_run=args.dry_run, verbose=args.verbose)


if __name__ == "__main__":
    main()
