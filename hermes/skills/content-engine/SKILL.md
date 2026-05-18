---
name: content-engine
description: Configure and operate a reusable AI content engine for idea generation, drafting, QA, publishing support, and distribution.
version: 1.0.0
---

# Content Engine Skill

Use this skill when the user wants to install, configure, run, or adapt the Content Engine in a Hermes instance.

## Non-negotiable onboarding rule

Before generating ideas, drafts, publishing schedules, social posts, or articles for a new installation, establish the user's content profile.

If `content_profile.md` or `config.yaml` is missing in the repo root, ask the onboarding questions below or run:

```bash
python3 scripts/onboard.py
```

Onboarding is once per install. If both files exist, do not ask the questions again. To intentionally redo the profile, the user must ask to override/reconfigure, or run:

```bash
python3 scripts/onboard.py --force
```

Do not invent brand voice. Do not reuse ArchonHQ defaults unless the user explicitly asks for them.

## Qualifying questions

Ask these in batches, not as one giant wall. Capture answers in `config.yaml`, `content_profile.md`, and at least one `series_themes/*.json` file.

### 1. Brand and audience

- What is the publication / brand name?
- What is the one-sentence promise to readers?
- Who is the primary audience?
- What should readers be able to do, decide, or understand after reading?
- What makes this point of view different from adjacent publications?

### 2. Voice and style

- Pick 3-6 voice adjectives.
- Should articles use first person, second person, or neutral expert voice?
- What tone patterns are forbidden? Examples: corporate filler, hype, guru voice, academic stiffness, generic AI disclaimers.
- Which writers, brands, or publications should the voice borrow energy from?
- What should every article avoid sounding like?

### 3. Themes and boundaries

- What are the core topics/themes?
- What topics are explicitly out of scope?
- What recurring frameworks, metaphors, or angles should show up often?
- What evidence standard is required? Examples: working code, screenshots, citations, lived experience, benchmarks, case studies.
- What kinds of claims require proof?

### 4. Article format

- Preferred article types: tutorial, essay, teardown, checklist, case study, build log, opinionated deep-dive.
- Required sections.
- Target word range.
- Free vs paid strategy.
- CTA style.
- Should generated articles include code, templates, prompts, checklists, diagrams, or downloadable artefacts?

### 5. Series design

For each content series:

- Series name and prefix/code.
- Audience segment.
- Description.
- Core themes.
- Forbidden themes.
- Must-have criteria for an idea to fit.
- Nice-to-have criteria.
- Disqualifying criteria.
- Default paywall setting.
- Article structure.

### 6. Publishing and distribution

- Publishing platform: Substack, Ghost, static site, Markdown-only, other.
- Distribution platforms: X/Twitter, Dev.to, LinkedIn, Reddit research only, Hacker News, email.
- Which platforms may be automated?
- Which platforms require manual approval?
- Publishing cadence.
- Analytics that matter: subscribers, paid conversions, comments, backlinks, repo stars, replies, leads.

## Local files created by onboarding

Generated local files are intentionally ignored by git:

- `config.yaml`
- `content_profile.md`
- `series_themes/<user-series>.json`
- `ideas_queue.json`
- `idea_catalogue.json`
- `pipeline_state.json`
- generated article/image/report/social directories

Public repos should only contain sanitized examples and templates.

## Operating rules

- Never publish or cross-post paid/private content in full.
- Never auto-post to Reddit; produce opportunity reports only.
- Do not run the pipeline until onboarding exists.
- Prefer explicit local config over hardcoded paths.
- If the user wants public sharing, scan for articles, images, private paths, emails, API keys, metrics, and runtime state before committing.
