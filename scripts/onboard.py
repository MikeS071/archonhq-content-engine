#!/usr/bin/env python3
"""Interactive onboarding for Content Engine.

Creates local, git-ignored configuration from a user's brand/content answers:
- config.yaml
- series_themes/<primary-series>.json
- content_profile.md
- empty runtime state templates

No articles, images, metrics, or private content are generated.
"""

from __future__ import annotations

import argparse
import json
import os
import re
from pathlib import Path
from textwrap import dedent

ROOT = Path(__file__).resolve().parents[1]


def ask(prompt: str, default: str | None = None, required: bool = True) -> str:
    suffix = f" [{default}]" if default else ""
    while True:
        value = input(f"{prompt}{suffix}: ").strip()
        if not value and default is not None:
            return default
        if value or not required:
            return value
        print("  Required. Give me something to work with.")


def ask_list(prompt: str, default: list[str] | None = None, min_items: int = 1) -> list[str]:
    default_text = ", ".join(default or [])
    raw = ask(prompt, default_text if default else None)
    items = [item.strip() for item in re.split(r"[,\n]", raw) if item.strip()]
    while len(items) < min_items:
        print(f"  Need at least {min_items} item(s). Separate with commas.")
        raw = ask(prompt, default_text if default else None)
        items = [item.strip() for item in re.split(r"[,\n]", raw) if item.strip()]
    return items


def slugify(text: str) -> str:
    text = text.lower().strip()
    text = re.sub(r"[^a-z0-9]+", "-", text)
    return text.strip("-") or "content-series"


def yes_no(prompt: str, default: bool = False) -> bool:
    d = "y" if default else "n"
    while True:
        raw = ask(prompt, d, required=False).lower()
        if raw in {"y", "yes"}:
            return True
        if raw in {"n", "no"}:
            return False
        print("  Answer y or n.")


def write_yaml(path: Path, data: dict) -> None:
    """Tiny YAML writer for simple dict/list/scalar structures."""

    def render(obj, indent=0):
        pad = " " * indent
        lines: list[str] = []
        if isinstance(obj, dict):
            for key, value in obj.items():
                if isinstance(value, (dict, list)):
                    lines.append(f"{pad}{key}:")
                    lines.extend(render(value, indent + 2))
                else:
                    lines.append(f"{pad}{key}: {scalar(value)}")
        elif isinstance(obj, list):
            for item in obj:
                if isinstance(item, (dict, list)):
                    lines.append(f"{pad}-")
                    lines.extend(render(item, indent + 2))
                else:
                    lines.append(f"{pad}- {scalar(item)}")
        return lines

    def scalar(value):
        if value is None:
            return "null"
        if isinstance(value, bool):
            return "true" if value else "false"
        if isinstance(value, (int, float)):
            return str(value)
        s = str(value).replace('"', '\\"')
        return f'"{s}"'

    path.write_text("\n".join(render(data)) + "\n")


def main() -> int:
    parser = argparse.ArgumentParser(description="Onboard a Content Engine install once per brand/publication.")
    parser.add_argument(
        "--force",
        action="store_true",
        help="Re-run onboarding and overwrite local brand voice/config files.",
    )
    args = parser.parse_args()

    existing = [ROOT / "config.yaml", ROOT / "content_profile.md"]
    if all(path.exists() for path in existing) and not args.force:
        print("\nContent Engine is already onboarded.")
        print(f"  Existing config: {ROOT / 'config.yaml'}")
        print(f"  Existing profile: {ROOT / 'content_profile.md'}")
        print("\nSkipping questions. To override brand voice/themes, run:")
        print("  python3 scripts/onboard.py --force")
        return 0

    if args.force:
        print("\nRe-running onboarding with --force. Existing local config/profile may be overwritten.")

    print("\nContent Engine onboarding")
    print("Answer these once. The files created are local and ignored by git.\n")

    publication_name = ask("Publication / brand name")
    site_url = ask("Publication URL", "https://example.com", required=False)
    one_liner = ask("One-sentence promise to readers")
    audience = ask("Primary audience")
    transformation = ask("What should readers be able to do after reading?")

    voice_adjectives = ask_list(
        "Voice adjectives (comma-separated)",
        ["direct", "practical", "specific", "opinionated"],
        min_items=3,
    )
    point_of_view = ask(
        "Preferred point of view",
        "first person when earned, otherwise direct second person",
    )
    forbidden_voice = ask_list(
        "Forbidden voice/tone patterns",
        ["corporate filler", "vague hype", "generic AI disclaimers", "empty motivation"],
        min_items=2,
    )
    example_authors = ask(
        "Reference writers/brands to borrow energy from (optional)",
        "",
        required=False,
    )

    core_topics = ask_list("Core topics/themes", min_items=3)
    forbidden_topics = ask_list(
        "Forbidden topics/themes",
        ["offensive security", "hardware hacking", "generic news commentary"],
        min_items=1,
    )
    unique_angles = ask_list(
        "Recurring angles/frameworks you want more of",
        ["concrete workflows", "buildable systems", "decision frameworks"],
        min_items=2,
    )
    evidence_standard = ask(
        "Evidence standard",
        "specific examples, working code, named trade-offs, clear caveats",
    )

    article_types = ask_list(
        "Preferred article types",
        ["tutorial", "opinionated deep-dive", "checklist", "case study"],
        min_items=1,
    )
    required_sections = ask_list(
        "Required article sections",
        ["Hook", "The Idea", "Why This Matters", "Walkthrough", "Caveats", "CTA"],
        min_items=3,
    )
    min_words = int(ask("Minimum word count", "800"))
    max_words = int(ask("Target max word count", "1800"))
    paywall_default = ask("Default paywall mode (free/paid)", "free").lower()
    cadence = ask("Publishing cadence", "2 articles/week")

    series_name = ask("Primary series name", f"{publication_name} Field Notes")
    series_code = ask("Series code/prefix", series_name[0].upper())[:3].upper()
    series_desc = ask("Primary series description", one_liner)
    must_have = ask_list(
        "Idea fit: must-have criteria",
        ["serves the primary audience", "delivers a concrete reader outcome"],
        min_items=2,
    )
    nice_to_have = ask_list(
        "Idea fit: nice-to-have criteria",
        ["includes a reusable template", "has a strong opinion"],
        min_items=1,
    )
    disqualify = ask_list(
        "Idea fit: disqualifying criteria",
        forbidden_topics,
        min_items=1,
    )

    articles_dir = ask("Local articles directory", str(ROOT / "articles"))
    hero_dir = ask("Local hero images directory", str(Path(articles_dir) / "hero-images"))

    enable_devto = yes_no("Enable Dev.to integration?", False)
    enable_x = yes_no("Enable X/Twitter integration?", False)
    enable_substack = yes_no("Use Substack publishing support?", False)

    config = {
        "publication": {
            "name": publication_name,
            "site_url": site_url,
            "promise": one_liner,
            "audience": audience,
            "reader_transformation": transformation,
        },
        "brand_voice": {
            "voice_adjectives": voice_adjectives,
            "point_of_view": point_of_view,
            "forbidden_voice": forbidden_voice,
            "reference_brands": example_authors,
            "evidence_standard": evidence_standard,
        },
        "content_strategy": {
            "core_topics": core_topics,
            "forbidden_topics": forbidden_topics,
            "preferred_article_types": article_types,
            "recurring_angles": unique_angles,
            "publishing_cadence": cadence,
        },
        "models": {
            "idea": os.environ.get("IDEA_MODEL", "deepseek/deepseek-chat-v3-0324"),
            "draft": os.environ.get("DRAFT_MODEL", "z-ai/glm-5.1"),
            "qa": os.environ.get("QA_MODEL", "z-ai/glm-5.1"),
            "distribution": os.environ.get("DISTRIBUTE_MODEL", "deepseek/deepseek-chat-v3-0324"),
            "publish": os.environ.get("PUBLISH_MODEL", "openai/gpt-4.1-nano"),
            "hero_image": "gpt-image-2",
        },
        "paths": {
            "articles": articles_dir,
            "hero_images": hero_dir,
            "ideas_queue": str(ROOT / "ideas_queue.json"),
            "idea_catalogue": str(ROOT / "idea_catalogue.json"),
            "pipeline_state": str(ROOT / "pipeline_state.json"),
            "qa_reports": str(ROOT / "qa_reports"),
            "metrics": str(ROOT / "metrics"),
            "social": str(ROOT / "social"),
            "images": str(ROOT / "images"),
        },
        "qa": {
            "min_word_count": min_words,
            "max_word_count": max_words,
            "required_sections": required_sections,
            "forbidden_voice": forbidden_voice,
            "forbidden_topics": forbidden_topics,
        },
        "publishing": {
            "paywall_default": paywall_default,
            "substack_enabled": enable_substack,
            "devto_enabled": enable_devto,
            "x_enabled": enable_x,
            "reddit_auto_post": False,
        },
    }

    series_slug = slugify(series_name)
    series_theme = {
        "series": series_slug,
        "series_code": series_code,
        "name": series_name,
        "description": series_desc,
        "status": "active",
        "directory": f"{series_slug}-series",
        "next_number": 1,
        "paywall_default": paywall_default,
        "audience": audience,
        "voice": {
            "adjectives": voice_adjectives,
            "point_of_view": point_of_view,
            "forbidden": forbidden_voice,
        },
        "core_themes": core_topics,
        "forbidden_themes": forbidden_topics,
        "article_structure": {
            "required_sections": required_sections,
            "word_range": [min_words, max_words],
        },
        "fit_criteria": {
            "must_have": must_have,
            "nice_to_have": nice_to_have,
            "disqualify": disqualify,
        },
    }

    profile_md = dedent(
        f"""
        # Content Engine Profile

        ## Brand
        - Name: {publication_name}
        - URL: {site_url}
        - Promise: {one_liner}
        - Audience: {audience}
        - Reader transformation: {transformation}

        ## Voice
        - Adjectives: {', '.join(voice_adjectives)}
        - Point of view: {point_of_view}
        - Forbidden: {', '.join(forbidden_voice)}
        - References: {example_authors or 'none'}
        - Evidence standard: {evidence_standard}

        ## Content Strategy
        - Core topics: {', '.join(core_topics)}
        - Forbidden topics: {', '.join(forbidden_topics)}
        - Preferred article types: {', '.join(article_types)}
        - Recurring angles: {', '.join(unique_angles)}
        - Cadence: {cadence}

        ## Primary Series
        - Name: {series_name}
        - Code: {series_code}
        - Description: {series_desc}
        - Must-have fit: {', '.join(must_have)}
        - Nice-to-have fit: {', '.join(nice_to_have)}
        - Disqualify: {', '.join(disqualify)}
        """
    ).strip() + "\n"

    (ROOT / "series_themes").mkdir(exist_ok=True)
    Path(articles_dir).mkdir(parents=True, exist_ok=True)
    Path(hero_dir).mkdir(parents=True, exist_ok=True)
    for d in ["qa_reports", "metrics", "social", "images", "growth_reports"]:
        (ROOT / d).mkdir(exist_ok=True)

    write_yaml(ROOT / "config.yaml", config)
    (ROOT / "series_themes" / f"{series_slug}.json").write_text(json.dumps(series_theme, indent=2) + "\n")
    (ROOT / "content_profile.md").write_text(profile_md)

    for filename, payload in {
        "ideas_queue.json": {"ideas": []},
        "idea_catalogue.json": {"ideas": []},
        "pipeline_state.json": {"articles": {}, "last_updated": None},
    }.items():
        path = ROOT / filename
        if not path.exists():
            path.write_text(json.dumps(payload, indent=2) + "\n")

    print("\nOnboarding complete.")
    print(f"  Wrote: {ROOT / 'config.yaml'}")
    print(f"  Wrote: {ROOT / 'content_profile.md'}")
    print(f"  Wrote: {ROOT / 'series_themes' / (series_slug + '.json')}")
    print("\nThese files are local/runtime config and are ignored by git.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
