#!/usr/bin/env python3
"""ArchonHQ QA Engine — runs 8-check quality checklist on drafts, auto-fixes where possible.
Second pass uses LLM-powered semantic checks for deeper quality analysis.

Usage:
    python3 qa_engine.py [--article PATH] [--all] [--skip-llm]

If --all, processes all drafts in articles dir with status "draft".
If --article, processes a specific file.
If --skip-llm, runs regex checks only (fast mode, no LLM call).
Otherwise, processes drafts from today's generation.
"""

import argparse
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

# ── Config ──────────────────────────────────────────────────────────────────
from config import get_config, get_path, get_qa_rules, get_word_count, update_pipeline_state
from config import get_model, get_api, load_env

_cfg = get_config()

ROOT = get_path('content_root', _cfg)
ARTICLES_DIR = get_path('articles_dir', _cfg)
QA_REPORTS_DIR = get_path('qa_reports_dir', _cfg)
MIN_WORD_COUNT = get_word_count('min_word_count', _cfg)

# Blog pattern elements that must be present — loaded from config
_qa_rules = get_qa_rules(_cfg)
REQUIRED_SECTIONS = _qa_rules.get('required_sections', ["hook", "idea", "why", "walkthrough", "caveat", "philosophy", "cta"])

# ── Checks ──────────────────────────────────────────────────────────────────

# Load negation patterns from config
NEGATION_PATTERNS = []
for np in _qa_rules.get('negation_patterns', []):
    if isinstance(np, dict):
        NEGATION_PATTERNS.append((np.get('pattern', ''), np.get('label', '')))
    else:
        NEGATION_PATTERNS.append((np, ''))

WEASEL_WORDS = _qa_rules.get('weasel_words', [
    "arguably", "somewhat", "in some ways", "it could be said",
    "argueably", "perhaps", "maybe", "possibly",
])

HEDGE_PHRASES = [
    "it depends", "on the other hand",
]

EM_DASH = "\u2014"
EN_DASH = "\u2013"

# ── Affirmative substitution table ──────────────────────────────────────────

AFFIRMATIVE_SUBS = {
    "doesn't work": "fails",
    "doesn't have": "lacks",
    "doesn't return": "omits",
    "don't": "avoid",
    "can't": "struggles to",
    "won't": "refuses to",
    "never": "has yet to",
    "nobody": "goes unmentioned",
    "nothing": "zero",
    "without": "independently of",
    "insufficient": "falls short",
    "unacceptable": "fails to meet standards",
    "not functional": "decorative",
}


def log(msg):
    print(f"[qa] {msg}", file=sys.stderr)


def read_article(path):
    """Read article, separating frontmatter from body."""
    text = path.read_text()
    if text.startswith("---"):
        parts = text.split("---", 2)
        if len(parts) >= 3:
            frontmatter = parts[1].strip()
            body = parts[2].strip()
            return frontmatter, body
    return "", text


def check_em_dashes(body):
    """Check 1: Em-dash sweep."""
    matches = list(re.finditer(EM_DASH, body))
    return len(matches) == 0, matches


def check_en_dashes(body):
    """Check 2: En-dash sweep (only flag in prose, not in date ranges etc)."""
    # Flag en-dashes used as prose punctuation (surrounded by spaces)
    pattern = r'\s\u2013\s'
    matches = list(re.finditer(pattern, body))
    return len(matches) == 0, matches


def check_negation(body):
    """Check 3: Negation prose sweep."""
    findings = []
    # Code-block and heading skip patterns
    code_keywords = [
        'if not', 'is not none', 'is not', '!=', 'not in ', 'not null',
        'not the client', 'if not clients', 'not in\n',
        'not installed', 'not found', 'not yet', 'not enough',
        'not a valid', 'not supported', 'not available',
        'not be', 'not have', 'not only', 'not just',
        'no @', 'no `@`',  # CLI syntax like "no @ suffix"
    ]
    # Also skip inside code blocks (even if not indented)
    in_code = False
    code_lines = set()
    lines = body.split('\n')
    cb_start = None
    for li, line in enumerate(lines):
        if line.strip().startswith('```'):
            if cb_start is None:
                cb_start = li
            else:
                for cl in range(cb_start, li + 1):
                    code_lines.add(cl)
                cb_start = None
    # If unclosed code block, mark everything from start to end
    if cb_start is not None:
        for cl in range(cb_start, len(lines)):
            code_lines.add(cl)
    
    for pattern, label in NEGATION_PATTERNS:
        for m in re.finditer(pattern, body, re.IGNORECASE):
            # Skip code blocks by line number
            line_num = body[:m.start()].count('\n')
            if line_num in code_lines:
                continue
            # Skip indented code
            line_start = body.rfind('\n', 0, m.start()) + 1
            line_end = body.find('\n', m.start())
            line = body[line_start:line_end] if line_end != -1 else body[line_start:]
            if line.strip().startswith('    '):
                continue
            # Skip headings
            if line.strip().startswith('#'):
                continue
            # Skip image references (hero images may contain negation words in filenames)
            if line.strip().startswith('!['):
                continue
            # Skip table rows
            if line.strip().startswith('|'):
                continue
            # Skip code-adjacent patterns
            context = line.strip().lower()
            if any(kw in context for kw in code_keywords):
                continue
            # Skip early-warning/example tags (showing bad client language)
            if '<early-warning>' in line or '</early-warning>' in line:
                continue
            findings.append((m.start(), m.group(), label))
    return len(findings) == 0, findings


def check_weasel_words(body):
    """Check 4: Weasel word sweep."""
    findings = []
    body_lower = body.lower()
    for word in WEASEL_WORDS:
        idx = 0
        while True:
            idx = body_lower.find(word, idx)
            if idx == -1:
                break
            # Skip code blocks
            line_start = body.rfind('\n', 0, idx) + 1
            line = body[line_start:].split('\n')[0]
            if not (line.strip().startswith('```') or line.strip().startswith('    ')):
                findings.append((idx, word))
            idx += len(word)
    return len(findings) == 0, findings


def check_hedging(body):
    """Check 5: Hedging sweep."""
    findings = []
    body_lower = body.lower()
    # Skip lines inside early-warning/example tags
    lines = body.split('\n')
    exempt_lines = set()
    for i, line in enumerate(lines):
        if '<early-warning>' in line or '</early-warning>' in line:
            exempt_lines.add(i)
        if 'early-warning language' in line.lower():
            exempt_lines.add(i)
    for phrase in HEDGE_PHRASES:
        idx = body_lower.find(phrase)
        while idx != -1:
            line_num = body[:idx].count('\n')
            line_start = body.rfind('\n', 0, idx) + 1
            line = body[line_start:].split('\n')[0]
            if line_num in exempt_lines:
                pass  # skip early-warning examples
            elif not (line.strip().startswith('```') or line.strip().startswith('    ')):
                findings.append((idx, phrase))
            idx = body_lower.find(phrase, idx + len(phrase))
    # Check "however" used as softening (not in technical instructions)
    for m in re.finditer(r'\bHowever\b', body):
        line_num = body[:m.start()].count('\n')
        line_start = body.rfind('\n', 0, m.start()) + 1
        line = body[line_start:].split('\n')[0]
        if line_num in exempt_lines:
            pass
        elif not (line.strip().startswith('```') or line.strip().startswith('    ')):
            # Check context: if "however" starts a sentence and softens a claim
            context = body[max(0, m.start()-20):m.start()+30].lower()
            if 'however' in context and not any(t in context for t in ['however many', 'however much', 'however long']):
                findings.append((m.start(), "however (hedging)"))
    return len(findings) == 0, findings


def check_blog_pattern(body):
    """Check 6: Blog pattern — verify required structural elements present."""
    body_lower = body.lower()
    headings = re.findall(r'^#+\s+(.+)$', body, re.MULTILINE)
    headings_lower = [h.lower() for h in headings]
    
    # Flexible matching: accept variations of required sections
    SECTION_VARIANTS = {
        "hook": ["hook"],
        "idea": ["idea", "the idea", "what you'll build", "what this gives you"],
        "why": ["why this", "why", "over the alternat", "why you need", "why build"],
        "walkthrough": ["walkthrough", "detailed walkthrough", "build it", "step", "layer", "how to"],
        "caveat": ["caveat", "honest caveat", "realistic", "what breaks", "where this", "limitation", "what doesn't work"],
        "philosophy": ["philosophy", "compounding", "so what", "the bigger", "the compound"],
        "cta": ["cta", "call to action", "build your own", "subscribe", "your turn", "which layer", "what should you"],
    }
    
    found = {}
    for section, variants in SECTION_VARIANTS.items():
        matches = [h for h in headings_lower if any(v in h for v in variants)]
        found[section] = len(matches) > 0
    
    # Only require core 5 (hook, idea, why, walkthrough, caveat)
    required = ["hook", "idea", "why", "walkthrough", "caveat"]
    all_present = all(found.get(r, False) for r in required)
    return all_present, found


def check_duplication(body):
    """Check 7: Check for duplicated stats/numbers across sections."""
    # Find repeated numbers/stats (e.g., percentages, specific figures)
    numbers = re.findall(r'\b\d+\.?\d*%?\b', body)
    # Count occurrences — numbers appearing 4+ times with 2+ digits might be duplicated
    from collections import Counter
    counts = Counter(numbers)
    # Only flag numbers with 2+ digits (skip single digits, 2-digit times like "30", "00")
    dupes = {n: c for n, c in counts.items() if c >= 4 and len(n) >= 3 and not n.endswith('%')}
    # Exception: common cron times, word counts, version strings, config values, HTTP codes, test data
    exceptions = {'800', '1536', '1024', '1100', '550', '2026', '2.0', '2.1', '0.1', '0.067',
                  '033', '000', '200', '401', '404', '500', '12345', '3.1', '0.95', '3.5'}
    dupes = {n: c for n, c in dupes.items() if n not in exceptions}
    return len(dupes) == 0, dupes


def check_word_count(body):
    """Check 8: Word count >= 800."""
    wc = len(body.split())
    return wc >= MIN_WORD_COUNT, wc


# ── Auto-fix functions ──────────────────────────────────────────────────────

def fix_em_dashes(body):
    """Replace all em-dashes with comma or period."""
    # Mid-sentence aside: replace with comma
    body = body.replace(EM_DASH, ",")
    return body


def fix_en_dashes(body):
    """Replace en-dashes used as prose punctuation with comma."""
    body = re.sub(r'\s\u2013\s', ', ', body)
    return body


def fix_negation(body):
    """Auto-fix known negation patterns with affirmative substitutions."""
    for neg, aff in AFFIRMATIVE_SUBS.items():
        body = re.sub(re.escape(neg), aff, body, flags=re.IGNORECASE)
    return body


def fix_weasel(body):
    """Delete weasel words."""
    for word in WEASEL_WORDS:
        # Remove the word, clean up extra spaces
        pattern = re.compile(r'\b' + re.escape(word) + r'\b[, ]*', re.IGNORECASE)
        body = pattern.sub('', body)
    # Clean up double spaces
    body = re.sub(r'  +', ' ', body)
    return body


def fix_blog_pattern(body):
    """Auto-fix missing Hook heading by inserting it before first content paragraph."""
    headings = re.findall(r'^##+\s+', body, re.MULTILINE)
    if headings:
        return body  # Has sub-headings, don't mess with it
    
    lines = body.split('\n')
    for i, line in enumerate(lines):
        if line.startswith('# ') and i == 0:
            continue
        if line.startswith('!['):
            continue
        if line.strip() == '':
            continue
        # First content line — insert Hook heading
        lines.insert(i, '## Hook')
        lines.insert(i + 1, '')
        return '\n'.join(lines)
    return body


def fix_paywall_marker(body, frontmatter):
    """Add <!--paid--> for paid articles missing it."""
    if 'paywall: paid' not in frontmatter:
        return body
    if '<!--paid-->' in body:
        return body
    # Insert before Walkthrough or Caveats
    for heading in ['## Walkthrough', '## Detailed Walkthrough', '## Caveat']:
        idx = body.find(heading)
        if idx != -1:
            return body[:idx] + '<!--paid-->\n\n' + body[idx:]
    return body


# ── LLM-powered semantic QA ────────────────────────────────────────────────

LLM_QA_SYSTEM_PROMPT = """\
You are the ArchonHQ quality analyst. You evaluate blog articles against the
ArchonHQ voice and style rules. The rules are strict and non-negotiable:

VOICE RULES:
- First person ("I", "we"), opinionated, lean prose
- NO em-dashes (—) or en-dashes (–) used as prose punctuation
- NO negation prose — rewrite "doesn't work" as "fails", "can't" as "struggles to", etc.
- NO hedging — no "arguably", "somewhat", "perhaps", "it depends", "however" as softening
- NO weasel words — no "in some ways", "it could be said"
- Affirmative voice only — say what IS, not what ISN'T
- Concrete and specific — real numbers, named tools, specific examples
- Every section must build on the previous one in a logical chain
- Opening hook must create urgency and specificity (not generic filler)
- Reader must be able to take immediate action after reading

You will receive an article and must evaluate it on 6 dimensions.
Return ONLY valid JSON with this exact structure — no markdown, no commentary:

{
  "voice_consistency": {
    "score": <int 1-10>,
    "issues": ["<line N>: <description>", ...]
  },
  "hook_strength": {
    "score": <int 1-10>,
    "issues": ["<line N>: <description>", ...]
  },
  "specificity": {
    "score": <int 1-10>,
    "issues": ["<line N>: <description>", ...]
  },
  "logical_flow": {
    "score": <int 1-10>,
    "issues": ["<section transition>: <description>", ...]
  },
  "affirmative_prose": {
    "score": <int 1-10>,
    "issues": ["<line N>: <remaining negation pattern>", ...]
  },
  "actionability": {
    "score": <int 1-10>,
    "issues": ["<line N>: <description>", ...]
  }
}

Scoring guide:
- 9-10: Excellent, fully meets ArchonHQ standard
- 7-8: Good, minor issues only
- 5-6: Needs work, notable violations
- 1-4: Fails this dimension, major rework needed

For each issue, reference the specific line number and describe exactly what's wrong.
If a dimension has no issues, return an empty issues list.
"""


def run_llm_qa(article_text, series_name=""):
    """Run LLM-powered semantic QA checks via OpenRouter API.

    Evaluates 6 dimensions: voice consistency, hook strength, specificity,
    logical flow, affirmative prose, and actionability.

    Args:
        article_text: Full article body text (no frontmatter).
        series_name: Optional series name for context.

    Returns:
        dict with keys: voice_consistency, hook_strength, specificity,
        logical_flow, affirmative_prose, actionability.
        Each value is {"score": int, "issues": [str, ...]}.
        Returns None if the LLM call fails.
    """
    import urllib.request
    import urllib.error

    try:
        model = get_model('qa', _cfg) or "z-ai/glm-5.1"
        api_url = get_api('openrouter', _cfg)
        env = load_env(_cfg)
        api_key = env.get('OPENROUTER_API_KEY', os.environ.get('OPENROUTER_API_KEY', ''))

        if not api_key:
            log("  LLM QA skipped: no OPENROUTER_API_KEY found")
            return None

        if not api_url:
            api_url = "https://openrouter.ai/api/v1/chat/completions"

        # Build the user message with the article and context
        series_context = f"\nSeries: {series_name}" if series_name else ""
        user_msg = (
            f"Evaluate this ArchonHQ article on the 6 quality dimensions.{series_context}\n\n"
            f"--- ARTICLE START ---\n{article_text}\n--- ARTICLE END ---\n\n"
            f"Return JSON with scores (1-10) and specific line-referenced issues for each dimension."
        )

        payload = json.dumps({
            "model": model,
            "messages": [
                {"role": "system", "content": LLM_QA_SYSTEM_PROMPT},
                {"role": "user", "content": user_msg},
            ],
            "temperature": 0.2,
            "max_tokens": 2048,
        }).encode("utf-8")

        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
            "HTTP-Referer": "https://archonhq.com",
            "X-Title": "ArchonHQ QA Engine",
        }

        req = urllib.request.Request(api_url, data=payload, headers=headers, method="POST")

        log(f"  Calling LLM QA ({model})...")
        with urllib.request.urlopen(req, timeout=120) as resp:
            resp_data = json.loads(resp.read().decode("utf-8"))

        # Extract the content from the response
        content = resp_data.get("choices", [{}])[0].get("message", {}).get("content", "")
        if not content:
            log("  LLM QA: empty response from model")
            return None

        # Strip markdown code fences if present
        content = content.strip()
        if content.startswith("```"):
            first_newline = content.index("\n") if "\n" in content else len(content)
            content = content[first_newline + 1:]
            if content.endswith("```"):
                content = content[:-3]
            content = content.strip()

        # Parse JSON response
        result = json.loads(content)

        # Validate expected keys
        expected_keys = {
            "voice_consistency", "hook_strength", "specificity",
            "logical_flow", "affirmative_prose", "actionability"
        }
        if not expected_keys.issubset(result.keys()):
            missing = expected_keys - result.keys()
            log(f"  LLM QA: missing keys in response: {missing}")
            # Fill in any missing keys with defaults
            for key in expected_keys:
                if key not in result:
                    result[key] = {"score": 0, "issues": [f"LLM did not evaluate {key}"]}
                elif "score" not in result[key]:
                    result[key]["score"] = 0
                elif "issues" not in result[key]:
                    result[key]["issues"] = []

        # Ensure scores are ints
        for key in expected_keys:
            try:
                result[key]["score"] = int(result[key]["score"])
            except (ValueError, TypeError):
                result[key]["score"] = 0

        return result

    except urllib.error.HTTPError as e:
        body = ""
        try:
            body = e.read().decode("utf-8", errors="replace")
        except Exception:
            pass
        log(f"  LLM QA HTTP error {e.code}: {body[:200]}")
        return None
    except urllib.error.URLError as e:
        log(f"  LLM QA network error: {e.reason}")
        return None
    except json.JSONDecodeError as e:
        log(f"  LLM QA: failed to parse model response as JSON: {e}")
        return None
    except Exception as e:
        log(f"  LLM QA unexpected error: {type(e).__name__}: {e}")
        return None


# ── Main QA pipeline ───────────────────────────────────────────────────────

def run_qa(article_path, auto_fix=True, skip_llm=False):
    """Run all checks on an article. Returns (status, report).

    Flow:
      1. Run regex checks (cheap, mechanical)
      2. Auto-fix where possible
      3. If regex passes, run LLM QA (semantic, deeper analysis)
      4. Combine results; determine final status
    """
    log(f"Checking: {article_path.name}")
    frontmatter, body = read_article(article_path)
    
    results = {}
    fixes_applied = []
    
    # Run checks
    results["em_dash"] = check_em_dashes(body)
    results["en_dash"] = check_en_dashes(body)
    results["negation"] = check_negation(body)
    results["weasel"] = check_weasel_words(body)
    results["hedging"] = check_hedging(body)
    results["pattern"] = check_blog_pattern(body)
    results["duplication"] = check_duplication(body)
    results["word_count"] = check_word_count(body)
    
    # Check 9: Paywall marker for paid articles
    paywall_match = re.search(r'paywall:\s*(paid|free)', frontmatter)
    if paywall_match and paywall_match.group(1) == 'paid':
        has_marker = '<!--paid-->' in body
        results["paywall_marker"] = (has_marker, "missing <!--paid--> marker" if not has_marker else "OK")
    
    passed = {k: v[0] for k, v in results.items()}
    
    # Auto-fix if requested (only safe mechanical fixes)
    if auto_fix:
        modified = body
        if not passed["em_dash"]:
            modified = fix_em_dashes(modified)
            fixes_applied.append("em-dash → comma")
        if not passed["en_dash"]:
            modified = fix_en_dashes(modified)
            fixes_applied.append("en-dash → comma")
        # NOTE: negation and weasel fixes are too blunt for auto-apply.
        # They are flagged for manual review instead.
        # if not passed["negation"]:
        #     modified = fix_negation(modified)
        #     fixes_applied.append("negation → affirmative")
        # if not passed["weasel"]:
        #     modified = fix_weasel(modified)
        #     fixes_applied.append("weasel words deleted")
        if not passed["pattern"]:
            modified = fix_blog_pattern(modified)
            fixes_applied.append("added ## Hook heading")
        # Always check and fix paywall marker
        modified = fix_paywall_marker(modified, frontmatter)
        if '<!--paid-->' in modified and '<!--paid-->' not in body:
            fixes_applied.append("added <!--paid--> paywall marker")
        
        if modified != body:
            # Re-check after fixes
            body = modified
            results["em_dash"] = check_em_dashes(body)
            results["en_dash"] = check_en_dashes(body)
            results["negation"] = check_negation(body)
            results["weasel"] = check_weasel_words(body)
            
            # Write back
            full_text = f"---\n{frontmatter}\n---\n\n{body}"
            article_path.write_text(full_text)
            log(f"  Applied fixes: {', '.join(fixes_applied)}")
    
    # Determine regex status
    recheck = {k: v[0] for k, v in results.items()}
    regex_all_pass = all(recheck.values())
    
    if regex_all_pass:
        status = "qa_passed"
    elif fixes_applied:
        status = "qa_fixed"
    else:
        status = "qa_failed"
    
    # Build report
    report = {
        "article": article_path.name,
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "status": status,  # may be updated after LLM pass
        "checks": {},
        "llm_checks": None,  # populated by LLM pass below
        "fixes_applied": fixes_applied,
    }
    
    check_names = {
        "em_dash": "Em-dash sweep",
        "en_dash": "En-dash sweep",
        "negation": "Negation prose",
        "weasel": "Weasel words",
        "hedging": "Hedging",
        "pattern": "Blog pattern",
        "duplication": "Duplication",
        "word_count": "Word count",
        "paywall_marker": "Paywall marker",
    }
    
    for key, (passed, detail) in results.items():
        report["checks"][check_names[key]] = {
            "pass": passed,
            "detail": str(detail)[:200] if not passed else "OK",
        }
    
    # ── LLM QA second pass ────────────────────────────────────────────────
    # Only run if regex checks all pass and --skip-llm is not set
    llm_result = None
    if regex_all_pass and not skip_llm:
        # Extract series name from frontmatter for LLM context
        series_name = ""
        series_match = re.search(r'^series:\s*(.+)$', frontmatter, re.MULTILINE)
        if series_match:
            series_name = series_match.group(1).strip()
        
        llm_result = run_llm_qa(body, series_name)
        
        if llm_result is not None:
            report["llm_checks"] = llm_result
            
            # Check if all LLM scores >= 7
            llm_scores = [
                llm_result[k]["score"]
                for k in ("voice_consistency", "hook_strength", "specificity",
                          "logical_flow", "affirmative_prose", "actionability")
            ]
            llm_all_pass = all(s >= 7 for s in llm_scores)
            llm_avg = sum(llm_scores) / len(llm_scores) if llm_scores else 0
            
            if llm_all_pass:
                status = "qa_passed"
                log(f"  LLM QA passed (avg score: {llm_avg:.1f})")
            else:
                status = "draft"  # stay in draft — needs rework based on LLM feedback
                failed_dims = [k for k in ("voice_consistency", "hook_strength",
                              "specificity", "logical_flow", "affirmative_prose",
                              "actionability") if llm_result[k]["score"] < 7]
                log(f"  LLM QA failed (avg score: {llm_avg:.1f}, failed: {', '.join(failed_dims)})")
            
            report["status"] = status
            report["llm_avg_score"] = round(llm_avg, 1)
        else:
            # LLM call failed — keep regex result but note it in report
            report["llm_checks"] = {"error": "LLM QA call failed, regex-only result used"}
            log("  LLM QA unavailable — keeping regex-only result")
    elif skip_llm:
        log("  LLM QA skipped (--skip-llm flag)")
        report["llm_checks"] = {"skipped": True, "reason": "--skip-llm flag"}
    else:
        # Regex checks didn't all pass — no point running LLM
        log("  LLM QA skipped (regex checks not all passing)")
        report["llm_checks"] = {"skipped": True, "reason": "regex checks did not all pass"}
    
    # Update frontmatter status
    if frontmatter:
        updated_fm = re.sub(r'status:\s*\w+', f'status: {status}', frontmatter)
        if updated_fm != frontmatter:
            full_text = f"---\n{updated_fm}\n---\n\n{body}"
            article_path.write_text(full_text)
    
    # Update pipeline state
    try:
        details = {"title": article_path.stem.replace("-", " ")}
        # Extract series from frontmatter
        series_match = re.search(r'^series:\s*(.+)$', frontmatter, re.MULTILINE)
        if series_match:
            details["series"] = series_match.group(1).strip()
        # Include QA score and issues
        if llm_result is not None:
            llm_scores = [llm_result[k]["score"]
                          for k in ("voice_consistency", "hook_strength", "specificity",
                                    "logical_flow", "affirmative_prose", "actionability")]
            details["qa_score"] = round(sum(llm_scores) / len(llm_scores), 1)
            all_issues = []
            for k in ("voice_consistency", "hook_strength", "specificity",
                      "logical_flow", "affirmative_prose", "actionability"):
                all_issues.extend(llm_result[k].get("issues", []))
            details["qa_issues"] = all_issues
        elif not regex_all_pass:
            # Collect regex failures as issues
            regex_issues = [f"{check_names[k]}: {str(v[1])[:100]}"
                           for k, v in results.items() if not v[0]]
            details["qa_issues"] = regex_issues
        
        update_pipeline_state(article_path.name, status, details, _cfg)
    except Exception as e:
        log(f"  Pipeline state update failed: {e}")
    
    log(f"  Result: {status}")
    return status, report


def find_drafts():
    """Find all articles with status 'draft', 'qa_fixed', or 'qa_passed' in frontmatter."""
    drafts = []
    if not ARTICLES_DIR.exists():
        return drafts
    for f in ARTICLES_DIR.rglob("*.md"):
        text = f.read_text()
        if re.search(r'^status:\s*(draft|qa_fixed|qa_passed)\s*$', text, re.MULTILINE):
            drafts.append(f)
    return drafts


def run(article_path=None, all_drafts=False, no_fix=False, skip_llm=False):
    QA_REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    
    if article_path:
        paths = [Path(article_path)]
    elif all_drafts:
        paths = find_drafts()
        log(f"Found {len(paths)} drafts to check")
    else:
        # Default: check today's drafts
        paths = find_drafts()
        log(f"Found {len(paths)} drafts to check")
    
    if not paths:
        log("No drafts found to check.")
        return
    
    reports = []
    for path in paths:
        status, report = run_qa(path, auto_fix=not no_fix, skip_llm=skip_llm)
        reports.append(report)
    
    # Save combined report
    report_file = QA_REPORTS_DIR / f"qa_{datetime.now().strftime('%Y%m%d_%H%M')}.json"
    report_file.write_text(json.dumps(reports, indent=2))
    log(f"\n✓ QA report saved to {report_file}")
    
    # Summary
    statuses = [r["status"] for r in reports]
    log(f"Summary: {statuses.count('qa_passed')} passed, {statuses.count('qa_fixed')} fixed, {statuses.count('qa_failed')} failed, {statuses.count('draft')} draft (LLM failed)")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="ArchonHQ QA Engine")
    parser.add_argument("--article", type=str, help="Specific article path to check")
    parser.add_argument("--all", action="store_true", dest="all_drafts", help="Check all drafts")
    parser.add_argument("--no-fix", action="store_true", help="Don't auto-fix, just report")
    parser.add_argument("--skip-llm", action="store_true", help="Skip LLM semantic checks (regex only)")
    args = parser.parse_args()

    run(article_path=args.article, all_drafts=args.all_drafts, no_fix=args.no_fix, skip_llm=args.skip_llm)
