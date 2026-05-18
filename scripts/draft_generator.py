#!/usr/bin/env python3
"""ArchonHQ Draft Generator — takes approved ideas, generates full article drafts.

Usage:
    python3 draft_generator.py [--idea ID] [--auto-approve-latest]

Reads from ~/archonhq-content/ideas_queue.json, writes drafts to
articles/<title>.md
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
from config import get_config, get_path, get_model, get_api, get_word_count, load_env, update_pipeline_state

_cfg = get_config()

ROOT = get_path('content_root', _cfg)
IDEAS_QUEUE = get_path('ideas_queue', _cfg)
ARTICLES_DIR = get_path('articles_dir', _cfg)
SKILLS_DIR = get_path('skills_dir', _cfg)
ENV_FILE = get_path('env_file', _cfg)

LLM_MODEL = get_model('draft_model', _cfg)
LLM_URL = get_api('openrouter_url', _cfg)
LLM_TIMEOUT = 180
MIN_WORD_COUNT = get_word_count('min_word_count', _cfg)

# ── Article-Creator Skill Rules (embedded) ──────────────────────────────────

VOICE_RULES = """
## Voice
First-person. Technical but accessible. Opinionated. Problem-first. Lean.

- Write like you're explaining to a colleague at 2am, not presenting to a boardroom
- Strong opinions, loosely held. Pick a side. Commit.
- Wit over filler. A well-placed line lands harder than a paragraph of hedging.
- Evidence over assertion. Show the data. Quote the source. Link the repo.

## Structural Rules
1. Open with the problem. Always. The reader needs to feel the pain before you offer the fix.
2. One idea per paragraph. If a paragraph has two ideas, split it.
3. Specificity wins. "40% failure rate" beats "often fails." "`time.sleep(3)`" beats "a brief wait."
4. Code is prose. Code blocks should tell a story. Comment the non-obvious. Skip the obvious.
5. End with action. The last section should make the reader want to build something.

## ArchonHQ Blog Structure Pattern
1. ## Hook — Vivid scenario the reader recognizes (2-3 paragraphs). This MUST be a ## heading.
2. ## The Idea (60 Seconds) — Bold heading. One paragraph summary of the core thesis.
3. ## Why This Setup Over the Alternatives — What makes this approach worth the reader's time
4. ## Walkthrough — Numbered sections with specific steps, code blocks, techniques
5. ## Caveats — What breaks, where it fails, realistic expectations
6. ## Philosophy — The "so what" — how this fits into larger pattern
7. ## Build Your Own — Short CTA: subscribe link, question for comments.

CRITICAL: The ## Hook heading is mandatory. Do NOT start with an untagged paragraph — always use "## Hook" as the first section heading after the title.

## Negative Prose Elimination
NO negation prose. Replace all:
- "not X" → affirmative version
- "don't/doesn't/can't/won't" → skip/avoid/resist/fails/breaks
- "No X. No Y." → "Absent X. Absent Y." or "Zero X. Zero Y."
- "without X" → "independently" / "autonomously" / "absent X"
- "isn't X, it's Y" → "is Y, beyond X"
- "never/nobody/nothing" → "has yet to" / "goes unmentioned" / "bare/baseline/zero"

## Em-Dash Rules
NO em-dashes (—) anywhere in prose. Replace with comma, period, or rephrase.

## Weasel/Hedge Word Rules
Delete or commit on: "arguably", "somewhat", "in some ways", "it could be said"
Take a position on: "it depends", "on the other hand", "however" (when softening)
"""

def log(msg):
    print(f"[draft_gen] {msg}", file=sys.stderr)


def load_ideas():
    if not IDEAS_QUEUE.exists():
        log(f"Ideas queue not found: {IDEAS_QUEUE}")
        return []
    data = json.loads(IDEAS_QUEUE.read_text())
    return data.get("ideas", [])


def save_ideas(ideas):
    data = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "ideas": ideas,
    }
    IDEAS_QUEUE.write_text(json.dumps(data, indent=2))


def get_voice_samples(n=2):
    """Get the 2 most recent published articles for voice calibration."""
    samples = []
    if not ARTICLES_DIR.exists():
        return samples
    md_files = sorted(ARTICLES_DIR.glob("*.md"), key=lambda f: f.stat().st_mtime, reverse=True)
    for f in md_files[:n]:
        text = f.read_text()[:3000]  # First 3K chars is enough for voice
        if text.strip():
            samples.append({"title": f.stem, "text": text})
    return samples


def build_prompt(idea, voice_samples):
    """Build the full LLM prompt for draft generation."""
    parts = []

    parts.append(f"# Article Draft Generation\n")
    parts.append(f"## Idea\n")
    parts.append(f"- **Title:** {idea['title']}")
    parts.append(f"- **Thesis:** {idea['thesis']}")
    parts.append(f"- **Skill/CLI Spec:** {idea['skill_spec']}")
    parts.append(f"- **Target audience:** {idea['target']}")
    parts.append(f"- **Pain point:** {idea['pain_point']}")
    parts.append(f"- **Paywall:** {idea['paywall']}")
    parts.append(f"- **Sections:** {json.dumps(idea.get('sections', []))}\n")

    parts.append(f"## Voice & Style Rules\n")
    parts.append(VOICE_RULES)

    if voice_samples:
        parts.append(f"\n## Voice Calibration Samples\n")
        parts.append("Match the voice, tone, and structure of these existing articles:\n")
        for sample in voice_samples:
            parts.append(f"### {sample['title']}\n")
            parts.append(sample['text'][:2000])
            parts.append("\n---\n")

    parts.append(f"\n## Instructions\n")
    parts.append("Write the full article in Markdown. Minimum 800 words. Follow ALL voice and structural rules strictly.")
    parts.append("Start with YAML frontmatter (title, status, idea_id, generated_at, paywall, word_count).")
    parts.append("The article must be practical — the reader should be able to build the skill/CLI described in the skill_spec.")
    parts.append("Include code blocks where appropriate. Be specific with commands, file paths, API endpoints.")

    return "\n".join(parts)


def call_llm(env, prompt):
    api_key = env.get("OPENROUTER_API_KEY")
    if not api_key:
        log("  ✗ No OPENROUTER_API_KEY found")
        return None

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://archonhq.ai",
        "X-Title": "ArchonHQ Draft Generator",
    }
    payload = {
        "model": LLM_MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.7,
        "max_tokens": 8000,
    }

    log(f"  Calling LLM ({LLM_MODEL})...")
    try:
        r = requests.post(LLM_URL, headers=headers, json=payload, timeout=LLM_TIMEOUT)
        r.raise_for_status()
        return r.json()["choices"][0]["message"]["content"]
    except Exception as e:
        log(f"  ✗ LLM call failed: {e}")
        return None


def word_count(text):
    return len(text.split())


def generate_draft(idea, env):
    log(f"Generating draft for: {idea['title']}")
    voice_samples = get_voice_samples(2)
    prompt = build_prompt(idea, voice_samples)

    # First attempt
    draft = call_llm(env, prompt)
    if not draft:
        return None

    wc = word_count(draft)
    if wc < MIN_WORD_COUNT:
        log(f"  Draft too short ({wc} words), regenerating...")
        draft = call_llm(env, prompt)
        if draft:
            wc = word_count(draft)
        if not draft or wc < MIN_WORD_COUNT:
            log(f"  ✗ Still too short after retry ({wc} words). Flagging for manual review.")
            return draft  # Return it anyway but flag it

    log(f"  ✓ Draft generated ({wc} words)")
    return draft


def add_hook_heading(content):
    """Ensure ## Hook heading exists after the title. Add if missing."""
    lines = content.split('\n')
    headings = re.findall(r'^##+\s+', content, re.MULTILINE)
    
    if not headings:
        # No sub-headings at all — find first paragraph after title
        for i, line in enumerate(lines):
            if line.startswith('# ') and i == 0:
                continue
            if line.startswith('!['):
                continue
            if line.strip() == '':
                continue
            # First content line — insert Hook heading before it
            lines.insert(i, '## Hook')
            lines.insert(i + 1, '')
            return '\n'.join(lines)
    return content


def add_paywall_marker(content, paywall):
    """Insert <!--paid--> before Walkthrough section for paid articles."""
    if paywall != 'paid' or '<!--paid-->' in content:
        return content
    # Insert before ## Walkthrough or ## Detailed Walkthrough
    for heading in ['## Walkthrough', '## Detailed Walkthrough']:
        idx = content.find(heading)
        if idx != -1:
            content = content[:idx] + '<!--paid-->\n\n' + content[idx:]
            return content
    # Fallback: insert before ## Caveats if no Walkthrough found
    idx = content.find('## Caveat')
    if idx != -1:
        content = content[:idx] + '<!--paid-->\n\n' + content[idx:]
    return content


def save_draft(idea, content):
    """Save draft as markdown file with frontmatter, hook heading, and paywall marker."""
    # Ensure ## Hook heading
    content = add_hook_heading(content)
    # Add paywall marker for paid articles
    content = add_paywall_marker(content, idea.get('paywall', 'paid'))
    
    # Sanitize title to filename
    safe_title = re.sub(r"[^\w\s\-]", "", idea["title"]).strip().replace(" ", "-")
    filename = f"{safe_title}.md"
    filepath = ARTICLES_DIR / filename

    # Build frontmatter
    wc = word_count(content)
    frontmatter = f"""---
title: "{idea['title']}"
status: draft
idea_id: {idea['id']}
generated_at: {datetime.now(timezone.utc).isoformat()}
paywall: {idea.get('paywall', 'paid')}
word_count: {wc}
---

"""
    full_content = frontmatter + content
    ARTICLES_DIR.mkdir(parents=True, exist_ok=True)
    filepath.write_text(full_content)
    log(f"  ✓ Saved to {filepath}")
    return filepath


def run(idea_id=None, auto_approve_latest=False):
    env = load_env()
    ideas = load_ideas()

    if not ideas:
        log("No ideas in queue. Run idea_generator.py first.")
        return

    # Determine which ideas to process
    to_process = []
    if idea_id:
        to_process = [i for i in ideas if i["id"] == idea_id and i["status"] == "approved"]
        if not to_process:
            # Also check pending — allow direct generation from pending
            to_process = [i for i in ideas if i["id"] == idea_id and i["status"] == "pending"]
    elif auto_approve_latest:
        # Auto-approve the highest-scoring pending idea
        pending = [i for i in ideas if i["status"] == "pending"]
        if pending:
            best = max(pending, key=lambda i: float(i.get("score", 0)))
            best["status"] = "approved"
            to_process = [best]
            log(f"Auto-approved: {best['title']} (score: {best['score']})")
            save_ideas(ideas)
    else:
        to_process = [i for i in ideas if i["status"] == "approved"]

    if not to_process:
        log("No approved ideas to process. Use --auto-approve-latest or approve ideas manually in ideas_queue.json")
        return

    for idea in to_process:
        draft = generate_draft(idea, env)
        if draft:
            filepath = save_draft(idea, draft)
            # Update idea status
            for i, orig in enumerate(ideas):
                if orig["id"] == idea["id"]:
                    ideas[i]["status"] = "in_progress"
                    break
            save_ideas(ideas)
            log(f"✓ Complete: {filepath.name}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="ArchonHQ Draft Generator")
    parser.add_argument("--idea", type=str, help="Specific idea ID to generate")
    parser.add_argument("--auto-approve-latest", action="store_true",
                        help="Auto-approve the highest-scoring pending idea")
    args = parser.parse_args()

    run(idea_id=args.idea, auto_approve_latest=args.auto_approve_latest)
