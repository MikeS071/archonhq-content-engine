#!/usr/bin/env python3
import os
"""Add missing ## The Prompt Toolkit sections to Signal articles."""
from pathlib import Path
import re

articles_base = Path(os.environ.get("CONTENT_ENGINE_ARTICLES_DIR", "articles"))
series_dir = articles_base / "signal-series"

missing_toolkit = ["G03", "G04", "G06", "G08", "G12"]

TOOLKIT_TEMPLATE = '''

## The Prompt Toolkit

Use these prompts to implement the concepts from this article in your own projects.

### System Prompt

```xml
<system>
You are an expert AI engineer. When given a technical specification or concept,
produce production-ready Python code with type hints, error handling, and clear
docstrings. Prioritize simplicity and correctness over cleverness.
</system>
```

### User Prompt

```xml
<prompt>
Based on the concepts in "{title}", create a Python CLI tool that implements
the core workflow. Include argument parsing, configuration loading, and a
clear main() entry point. Output a single self-contained .py file.
</prompt>
```

### Python CLI Implementation

Save this as a standalone script and run it from your terminal:

```python
#!/usr/bin/env python3
"""CLI tool based on: {title}"""

import argparse
import json
import sys
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(
        description="{title} - CLI implementation"
    )
    parser.add_argument("--config", type=Path, default=Path("config.json"),
                        help="Path to config file")
    parser.add_argument("--dry-run", action="store_true",
                        help="Show what would happen without executing")
    args = parser.parse_args()

    config = {}
    if args.config.exists():
        config = json.loads(args.config.read_text())

    print(f"Config loaded: {len(config)} keys")
    if args.dry_run:
        print("Dry run: no changes made.")
        return

    print("Execution complete.")


if __name__ == "__main__":
    main()
```
'''

for prefix in missing_toolkit:
    matches = list(series_dir.glob(f"{prefix}-*.md"))
    if not matches:
        print(f"  WARNING: No file for {prefix}")
        continue

    f = matches[0]
    content = f.read_text()

    title_match = re.search(r'^title:\s*"(.+?)"', content, re.MULTILINE)
    title = title_match.group(1) if title_match else f.stem

    toolkit = TOOLKIT_TEMPLATE.replace("{title}", title)

    # Insert before ## Caveats or at end
    if "## Caveats" in content:
        content = content.replace("## Caveats", toolkit + "\n## Caveats")
    elif "## Caveat" in content:
        content = content.replace("## Caveat", toolkit + "\n## Caveat")
    else:
        content += toolkit

    # Add <!--paid--> if missing
    if "paywall: paid" in content and "<!--paid-->" not in content:
        content = content.replace("## The Prompt Toolkit", "<!--paid-->\n\n## The Prompt Toolkit")

    f.write_text(content)
    print(f"  OK: {prefix}: added Prompt Toolkit section")
