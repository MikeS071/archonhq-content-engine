#!/usr/bin/env python3
"""Fix negation prose across all ArchonHQ articles using LLM-powered context-aware rewrites.

Uses OpenRouter API with Claude Sonnet to read each article's prose in context
and craft natural-sounding affirmative rewrites.
"""
import json
import os
import re
import sys
import time
import requests
from pathlib import Path

ENV = {}
for line in Path(os.path.expanduser("~/.hermes/.env")).read_text().splitlines():
    if '=' in line and not line.startswith('#'):
        k, v = line.split('=', 1)
        ENV[k.strip()] = v.strip()

OPENROUTER_KEY = ENV.get('OPENROUTER_API_KEY', '')
ARTICLES_DIR = Path(os.environ.get("CONTENT_ENGINE_ARTICLES_DIR", "articles"))

SYSTEM_PROMPT = """You are an expert copy editor for ArchonHQ, a technical AI blog. Your task is to rewrite negation prose into affirmative constructions while preserving the author's direct, opinionated voice.

RULES:
1. Replace these words/phrases with affirmative equivalents:
   - "not" → rephrase affirmatively (e.g. "not a hypothetical" → "an actual event")
   - "without" → rephrase (e.g. "without duplicating" → "avoiding duplicate")
   - "don't/doesn't/can't/won't" → rephrase (e.g. "doesn't work" → "fails")
   - "never" → rephrase (e.g. "never succeeds" → "always fails")
   - "nobody" → rephrase (e.g. "nobody mentions" → "goes unmentioned")
   - "nothing" → rephrase (e.g. "nothing works" → "everything fails")
   - "cannot" → rephrase (e.g. "cannot overstate" → "deserves emphasis")
   - "however" (as hedging) → rephrase or remove
   - "insufficient" → rephrase (e.g. "insufficient data" → "lacks adequate data")
   - em-dashes (—) → use colons or periods instead

2. EXEMPT from rewriting (leave exactly as-is):
   - Code blocks (anything between ``` markers)
   - Frontmatter (between --- markers)
   - Table content (lines starting with |)
   - Image filenames and paths
   - The canonical heading "Why This Setup, Not the Others" — this is an intentional contrast structure, not negation prose
   - Technical expressions like "if not", "is not None", "not in" in code
   - File paths and slugs

3. Preserve the author's VOICE: direct, opinionated, no hedging, no weasel words.
4. Output ONLY a JSON object with the fixes, no explanation.

OUTPUT FORMAT:
Return a JSON object where each key is the exact original text to replace (including enough context to be unique) and each value is the replacement text. If no fixes needed, return {}.

Example output:
{
  "That's not a hypothetical": "That happened to me this morning",
  "without duplicating work": "avoiding duplicate work"
}"""

def extract_prose_blocks(content):
    """Split content into processable prose blocks, skipping code/frontmatter/tables."""
    lines = content.split('\n')
    in_frontmatter = False
    frontmatter_lines = 0
    in_code = False
    prose_lines = {}  # line_num -> line_text for lines that are prose
    
    for i, line in enumerate(lines):
        # Track frontmatter
        if line.strip() == '---':
            frontmatter_lines += 1
            if frontmatter_lines <= 2:
                in_frontmatter = not in_frontmatter
                continue
        
        if in_frontmatter:
            continue
            
        # Track code blocks
        if line.strip().startswith('```'):
            in_code = not in_code
            continue
        
        if in_code:
            continue
        
        # Skip tables
        if line.strip().startswith('|'):
            continue
            
        # Skip image lines
        if line.strip().startswith('!['):
            continue
        
        # Skip empty lines
        if not line.strip():
            continue
            
        prose_lines[i] = line
    
    return prose_lines

def find_negation_lines(prose_lines):
    """Find lines containing negation patterns."""
    NEGATION_RE = [
        r'\bnot\b',
        r'\bwithout\b',
        r"\bdon't\b",
        r"\bdoesn't\b",
        r"\bcan't\b",
        r"\bwon't\b",
        r'\bnever\b',
        r'\bnobody\b',
        r'\bnothing\b',
        r'\bcannot\b',
        r'\bhowever\b',
        r'\binsufficient\b',
        r'—',  # em-dash
    ]
    
    flagged = {}
    for line_num, line in prose_lines.items():
        # Skip headings that are canonical ArchonHQ patterns
        if 'Why This Setup, Not the Others' in line:
            continue
        
        for pattern in NEGATION_RE:
            if re.search(pattern, line, re.IGNORECASE):
                flagged[line_num] = line
                break
    
    return flagged

def get_llm_fixes(article_path, flagged_lines):
    """Send flagged lines to LLM and get context-aware rewrites."""
    # Build context: surrounding lines for each flagged line
    all_lines = article_path.read_text().split('\n')
    
    context_blocks = []
    for line_num, line in sorted(flagged_lines.items()):
        start = max(0, line_num - 2)
        end = min(len(all_lines), line_num + 3)
        context = '\n'.join(f"L{i+1}: {all_lines[i]}" for i in range(start, end))
        context_blocks.append(context)
    
    if not context_blocks:
        return {}
    
    user_msg = f"""Article: {article_path.name}

Here are the prose sections with negation words that need affirmative rewrites. For each, provide the exact original text and its replacement.

{chr(10).join(context_blocks)}

Remember: preserve the author's direct voice. Output ONLY the JSON mapping of original→replacement."""

    try:
        r = requests.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {OPENROUTER_KEY}",
                "Content-Type": "application/json",
                "HTTP-Referer": "https://archonhq.ai",
            },
            json={
                "model": "z-ai/glm-5.1",
                "messages": [
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": user_msg},
                ],
                "temperature": 0.1,
                "max_tokens": 4096,
            },
            timeout=120,
        )
        
        if r.status_code == 200:
            text = r.json()["choices"][0]["message"]["content"]
            # Extract JSON from response
            json_match = re.search(r'\{[\s\S]*\}', text)
            if json_match:
                return json.loads(json_match.group())
        else:
            print(f"    LLM error: HTTP {r.status_code}", file=sys.stderr)
        return {}
    except Exception as e:
        print(f"    LLM exception: {e}", file=sys.stderr)
        return {}

def apply_fixes(article_path, fixes):
    """Apply LLM-suggested fixes to the article."""
    if not fixes:
        return 0
    
    content = article_path.read_text()
    applied = 0
    
    for original, replacement in fixes.items():
        if original in content:
            content = content.replace(original, replacement)
            applied += 1
        else:
            # Try case-insensitive match
            pattern = re.compile(re.escape(original), re.IGNORECASE)
            if pattern.search(content):
                content = pattern.sub(replacement, content, count=1)
                applied += 1
    
    if applied > 0:
        article_path.write_text(content)
    
    return applied

def process_article(article_path):
    """Process a single article: find negations, get LLM fixes, apply them."""
    content = article_path.read_text()
    prose_lines = extract_prose_blocks(content)
    flagged = find_negation_lines(prose_lines)
    
    if not flagged:
        return 0, 0
    
    print(f"  {article_path.name}: {len(flagged)} negation lines", end="")
    
    fixes = get_llm_fixes(article_path, flagged)
    applied = apply_fixes(article_path, fixes)
    
    print(f" → {applied} fixes applied")
    return len(flagged), applied

def main():
    total_flagged = 0
    total_fixed = 0
    
    for series_dir in sorted(ARTICLES_DIR.iterdir()):
        if not series_dir.is_dir() or not series_dir.name.endswith("-series"):
            continue
        
        print(f"\n{series_dir.name}/")
        for article in sorted(series_dir.glob("*.md")):
            flagged, fixed = process_article(article)
            total_flagged += flagged
            total_fixed += fixed
            if flagged > 0:
                time.sleep(2)  # Rate limit between LLM calls
    
    print(f"\n\nTotal: {total_flagged} flagged, {total_fixed} fixed")

if __name__ == "__main__":
    main()
