# ArchonHQ Content Engine

Automated content pipeline: **idea → draft → QA → publish → distribute**. Scans the internet for buildable AI skills and CLIs, generates opinionated long-form articles, enforces voice/style rules, publishes to Substack, and distributes across platforms — all on autopilot.

## Architecture

```
┌──────────────────────────────────────────────────────────────────────┐
│                     ArchonHQ Content Engine                         │
│                                                                      │
│  ┌─────────┐   ┌─────────┐   ┌──────┐   ┌──────────┐   ┌────────┐ │
│  │  IDEA   │──▶│  DRAFT  │──▶│  QA  │──▶│ PUBLISH  │──▶│DISTRIB │ │
│  │  GEN    │   │  GEN    │   │ENGINE│   │ (Substack)│   │ENGINE  │ │
│  └─────────┘   └─────────┘   └──────┘   └──────────┘   └────────┘ │
│  idea_          draft_        qa_        substack_       distribution│
│  generator.py   generator.py  engine.py  publisher.py    _engine.py │
│                                                                      │
│  Schedule:   Schedule:   Schedule:  Schedule:       Schedule:       │
│  6am daily   7am daily   8am daily  9am daily       10am daily      │
│                                                                      │
│  Model:       Model:      (local)    Model:          Model:          │
│  IDEA_MODEL   DRAFT_MODEL            PUBLISH_MODEL   DISTRIBUTE_MODEL│
│                                                                      │
│  ┌──────────────┐  ┌──────────────┐  ┌───────────────────────────┐  │
│  │ GROWTH ENGINE│  │  ANALYTICS   │  │  SUPPORTING SCRIPTS       │  │
│  │growth_engine │  │  TRACKER     │  │  gen_heroes.py            │  │
│  │    .py       │  │analytics_    │  │  llm_prose_fix.py         │  │
│  │              │  │  tracker.py  │  │  score_ideas.py           │  │
│  │ Weekly       │  │  Weekly      │  │  crosspost.py             │  │
│  │ strategic    │  │  metrics     │  │  create_shipyard_repos.py │  │
│  │ analysis     │  │  report      │  │  fix_toolkit.py           │  │
│  └──────────────┘  └──────────────┘  └───────────────────────────┘  │
└──────────────────────────────────────────────────────────────────────┘
```

## Prerequisites

- **Python 3.10+** (3.11+ recommended)
- **OpenRouter API key** — used by most scripts for LLM calls ([get one here](https://openrouter.ai/))
- **OpenAI API key** — for hero image generation (gpt-image-1)
- **Resend account** — for email-based Substack publishing ([resend.com](https://resend.com/))
- **Dev.to account** (optional) — for cross-posting articles
- **xurl CLI** (optional) — for X/Twitter integration ([xdevplatform/xurl](https://github.com/xdevplatform/xurl))
- **gh CLI** (optional) — for Shipyard repo creation
- **Pillow** (optional) — for hero image cropping; images save uncropped without it

## Quick Start

1. **Clone the repo**
   ```bash
   git clone <your-repo-url> ~/archonhq-content
   cd ~/archonhq-content
   ```

2. **Configure environment**
   ```bash
   cp .env.example .env
   # Edit .env with your API keys
   nano .env
   ```

3. **Run the installer**
   ```bash
   ./setup.sh
   ```

4. **Verify connectivity**
   ```bash
   python3 scripts/idea_generator.py --dry-run
   ```

5. **Go!** Generate your first idea:
   ```bash
   python3 scripts/idea_generator.py
   ```

## Pipeline Stages

### 1. Idea Generation (`idea_generator.py`)

Scans 7 sources (Hacker News, Reddit, GitHub Trending, Product Hunt, PyPI, dev.to, X/Twitter) for buildable AI skills and CLIs. Classifies findings into tiers, deduplicates against existing content, then uses an LLM to generate article angles with titles, theses, skill specs, and scores.

- **Schedule:** 6am daily (cron)
- **Model:** `IDEA_MODEL` (default: `deepseek/deepseek-chat-v3-0324`)
- **Output:** `ideas_queue.json`
- **Usage:** `python3 scripts/idea_generator.py [--dry-run] [--limit N]`

### 2. Draft Generation (`draft_generator.py`)

Takes approved ideas from the queue and generates full article drafts following the ArchonHQ voice rules (first-person, technical, opinionated, problem-first). Includes voice calibration from existing articles and enforces the canonical blog structure (Hook → Idea → Why → Walkthrough → Caveats → Philosophy → CTA).

- **Schedule:** 7am daily
- **Model:** `DRAFT_MODEL` (default: `z-ai/glm-5.1`)
- **Output:** Markdown files in articles directory with YAML frontmatter
- **Usage:** `python3 scripts/draft_generator.py [--idea ID] [--auto-approve-latest]`

### 3. QA Engine (`qa_engine.py`)

Runs an 8-check quality checklist on every draft:
1. Em-dash sweep (forbidden in prose)
2. En-dash sweep
3. Negation prose sweep (must use affirmative constructions)
4. Weasel word sweep
5. Hedging sweep
6. Blog pattern check (required sections present)
7. Duplication check (repeated stats/numbers)
8. Word count (minimum 800 words)
9. Paywall marker check (for paid articles)

Auto-fixes safe mechanical issues (em-dashes, en-dashes, missing Hook heading, paywall markers) and flags the rest for manual review.

- **Schedule:** 8am daily
- **Model:** Local (no LLM — regex-based checks)
- **Output:** QA reports in `qa_reports/`
- **Usage:** `python3 scripts/qa_engine.py [--article PATH] [--all] [--no-fix]`

### 4. Substack Publishing (`substack_publisher.py`)

Generates hero images using OpenAI's gpt-image-1 with curated color palettes that rotate per article. Composes a scene paragraph via LLM, generates the image, crops to 2:1 (1100×550), embeds it in the article, inserts paywall markers, and publishes as a Substack draft via email (Resend API → Substack post@ address).

- **Schedule:** 9am daily
- **Model:** `PUBLISH_MODEL` for scene generation (default: `openai/gpt-4.1-nano`), OpenAI gpt-image-1 for images
- **Output:** Hero images in `images/`, published Substack drafts
- **Usage:** `python3 scripts/substack_publisher.py [--article PATH] [--all-qa-passed] [--skip-image]`

### 5. Distribution Engine (`distribution_engine.py`)

Generates platform-specific social copy (HN, Reddit, LinkedIn, X/Twitter) via LLM, saves it to `social/`, and auto-cross-posts free articles to dev.to as drafts.

- **Schedule:** 10am daily
- **Model:** `DISTRIBUTE_MODEL` (default: `deepseek/deepseek-chat-v3-0324`)
- **Output:** Social copy in `social/`, dev.to drafts
- **Usage:** `python3 scripts/distribution_engine.py [--article PATH] [--all-published]`

### Supporting Scripts

- **`score_ideas.py`** — Scores ideas against series theme fit criteria and identity dimensions
- **`growth_engine.py`** — LLM-powered strategic growth recommendations (weekly)
- **`analytics_tracker.py`** — Weekly pipeline metrics report
- **`gen_heroes.py`** — Batch hero image generation for existing series articles
- **`llm_prose_fix.py`** — Context-aware LLM negation prose rewriter
- **`crosspost.py`** — Multi-platform cross-posting (Dev.to, X post, X thread)
- **`create_shipyard_repos.py`** — Extracts CLI code from Shipyard articles into GitHub repos
- **`fix_toolkit.py`** — Adds missing Prompt Toolkit sections to articles

## Series Configuration

The `series_themes/` directory contains JSON files that define each content series. These drive the `score_ideas.py` idea-scoring system and provide structure for article generation.

### Current Series

- **Caliber** (`caliber.json`) — Business growth for solo operators (M-prefix)
- **Shipyard** (`shipyard.json`) — Build-it-yourself AI tools (S-prefix)
- **Signal** (`signal.json`) — Opinionated engineering deep-dives (G-prefix)
- **Forge** (`forge.json`) — MCP server engineering (F-prefix)
- **Crucible** (`crucible.json`) — AI reliability engineering (C-prefix)
- **Bastion** (`bastion.json`) — Privacy-first AI (B-prefix)
- **Keystone** (`keystone.json`) — Enterprise architecture (K-prefix)
- **Atlas** (`atlas.json`) — Knowledge architecture (A-prefix)

### Defining Your Own Series

Create a new JSON file in `series_themes/` following this structure:

```json
{
  "series": "Your Series Name",
  "prefix": "X",
  "status": "active",
  "next_number": 1,
  "directory": "your-series",
  "paywall_default": "paid",
  "free_article_every": null,
  "description": "One-line description of the series focus and audience.",
  "audience": "Who this series is for",
  "tone": "Voice and style guidelines",
  "core_themes": ["theme 1", "theme 2", "theme 3"],
  "forbidden_themes": ["what NOT to cover"],
  "article_structure": {
    "required_sections": ["Hook", "The Idea (60 Seconds)", "Walkthrough", "Caveats", "Philosophy"],
    "word_range": [1200, 2000]
  },
  "fit_criteria": {
    "must_have": ["essential criterion"],
    "nice_to_have": ["bonus criterion"],
    "disqualify": ["deal-breaker criterion"]
  }
}
```

The `fit_criteria` drive a two-layer scoring system:
1. **Criteria layer:** 0–3 per criterion (must_have × 3, nice_to_have × 1, disqualify × −5)
2. **Identity layer:** Idea text scored against series description, DNA, tone, and core_themes (0–3 per dimension, weighted × 2)

## Customization

### Changing Models

Each pipeline stage uses a separate model, configurable via environment variables:

- `IDEA_MODEL` — Idea generation (default: `deepseek/deepseek-chat-v3-0324`)
- `DRAFT_MODEL` — Draft writing (default: `z-ai/glm-5.1`)
- `PUBLISH_MODEL` — Scene paragraph for hero images (default: `openai/gpt-4.1-nano`)
- `DISTRIBUTE_MODEL` — Social copy generation (default: `deepseek/deepseek-chat-v3-0324`)

Use any model available on [OpenRouter](https://openrouter.ai/models). Example:
```bash
export DRAFT_MODEL="anthropic/claude-sonnet-4"
```

### Adding Series

Add a new JSON file in `series_themes/` (see above). It will be automatically picked up by `score_ideas.py`.

### Modifying QA Rules

Edit `qa_engine.py` to change:
- `NEGATION_PATTERNS` — Regex patterns for negation prose
- `WEASEL_WORDS` — Words to flag
- `HEDGE_PHRASES` — Hedging phrases to flag
- `AFFIRMATIVE_SUBS` — Auto-fix substitution table
- `REQUIRED_SECTIONS` — Blog structure sections to check for
- `MIN_WORD_COUNT` — Minimum word count threshold

### Changing Voice Rules

Edit the `VOICE_RULES` string in `draft_generator.py`. This is the system prompt that shapes every generated article.

## Cost Expectations

Approximate costs at different publishing volumes (using default models on OpenRouter):

**Per article (full pipeline):**
- Idea generation: ~$0.01–0.03
- Draft generation: ~$0.05–0.15
- Scene paragraph (for hero image): ~$0.002
- Social copy: ~$0.01–0.02
- **Total LLM per article: ~$0.07–0.20**

**Hero images (OpenAI gpt-image-1):**
- ~$0.04–0.08 per image (1536×1024, high quality)

**Monthly estimates:**
- 1 article/day: ~$3–6 LLM + $1–2 images = **$4–8/month**
- 3 articles/day: ~$9–18 LLM + $3–6 images = **$12–24/month**
- 7 articles/week (intensive): ~$20–40/month total

Costs scale primarily with draft generation (longest outputs) and can be reduced by using cheaper models for the idea and distribution stages.

## Troubleshooting

### "No OPENROUTER_API_KEY found"
Make sure `.env` exists and contains your key. Scripts load from `~/.hermes/.env` by default — either place your `.env` there or set the environment variable directly.

### Hero image generation fails
- Verify `OPENAI_API_KEY` is set (different from OpenRouter key)
- Check you have billing enabled on your OpenAI account
- Install Pillow for automatic cropping: `pip install Pillow`

### Substack publishing fails
- Verify `RESEND_API_KEY`, `RESEND_FROM_EMAIL`, and `ARCHONHQ_SUBSTACK_EMAIL` are set
- Ensure the sender email is verified in Resend
- Check that your Substack publication's post-by-email address is correct

### Dev.to cross-posting fails
- Verify `ARCHONHQ_DEVTO_API_KEY` is set
- Get a key at https://dev.to/settings/extensions
- Paid articles are not cross-posted (only free articles or teasers)

### QA keeps failing on negation prose
This is by design — the ArchonHQ voice forbids negation constructions ("not", "don't", "can't", etc.) in favor of affirmative versions. Either:
- Fix the flagged lines manually, or
- Run `llm_prose_fix.py` for context-aware LLM rewrites, or
- Adjust `NEGATION_PATTERNS` in `qa_engine.py` to be less strict

### Ideas are low quality
- Try different source subreddits by editing `REDDIT_SUBS` in `idea_generator.py`
- Adjust tier classification keywords (`TIER1_KEYWORDS`, `TIER2_KEYWORDS`)
- Switch to a more capable model: `export IDEA_MODEL="anthropic/claude-sonnet-4"`

### xurl/Twitter integration not working
- Install xurl: `curl -fsSL https://raw.githubusercontent.com/xdevplatform/xurl/main/install.sh | bash`
- Authenticate: `xurl auth oauth2 --app <your-app-name>`
- Twitter integration is optional — the pipeline works without it

## License

MIT
