#!/usr/bin/env python3
import os
"""Extract Shipyard CLI code from articles and create GitHub repos.

Usage:
  python3 create_shipyard_repos.py --dry-run    # Preview what would be created
  python3 create_shipyard_repos.py              # Create all repos
  python3 create_shipyard_repos.py --article S01  # Single article
"""

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path
from datetime import date

ARTICLES_DIR = Path(os.environ.get("CONTENT_ENGINE_SHIPYARD_DIR", "articles/shipyard-series"))
GITHUB_ORG = "MikeS071"  # GitHub username
ARCHONHQ_URL = "https://archonhq.ai"

# Repo metadata per article
REPO_CONFIG = {
    "S01": {
        "repo_name": "skillpm",
        "description": "AI Skill Package Manager — install, version, and isolate AI capabilities",
        "topics": ["ai", "cli", "python", "skill-manager", "package-manager"],
        "cli_file": "skillpm.py",
        "entry_point": "#!/usr/bin/env python3",
    },
    "S02": {
        "repo_name": "hook-factory",
        "description": "AI Hook Factory CLI — generate and manage AI agent lifecycle hooks",
        "topics": ["ai", "cli", "python", "hooks", "agent-lifecycle"],
        "cli_file": "hook_factory.py",
        "entry_point": "#!/usr/bin/env python3",
    },
    "S03": {
        "repo_name": "seo-article-engine",
        "description": "SEO Article Engine — generate SEO-optimized articles with AI",
        "topics": ["ai", "cli", "bash", "seo", "content-generation"],
        "cli_file": "seo_engine.sh",
        "entry_point": "#!/usr/bin/env bash",
    },
    "S04": {
        "repo_name": "brand-kit-generator",
        "description": "Brand Kit Generator — create consistent brand assets with AI",
        "topics": ["ai", "cli", "bash", "branding", "design"],
        "cli_file": "brand_kit.sh",
        "entry_point": "#!/usr/bin/env bash",
    },
    "S05": {
        "repo_name": "multi-agent-pipeline",
        "description": "Multi-Agent Coding Pipeline — orchestrate AI agents for software development",
        "topics": ["ai", "cli", "bash", "multi-agent", "coding"],
        "cli_file": "pipeline.sh",
        "entry_point": "#!/usr/bin/env bash",
    },
    "S06": {
        "repo_name": "visual-dna-extractor",
        "description": "Visual DNA Extractor — extract design DNA from images using AI",
        "topics": ["ai", "cli", "bash", "computer-vision", "design"],
        "cli_file": "visual_dna.sh",
        "entry_point": "#!/usr/bin/env bash",
    },
}


def extract_code_blocks(content, language="python"):
    """Extract code blocks of a given language from markdown."""
    blocks = []
    in_block = False
    current = []
    current_lang = ""
    
    for line in content.split('\n'):
        stripped = line.strip()
        if stripped.startswith('```'):
            if not in_block:
                current_lang = stripped.replace('```', '').strip().lower()
                in_block = True
                current = []
            else:
                in_block = False
                if current_lang == language:
                    blocks.append('\n'.join(current))
                current = []
        elif in_block:
            current.append(line)
    
    return blocks


def extract_article_metadata(content):
    """Extract frontmatter metadata."""
    meta = {}
    for line in content.split('\n')[:20]:
        if ':' in line and not line.startswith(' '):
            key, val = line.split(':', 1)
            meta[key.strip()] = val.strip().strip('"').strip("'")
    return meta


def generate_readme(article_id, config, meta):
    """Generate a README.md for the repo."""
    title = meta.get("title", config["description"])
    series_num = article_id
    is_python = config["cli_file"].endswith(".py")
    
    install_cmd = f"python3 {config['cli_file']}" if is_python else f"bash {config['cli_file']}"
    
    readme = f"""# {config['repo_name']}

{config['description']}

Part of the [Shipyard Series]({ARCHONHQ_URL}) — Build-It-Yourself AI Tools.

## Quick Start

```bash
# Clone
git clone https://github.com/{GITHUB_ORG}/{config['repo_name']}.git
cd {config['repo_name']}

# Run
{install_cmd} --help
```

## What It Does

This is the companion CLI for **{title}** (Shipyard {series_num}).

The full build guide — step-by-step explanations, design decisions, and extensions — 
is available on [ArchonHQ]({ARCHONHQ_URL}).

## Requirements

- Python 3.10+ (for Python CLIs)
- An OpenRouter API key (or compatible OpenAI API endpoint)
- Set `OPENROUTER_API_KEY` environment variable

## Usage

```bash
# {config['description'].split('—')[0].strip()}
{install_cmd} <command> [options]
```

See the [full article]({ARCHONHQ_URL}) for detailed usage examples and walkthroughs.

## License

MIT

---

*Built with the [Shipyard methodology]({ARCHONHQ_URL}) — every article ships a working tool.*
"""
    return readme


def create_repo(article_id, dry_run=False):
    """Create a GitHub repo for a Shipyard article."""
    config = REPO_CONFIG.get(article_id)
    if not config:
        print(f"  No config for {article_id}, skipping")
        return False
    
    # Find the article file
    article_files = list(ARTICLES_DIR.glob(f"{article_id}-*.md"))
    if not article_files:
        print(f"  No article file for {article_id}")
        return False
    
    content = article_files[0].read_text()
    meta = extract_article_metadata(content)
    
    # Extract main code
    lang = "python" if config["cli_file"].endswith(".py") else "bash"
    blocks = extract_code_blocks(content, language=lang)
    
    if not blocks:
        print(f"  No {lang} code blocks found in {article_id}")
        return False
    
    # Take the largest block as the main CLI
    main_code = max(blocks, key=len)
    
    # Check if repo already exists
    result = subprocess.run(
        ["gh", "repo", "view", f"{GITHUB_ORG}/{config['repo_name']}", "--json", "name"],
        capture_output=True, text=True, timeout=15
    )
    if result.returncode == 0:
        print(f"  Repo {config['repo_name']} already exists, skipping")
        return False
    
    if dry_run:
        print(f"  Would create: {GITHUB_ORG}/{config['repo_name']}")
        print(f"  Description: {config['description']}")
        print(f"  CLI file: {config['cli_file']} ({len(main_code.splitlines())} lines)")
        print(f"  Topics: {', '.join(config['topics'])}")
        return True
    
    # Create the repo
    topics_str = ','.join(config['topics'])
    result = subprocess.run(
        ["gh", "repo", "create", config['repo_name'],
         "--public",
         "--description", config['description'],
         "--clone=false"],
        capture_output=True, text=True, timeout=30
    )
    
    if result.returncode != 0:
        print(f"  Failed to create repo: {result.stderr}")
        return False
    
    print(f"  Created repo: {GITHUB_ORG}/{config['repo_name']}")
    
    # Create a temp dir, write files, push
    import tempfile
    with tempfile.TemporaryDirectory() as tmpdir:
        repo_dir = Path(tmpdir) / config["repo_name"]
        repo_dir.mkdir()
        
        # Write CLI file
        cli_path = repo_dir / config["cli_file"]
        cli_path.write_text(main_code)
        
        # Write README
        readme_path = repo_dir / "README.md"
        readme_path.write_text(generate_readme(article_id, config, meta))
        
        # Write LICENSE
        license_path = repo_dir / "LICENSE"
        license_path.write_text("MIT License\n\nCopyright (c) 2026 ArchonHQ\n")
        
        # Init git, commit, push
        cmds = [
            ["git", "init"],
            ["git", "add", "."],
            ["git", "commit", "-m", f"Initial commit: {config['description']}"],
            ["git", "branch", "-M", "main"],
            ["git", "remote", "add", "origin", f"https://github.com/{GITHUB_ORG}/{config['repo_name']}.git"],
            ["git", "push", "-u", "origin", "main"],
        ]
        
        for cmd in cmds:
            result = subprocess.run(cmd, capture_output=True, text=True, 
                                    timeout=30, cwd=str(repo_dir))
            if result.returncode != 0 and "already exists" not in result.stderr:
                print(f"  Git warning: {result.stderr.strip()[:100]}")
        
        # Set topics
        subprocess.run(
            ["gh", "repo", "edit", f"{GITHUB_ORG}/{config['repo_name']}",
             "--add-topic", topics_str.replace(',', ',')],
            capture_output=True, text=True, timeout=15
        )
    
    print(f"  ✅ Pushed {config['cli_file']} ({len(main_code.splitlines())} lines) + README + LICENSE")
    return True


def main():
    parser = argparse.ArgumentParser(description="Create GitHub repos for Shipyard CLIs")
    parser.add_argument("--dry-run", action="store_true", help="Preview without creating")
    parser.add_argument("--article", help="Single article ID (e.g. S01)")
    args = parser.parse_args()
    
    if args.article:
        articles = [args.article.upper()]
    else:
        articles = sorted(REPO_CONFIG.keys())
    
    created = 0
    for aid in articles:
        print(f"\n📦 {aid}: {REPO_CONFIG[aid]['repo_name']}")
        if create_repo(aid, dry_run=args.dry_run):
            created += 1
    
    action = "Would create" if args.dry_run else "Created"
    print(f"\n{action} {created}/{len(articles)} repos")


if __name__ == "__main__":
    main()
