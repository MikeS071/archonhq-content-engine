#!/usr/bin/env python3
"""ArchonHQ Theme Manager — create, validate, list, show, and export series themes.

Usage:
    python3 theme_manager.py list                    # List all series and their themes
    python3 theme_manager.py show <series>           # Show full theme config
    python3 theme_manager.py create <series> [opts]  # Create a new series theme
    python3 theme_manager.py validate [series]       # Validate theme(s) against schema
    python3 theme_manager.py export <series>         # Export theme as standalone JSON
"""

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

# ── Config Integration ─────────────────────────────────────────────────────────

sys.path.insert(0, str(Path(__file__).parent))
from config import get_path, get_config

THEMES_DIR = get_path('series_themes_dir')


# ── Theme Schema Definition ────────────────────────────────────────────────────

THEME_SCHEMA = {
    "series_code":       {"type": str,  "required": True,  "description": "Two-letter series code (e.g. 'CB')"},
    "series_name":       {"type": str,  "required": True,  "description": "Full human-readable series name"},
    "tagline":           {"type": str,  "required": True,  "description": "One-line description / tagline"},
    "description":       {"type": str,  "required": True,  "description": "Longer description for README"},
    "prefix":            {"type": str,  "required": True,  "description": "Article prefix letter (e.g. 'M' for M15)"},
    "reader_magnet":     {"type": int,  "required": True,  "description": "Free article every N articles (0 = none, 1 = all free)"},
    "default_paywall":   {"type": str,  "required": True,  "choices": ["free", "paid"]},
    "voice":             {"type": dict, "required": True,  "description": "Voice configuration"},
    "topics":            {"type": list, "required": True,  "description": "Core topic areas"},
    "target_audience":   {"type": str,  "required": True,  "description": "Who reads this series"},
    "article_structure": {"type": list, "required": True,  "description": "Required article sections in order"},
    "prompt_toolkit_template": {"type": str, "required": True, "description": "XML template for this series' prompts"},
    "subreddits":        {"type": list, "required": True,  "description": "Target subreddits for distribution"},
    "tags_devto":        {"type": list, "required": True,  "description": "Dev.to tags"},
    "tags_substack":     {"type": list, "required": True,  "description": "Substack tags"},
}

VOICE_SCHEMA = {
    "person":    {"type": str, "required": True, "choices": ["first", "second", "third"]},
    "tone":      {"type": list, "required": True, "description": "Tone adjectives"},
    "forbidden": {"type": list, "required": True, "description": "Forbidden writing patterns"},
}


# ── Legacy Theme Migration ─────────────────────────────────────────────────────

def _migrate_legacy(legacy: dict) -> dict:
    """Convert old-format theme (caliber.json style) to new schema."""
    series_name = legacy.get("series", "Unknown")
    prefix = legacy.get("prefix", series_name[0] if series_name else "X")

    # Derive a two-letter code from the series name
    words = series_name.split()
    if len(words) >= 2:
        code = "".join(w[0] for w in words).upper()[:2]
    else:
        # Single-word name: take first two letters
        code = series_name[:2].upper()

    # Map legacy voice/tone info
    tone_raw = legacy.get("tone", "")
    tones = []
    if "opinionated" in tone_raw.lower():
        tones.append("opinionated")
    if "lean" in tone_raw.lower() or "zero fluff" in tone_raw.lower():
        tones.append("lean")
    if "technical" in tone_raw.lower() or "evidence" in tone_raw.lower():
        tones.append("technical")
    if "direct" in tone_raw.lower():
        tones.append("direct")
    if "authoritative" in tone_raw.lower():
        tones.append("authoritative")
    if "pragmatic" in tone_raw.lower() or "exact" in tone_raw.lower():
        tones.append("pragmatic")
    if not tones:
        tones = ["direct", "lean"]

    forbidden = []
    if "no em-dashes" in tone_raw.lower():
        forbidden.append("em-dashes")
    if "affirmative" in tone_raw.lower():
        forbidden.extend(["negation prose", "weasel words", "hedging"])
    if not forbidden:
        forbidden = ["em-dashes", "negation prose", "weasel words", "hedging"]

    paywall = legacy.get("paywall_default", "paid")
    reader_magnet = 0 if paywall == "paid" else 1

    # Map core_themes -> topics
    # Handle both list format (caliber/shipyard) and dict format (atlas/keystone)
    raw_themes = legacy.get("core_themes", [])
    if isinstance(raw_themes, dict):
        # Dict with "included" and optional "excluded"/"forbidden" keys
        topics = raw_themes.get("included", [])
        # Also extract forbidden_themes from dict if present
        if "forbidden" in raw_themes and "forbidden_themes" not in legacy:
            legacy["_migrated_forbidden"] = raw_themes.get("forbidden", [])
    elif isinstance(raw_themes, list):
        topics = raw_themes
    else:
        topics = []

    # Map article structure
    struct = legacy.get("article_structure", {})
    sections = struct.get("required_sections", [
        "Hook", "The Idea (60 Seconds)", "Why This Matters",
        "Walkthrough", "The Prompt Toolkit", "Caveats", "Philosophy"
    ])

    prompt_template = struct.get("prompt_toolkit", struct.get("prompt_toolkit_note", "XML prompts + Python CLI/script with argparse"))

    # Generate reasonable defaults for new fields
    return {
        "series_code": code,
        "series_name": series_name,
        "tagline": legacy.get("description", "").split(".")[0] if legacy.get("description") else f"{series_name} series",
        "description": legacy.get("description", ""),
        "prefix": prefix,
        "reader_magnet": reader_magnet,
        "default_paywall": paywall,
        "voice": {
            "person": "second",
            "tone": tones,
            "forbidden": forbidden,
        },
        "topics": topics,
        "target_audience": legacy.get("audience", ""),
        "article_structure": sections,
        "prompt_toolkit_template": prompt_template,
        "subreddits": [],
        "tags_devto": [],
        "tags_substack": [],
    }


# ── Theme Loading ──────────────────────────────────────────────────────────────

def _discover_themes() -> dict:
    """Load all theme JSON files from the themes directory."""
    themes = {}
    if not THEMES_DIR.exists():
        return themes
    for f in sorted(THEMES_DIR.glob("*.json")):
        try:
            data = json.loads(f.read_text())
            # Detect legacy format: has 'series' key but not 'series_name'
            if "series" in data and "series_name" not in data:
                data = _migrate_legacy(data)
            themes[f.stem] = data
        except (json.JSONDecodeError, Exception) as e:
            print(f"  ⚠  Could not load {f.name}: {e}", file=sys.stderr)
    return themes


def _load_theme(series: str) -> dict | None:
    """Load a single theme by series name (filename stem)."""
    path = THEMES_DIR / f"{series}.json"
    if not path.exists():
        return None
    data = json.loads(path.read_text())
    if "series" in data and "series_name" not in data:
        data = _migrate_legacy(data)
    return data


# ── Validation ─────────────────────────────────────────────────────────────────

def validate_theme(theme: dict, name: str = "") -> list[str]:
    """Validate a theme dict against the schema. Returns list of error strings."""
    errors = []

    # Check top-level required fields
    for key, spec in THEME_SCHEMA.items():
        if spec.get("required") and key not in theme:
            errors.append(f"Missing required field: {key}")
            continue
        if key not in theme:
            continue
        val = theme[key]
        expected_type = spec["type"]
        # Allow checking against choices
        if "choices" in spec and isinstance(val, str):
            if val not in spec["choices"]:
                errors.append(f"Field '{key}' value '{val}' not in allowed choices: {spec['choices']}")
        # Type check (use isinstance with some flexibility)
        if expected_type is str and not isinstance(val, str):
            errors.append(f"Field '{key}' expected str, got {type(val).__name__}")
        elif expected_type is int and not isinstance(val, int):
            errors.append(f"Field '{key}' expected int, got {type(val).__name__}")
        elif expected_type is list and not isinstance(val, list):
            errors.append(f"Field '{key}' expected list, got {type(val).__name__}")
        elif expected_type is dict and not isinstance(val, dict):
            errors.append(f"Field '{key}' expected dict, got {type(val).__name__}")

    # Validate voice sub-schema
    if "voice" in theme and isinstance(theme["voice"], dict):
        voice = theme["voice"]
        for key, spec in VOICE_SCHEMA.items():
            if spec.get("required") and key not in voice:
                errors.append(f"Missing required voice field: {key}")
                continue
            if key not in voice:
                continue
            val = voice[key]
            expected_type = spec["type"]
            if "choices" in spec and isinstance(val, str):
                if val not in spec["choices"]:
                    errors.append(f"Voice field '{key}' value '{val}' not in allowed choices: {spec['choices']}")
            if expected_type is str and not isinstance(val, str):
                errors.append(f"Voice field '{key}' expected str, got {type(val).__name__}")
            elif expected_type is list and not isinstance(val, list):
                errors.append(f"Voice field '{key}' expected list, got {type(val).__name__}")

    # Validate list contents are strings
    for list_key in ["topics", "article_structure", "subreddits", "tags_devto", "tags_substack"]:
        if list_key in theme and isinstance(theme[list_key], list):
            for i, item in enumerate(theme[list_key]):
                if not isinstance(item, str):
                    errors.append(f"{list_key}[{i}] expected str, got {type(item).__name__}")

    # Validate voice list contents
    if "voice" in theme and isinstance(theme["voice"], dict):
        for list_key in ["tone", "forbidden"]:
            if list_key in theme["voice"] and isinstance(theme["voice"][list_key], list):
                for i, item in enumerate(theme["voice"][list_key]):
                    if not isinstance(item, str):
                        errors.append(f"voice.{list_key}[{i}] expected str, got {type(item).__name__}")

    # Series code should be 2 chars
    if "series_code" in theme and isinstance(theme["series_code"], str):
        if len(theme["series_code"]) != 2:
            errors.append(f"series_code should be 2 characters, got '{theme['series_code']}' ({len(theme['series_code'])} chars)")

    # Prefix should be 1 char
    if "prefix" in theme and isinstance(theme["prefix"], str):
        if len(theme["prefix"]) != 1:
            errors.append(f"prefix should be 1 character, got '{theme['prefix']}'")

    # reader_magnet should be non-negative
    if "reader_magnet" in theme and isinstance(theme["reader_magnet"], int):
        if theme["reader_magnet"] < 0:
            errors.append(f"reader_magnet should be >= 0, got {theme['reader_magnet']}")

    return errors


# ── Default Theme Generation ───────────────────────────────────────────────────

def _generate_defaults(code: str, name: str, tagline: str, paywall: str,
                       topics: list[str], subreddits: list[str]) -> dict:
    """Generate a complete theme with reasonable defaults for all fields."""
    prefix = name[0].upper() if name else "X"
    reader_magnet = 0 if paywall == "paid" else 1

    # Generate topic-derived tags
    devto_tags = [t.lower().replace(" ", "").replace("&", "")[:20] for t in topics[:4]]
    substack_tags = [t.lower().split()[0] for t in topics[:3]]

    return {
        "series_code": code,
        "series_name": name,
        "tagline": tagline,
        "description": f"{name} — {tagline}. Full series description pending.",
        "prefix": prefix,
        "reader_magnet": reader_magnet,
        "default_paywall": paywall,
        "voice": {
            "person": "second",
            "tone": ["direct", "lean", "technical"],
            "forbidden": ["em-dashes", "negation prose", "weasel words", "hedging"],
        },
        "topics": topics,
        "target_audience": f"Readers interested in {', '.join(topics[:2]) if topics else 'this series'}",
        "article_structure": [
            "Hook",
            "The Idea (60 Seconds)",
            "Why This Matters",
            "Walkthrough",
            "The Prompt Toolkit",
            "Caveats",
            "Philosophy",
        ],
        "prompt_toolkit_template": (
            f'<prompt series="{code}">\n'
            f'  <context>You are writing for the {name} series.</context>\n'
            f'  <topic>{{{{topic}}}}</topic>\n'
            f'  <voice person="second" tone="direct,lean,technical" />\n'
            f'  <forbidden>em-dashes, negation prose, weasel words, hedging</forbidden>\n'
            f'  <structure>Hook → Idea → Why → Walkthrough → Prompt Toolkit → Caveats → Philosophy</structure>\n'
            f'</prompt>'
        ),
        "subreddits": subreddits,
        "tags_devto": devto_tags,
        "tags_substack": substack_tags,
    }


# ── CLI Commands ───────────────────────────────────────────────────────────────

def cmd_list(args):
    """List all series and their themes."""
    themes = _discover_themes()
    if not themes:
        print("No series themes found.")
        return

    print(f"{'Code':<5} {'Prefix':<7} {'Series Name':<25} {'Paywall':<8} {'Tagline'}")
    print("─" * 90)
    for stem, theme in themes.items():
        code = theme.get("series_code", "??")
        prefix = theme.get("prefix", "?")
        name = theme.get("series_name", stem)
        paywall = theme.get("default_paywall", "?")
        tagline = theme.get("tagline", "")[:45]
        print(f"{code:<5} {prefix:<7} {name:<25} {paywall:<8} {tagline}")

    print(f"\n{len(themes)} series found in {THEMES_DIR}")


def cmd_show(args):
    """Show full theme config for a series."""
    theme = _load_theme(args.series)
    if not theme:
        print(f"Theme '{args.series}' not found.", file=sys.stderr)
        sys.exit(1)
    print(json.dumps(theme, indent=2))


def cmd_create(args):
    """Create a new series theme."""
    # Check if theme already exists
    out_path = THEMES_DIR / f"{args.series}.json"
    if out_path.exists():
        print(f"Theme '{args.series}' already exists at {out_path}", file=sys.stderr)
        sys.exit(1)

    # Parse options
    code = args.code or args.series[:2].upper()
    name = args.name or args.series.title()
    tagline = args.tagline or f"{name} series"
    paywall = args.paywall or "paid"
    topics = [t.strip() for t in (args.topics or "").split(",") if t.strip()] or ["general"]
    subreddits = [s.strip() for s in (args.subreddits or "").split(",") if s.strip()] or []

    # Validate code length
    if len(code) != 2:
        print(f"Warning: series_code '{code}' is not 2 characters. Using '{code}' anyway.", file=sys.stderr)

    # Generate theme with defaults
    theme = _generate_defaults(code, name, tagline, paywall, topics, subreddits)

    # Validate before saving
    errors = validate_theme(theme, args.series)
    if errors:
        print("Generated theme has validation errors:", file=sys.stderr)
        for e in errors:
            print(f"  ✗ {e}", file=sys.stderr)
        print("\nSaving anyway — run 'validate' to check.", file=sys.stderr)

    # Save
    THEMES_DIR.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(theme, indent=2) + "\n")
    print(f"✓ Created theme: {out_path}")
    print(f"  Code: {code}  Name: {name}  Paywall: {paywall}")
    print(f"  Topics: {', '.join(topics)}")
    print(f"  Subreddits: {', '.join(subreddits) if subreddits else '(none)'}")


def cmd_validate(args):
    """Validate theme(s) against schema."""
    if args.series:
        themes = {args.series: _load_theme(args.series)}
        if themes[args.series] is None:
            print(f"Theme '{args.series}' not found.", file=sys.stderr)
            sys.exit(1)
    else:
        themes = _discover_themes()

    if not themes:
        print("No themes to validate.")
        return

    total_errors = 0
    for stem, theme in themes.items():
        errors = validate_theme(theme, stem)
        if errors:
            print(f"✗ {stem}:")
            for e in errors:
                print(f"    {e}")
            total_errors += len(errors)
        else:
            print(f"✓ {stem}: valid")

    if total_errors:
        print(f"\n{total_errors} error(s) found.")
        sys.exit(1)
    else:
        print(f"\nAll {len(themes)} theme(s) valid.")


def cmd_export(args):
    """Export theme as standalone JSON (to stdout or file)."""
    theme = _load_theme(args.series)
    if not theme:
        print(f"Theme '{args.series}' not found.", file=sys.stderr)
        sys.exit(1)

    output = json.dumps(theme, indent=2) + "\n"

    if args.output:
        Path(args.output).write_text(output)
        print(f"✓ Exported to {args.output}")
    else:
        print(output)


# ── Main ───────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="ArchonHQ Series Theme Manager",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""Examples:
  theme_manager.py list
  theme_manager.py show caliber
  theme_manager.py create vanguard --name "Vanguard" --code "VG" --tagline "Cutting-edge AI research"
  theme_manager.py create pulse --name "Pulse" --paywall free --topics "AI news,trends"
  theme_manager.py validate
  theme_manager.py validate caliber
  theme_manager.py export shipyard
  theme_manager.py export shipyard --output shipyard_theme.json""",
    )

    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # list
    sub_list = subparsers.add_parser("list", help="List all series and their themes")

    # show
    sub_show = subparsers.add_parser("show", help="Show full theme config")
    sub_show.add_argument("series", help="Series name (filename stem)")

    # create
    sub_create = subparsers.add_parser("create", help="Create a new series theme")
    sub_create.add_argument("series", help="Series identifier (filename stem)")
    sub_create.add_argument("--name", help="Full series name (default: title-cased identifier)")
    sub_create.add_argument("--code", help="Two-letter series code (default: first 2 letters of name)")
    sub_create.add_argument("--tagline", help="One-line description / tagline")
    sub_create.add_argument("--paywall", choices=["free", "paid"], default="paid",
                            help="Default paywall setting (default: paid)")
    sub_create.add_argument("--topics", help="Comma-separated topic list")
    sub_create.add_argument("--subreddits", help="Comma-separated subreddit list (e.g. r/sub1,r/sub2)")

    # validate
    sub_validate = subparsers.add_parser("validate", help="Validate theme(s) against schema")
    sub_validate.add_argument("series", nargs="?", help="Specific series to validate (default: all)")

    # export
    sub_export = subparsers.add_parser("export", help="Export theme as standalone JSON")
    sub_export.add_argument("series", help="Series name (filename stem)")
    sub_export.add_argument("--output", "-o", help="Output file path (default: stdout)")

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(1)

    commands = {
        "list": cmd_list,
        "show": cmd_show,
        "create": cmd_create,
        "validate": cmd_validate,
        "export": cmd_export,
    }

    commands[args.command](args)


if __name__ == "__main__":
    main()
