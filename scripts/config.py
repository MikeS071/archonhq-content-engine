#!/usr/bin/env python3
"""ArchonHQ Config Loader — single source of truth for all paths and settings.

Loads config.yaml and provides get_config() for all scripts to import.
Also provides update_pipeline_state() for tracking article pipeline status.
"""

import json
import os
import threading
from datetime import datetime, timezone
from pathlib import Path

# ── Config Loading ────────────────────────────────────────────────────────────

_CONFIG_PATH = Path(os.path.expanduser("~/archonhq-content/config.yaml"))
_config_cache = None
_config_lock = threading.Lock()


def _parse_yaml_simple(text):
    """Minimal YAML parser sufficient for our flat/nested config.
    Handles: strings, numbers, booleans, lists (with - items), nested dicts.
    Does NOT handle: anchors, aliases, multiline strings, complex types.
    """
    def parse_value(val):
        val = val.strip()
        if not val:
            return None
        # Boolean
        if val.lower() in ('true', 'yes', 'on'):
            return True
        if val.lower() in ('false', 'no', 'off'):
            return False
        # Number
        try:
            if '.' in val:
                return float(val)
            return int(val)
        except ValueError:
            pass
        # String — strip quotes
        if (val.startswith('"') and val.endswith('"')) or \
           (val.startswith("'") and val.endswith("'")):
            return val[1:-1]
        return val

    def parse_block(lines, start_indent=0):
        result = {}
        i = 0
        while i < len(lines):
            line = lines[i]
            stripped = line.lstrip()
            if not stripped or stripped.startswith('#'):
                i += 1
                continue

            indent = len(line) - len(stripped)
            if indent < start_indent:
                break

            # List item
            if stripped.startswith('- '):
                # Collect consecutive list items at this indent level
                items = []
                while i < len(lines):
                    s = lines[i].lstrip()
                    curr_indent = len(lines[i]) - len(s)
                    if curr_indent != indent or not s.startswith('- '):
                        break
                    item_val = s[2:].strip()
                    # Check if item has key: value
                    if ':' in item_val and not item_val.startswith("'") and not item_val.startswith('"'):
                        # Dict-style list item (like negation_patterns)
                        items.append(parse_value(item_val))
                    else:
                        items.append(parse_value(item_val))
                    i += 1
                return items

            # Key: value
            if ':' in stripped:
                colon_idx = stripped.index(':')
                key = stripped[:colon_idx].strip()
                val_str = stripped[colon_idx + 1:].strip()

                if val_str:
                    # Inline value
                    result[key] = parse_value(val_str)
                    i += 1
                else:
                    # Block value — peek ahead
                    if i + 1 < len(lines):
                        next_stripped = lines[i + 1].lstrip()
                        next_indent = len(lines[i + 1]) - len(next_stripped)
                        if next_indent > indent:
                            # Nested block
                            child_lines = []
                            i += 1
                            while i < len(lines):
                                s = lines[i].lstrip()
                                ci = len(lines[i]) - len(s)
                                if ci <= indent and lines[i].strip():
                                    break
                                child_lines.append(lines[i])
                                i += 1
                            child_result = parse_block(child_lines, next_indent)
                            result[key] = child_result
                        else:
                            result[key] = None
                            i += 1
                    else:
                        result[key] = None
                        i += 1
            else:
                i += 1

        return result

    lines = text.split('\n')
    return parse_block(lines, 0)


def load_config(config_path=None):
    """Load and parse config.yaml. Returns dict."""
    path = Path(config_path) if config_path else _CONFIG_PATH
    if not path.exists():
        raise FileNotFoundError(f"Config file not found: {path}")

    text = path.read_text()

    # Try PyYAML first if available
    try:
        import yaml
        return yaml.safe_load(text)
    except ImportError:
        pass

    # Fall back to our simple parser
    return _parse_yaml_simple(text)


def get_config(config_path=None):
    """Get cached config, loading on first call. Thread-safe."""
    global _config_cache
    if _config_cache is not None and config_path is None:
        return _config_cache

    with _config_lock:
        if _config_cache is not None and config_path is None:
            return _config_cache
        cfg = load_config(config_path)
        if config_path is None:
            _config_cache = cfg
        return cfg


def reload_config():
    """Force reload config from disk."""
    global _config_cache
    with _config_lock:
        _config_cache = None
    return get_config()


# ── Path Resolution Helpers ───────────────────────────────────────────────────

def _resolve_path(path_str):
    """Expand user home and return a Path object."""
    return Path(os.path.expanduser(path_str))


def get_path(key, config=None):
    """Get a resolved Path from config paths section."""
    cfg = config or get_config()
    path_str = cfg.get('paths', {}).get(key, '')
    if not path_str:
        raise KeyError(f"Path key '{key}' not found in config")
    return _resolve_path(path_str)


def get_model(key, config=None):
    """Get a model name from config, with env var override.

    Env var override pattern: uppercase key, e.g. DRAFT_MODEL, IDEA_MODEL.
    """
    cfg = config or get_config()
    env_key = key.upper()
    env_val = os.environ.get(env_key)
    if env_val:
        return env_val
    return cfg.get('models', {}).get(key, '')


def get_api(key, config=None):
    """Get an API endpoint from config."""
    cfg = config or get_config()
    return cfg.get('api', {}).get(key, '')


def get_word_count(key, config=None):
    """Get a word count limit from config."""
    cfg = config or get_config()
    return cfg.get('word_counts', {}).get(key, 800)


def get_qa_rules(config=None):
    """Get QA rules (required_sections, negation_patterns, weasel_words)."""
    cfg = config or get_config()
    return cfg.get('qa', {})


def get_publishing(key, config=None):
    """Get a publishing config value."""
    cfg = config or get_config()
    return cfg.get('publishing', {}).get(key, '')


def get_schedule(key, config=None):
    """Get a schedule cron expression from config."""
    cfg = config or get_config()
    return cfg.get('schedule', {}).get(key, '')


# ── Pipeline State Management ─────────────────────────────────────────────────

_PIPELINE_STATE_LOCK = threading.Lock()

# Valid article statuses in pipeline order
VALID_STATUSES = ['idea', 'draft', 'qa_passed', 'published_draft', 'published', 'cross_posted']


def load_pipeline_state(config=None):
    """Load pipeline_state.json from disk."""
    state_path = get_path('pipeline_state', config)
    if not state_path.exists():
        return {
            "version": 1,
            "last_updated": datetime.now(timezone.utc).isoformat(),
            "pipeline": {
                "idea_generation": {"last_run": None, "status": "pending", "ideas_generated": 0},
                "draft_generation": {"last_run": None, "status": "pending", "drafts_generated": 0},
                "qa_check": {"last_run": None, "status": "pending", "articles_passed": 0, "articles_failed": 0},
                "publishing": {"last_run": None, "status": "pending", "articles_published": 0},
                "cross_posting": {"last_run": None, "status": "pending", "platforms_posted": {}},
                "growth_report": {"last_run": None, "status": "pending"},
            },
            "articles": {},
        }
    return json.loads(state_path.read_text())


def save_pipeline_state(state, config=None):
    """Save pipeline_state.json to disk."""
    state_path = get_path('pipeline_state', config)
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state['last_updated'] = datetime.now(timezone.utc).isoformat()
    state_path.write_text(json.dumps(state, indent=2))


def update_pipeline_state(article_id, stage, details=None, config=None):
    """Update pipeline_state.json for an article stage transition.

    Args:
        article_id: Filename of the article (e.g. 'My-Article-Title.md')
        stage: Pipeline stage — one of 'idea', 'draft', 'qa_passed',
               'published_draft', 'published', 'cross_posted'
        details: Optional dict with extra info (qa_score, qa_issues,
                 series, title, cross_post platforms, etc.)
    """
    if stage not in VALID_STATUSES:
        raise ValueError(f"Invalid stage '{stage}'. Must be one of: {VALID_STATUSES}")

    details = details or {}

    with _PIPELINE_STATE_LOCK:
        state = load_pipeline_state(config)
        now = datetime.now(timezone.utc).isoformat()

        # Update article entry
        if article_id not in state['articles']:
            state['articles'][article_id] = {
                'series': '',
                'title': '',
                'status': 'idea',
                'timestamps': {},
                'qa_score': None,
                'qa_issues': [],
                'cross_post_platforms': [],
            }

        article = state['articles'][article_id]
        article['status'] = stage
        article['timestamps'][stage] = now

        # Merge details
        if 'series' in details:
            article['series'] = details['series']
        if 'title' in details:
            article['title'] = details['title']
        if 'qa_score' in details:
            article['qa_score'] = details['qa_score']
        if 'qa_issues' in details:
            article['qa_issues'] = details['qa_issues']
        if 'cross_post_platforms' in details:
            article['cross_post_platforms'] = details['cross_post_platforms']

        # Update pipeline stage summary
        stage_map = {
            'idea': 'idea_generation',
            'draft': 'draft_generation',
            'qa_passed': 'qa_check',
            'published_draft': 'publishing',
            'published': 'publishing',
            'cross_posted': 'cross_posting',
        }

        pipeline_key = stage_map.get(stage)
        if pipeline_key and pipeline_key in state['pipeline']:
            pipe = state['pipeline'][pipeline_key]
            pipe['last_run'] = now
            pipe['status'] = 'completed'

            # Increment counters
            if stage == 'idea':
                pipe['ideas_generated'] = pipe.get('ideas_generated', 0) + 1
            elif stage == 'draft':
                pipe['drafts_generated'] = pipe.get('drafts_generated', 0) + 1
            elif stage == 'qa_passed':
                pipe['articles_passed'] = pipe.get('articles_passed', 0) + 1
            elif stage in ('published_draft', 'published'):
                pipe['articles_published'] = pipe.get('articles_published', 0) + 1
            elif stage == 'cross_posted':
                platforms = details.get('platforms', [])
                posted = pipe.get('platforms_posted', {})
                for p in platforms:
                    posted[p] = posted.get(p, 0) + 1
                pipe['platforms_posted'] = posted

        save_pipeline_state(state, config)
        return state


# ── Environment Helpers ───────────────────────────────────────────────────────

def load_env(config=None):
    """Load .env file as dict, using config path."""
    env_path = get_path('env_file', config)
    env = {}
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith('#') and '=' in line:
                k, v = line.split('=', 1)
                env[k.strip()] = v.strip()
    return env
