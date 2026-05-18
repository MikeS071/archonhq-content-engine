#!/usr/bin/env python3
"""ArchonHQ Substack Publisher — generates hero images, publishes drafts to Substack.

Usage:
    python3 substack_publisher.py [--article PATH] [--all-qa-passed] [--skip-image]

Hero images use OpenAI gpt-image-1 at 1100x550.
Prompt structure: <scene> (per-article) + <settings> (from prompts/New Image Generation.md).
"""

import argparse
import base64
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

import requests

# ── Config ──────────────────────────────────────────────────────────────────

ROOT = Path(os.path.expanduser("~/archonhq-content"))
ARTICLES_DIR = Path(os.environ.get("CONTENT_ENGINE_ARTICLES_DIR", "articles"))
IMAGE_PROMPT_FILE = Path(os.path.expanduser(
    os.environ.get("CONTENT_ENGINE_IMAGE_PROMPT_FILE", "prompts/image-generation.md")
))
IMAGES_DIR = ROOT / "images"
ENV_FILE = Path(os.path.expanduser("~/.hermes/.env"))

OPENAI_IMAGE_MODEL = "gpt-image-1"
OPENAI_IMAGE_URL = "https://api.openai.com/v1/images/generations"
IMAGE_GEN_SIZE = "1536x1024"   # gpt-image-1 only supports 1024x1024, 1536x1024, 1024x1792
IMAGE_FINAL_W = 1100
IMAGE_FINAL_H = 550
IMAGE_QUALITY = "high"

# Curated ArchonHQ color palettes — same DNA, different hue.
# Each keeps the dark-base + emissive-accent structure but shifts the dominant color.
COLOR_PALETTES = [
    {
        "name": "verdant",  # original green-gold
        "dominant_colors": ["#061104", "#111d0a", "#1e2d0f", "#293811", "#3c571f", "#6e903e"],
        "accent_colors": ["#96a03f", "#b2c565", "#e3ed9f", "#e6c96a", "#f3ffcf"],
        "color_harmony_type": "Analogous yellow-green harmony with monochromatic dark green support; the palette stays tightly clustered around green, chartreuse, lime, and warm yellow.",
        "color_grading_style": "Cinematic sci-fi grading with aggressive green-gold bias, lifted luminous mids in the emissive regions, and protected dark shadows to maintain density and drama.",
        "shadow_color_cast": "Deep green-black with olive undertones.",
        "highlight_color_cast": "Chartreuse to pale yellow-white, with some near-white greenish bloom at the brightest points.",
        "saturation_note": "High saturation overall, especially in the yellow-green emission zones; blacks remain deep, which preserves intensity without washing out the palette.",
    },
    {
        "name": "electric_cyan",  # teal-cyan
        "dominant_colors": ["#040d11", "#0a1820", "#0f2630", "#113540", "#1f5060", "#3e8090"],
        "accent_colors": ["#3fa0b0", "#65c5d5", "#9fedf5", "#6ab0e6", "#cff5ff"],
        "color_harmony_type": "Analogous teal-cyan harmony with monochromatic dark blue-green support; the palette clusters around cyan, aquamarine, and cool white.",
        "color_grading_style": "Cinematic sci-fi grading with aggressive teal-cyan bias, lifted luminous mids in the emissive regions, and protected dark shadows to maintain density and drama.",
        "shadow_color_cast": "Deep blue-black with teal undertones.",
        "highlight_color_cast": "Bright cyan to pale cool-white, with some near-white bluish bloom at the brightest points.",
        "saturation_note": "High saturation overall, especially in the cyan emission zones; blacks remain deep, which preserves intensity without washing out the palette.",
    },
    {
        "name": "molten_amber",  # warm amber-orange
        "dominant_colors": ["#110804", "#1d0f0a", "#2d1610", "#381e11", "#57321f", "#905a3e"],
        "accent_colors": ["#a0783f", "#c59565", "#edb09f", "#e6a06a", "#fff0cf"],
        "color_harmony_type": "Analogous amber-orange harmony with monochromatic dark brown support; the palette stays tightly clustered around amber, copper, and warm gold.",
        "color_grading_style": "Cinematic sci-fi grading with aggressive amber-copper bias, lifted luminous mids in the emissive regions, and protected dark shadows to maintain density and drama.",
        "shadow_color_cast": "Deep brown-black with sienna undertones.",
        "highlight_color_cast": "Bright amber to pale warm-white, with some near-white golden bloom at the brightest points.",
        "saturation_note": "High saturation overall, especially in the amber emission zones; blacks remain deep, which preserves intensity without washing out the palette.",
    },
    {
        "name": "plasma_violet",  # purple-magenta
        "dominant_colors": ["#0d0411", "#180a1d", "#260f2d", "#321138", "#4a1f57", "#7a3e90"],
        "accent_colors": ["#9040a0", "#b565c5", "#ed9ff5", "#c96ae6", "#f5cfff"],
        "color_harmony_type": "Analogous violet-magenta harmony with monochromatic dark purple support; the palette stays tightly clustered around violet, magenta, and cool pink.",
        "color_grading_style": "Cinematic sci-fi grading with aggressive violet-magenta bias, lifted luminous mids in the emissive regions, and protected dark shadows to maintain density and drama.",
        "shadow_color_cast": "Deep purple-black with plum undertones.",
        "highlight_color_cast": "Bright magenta to pale cool-white, with some near-white pinkish bloom at the brightest points.",
        "saturation_note": "High saturation overall, especially in the violet emission zones; blacks remain deep, which preserves intensity without washing out the palette.",
    },
    {
        "name": "arctic_blue",  # cool blue-silver
        "dominant_colors": ["#040811", "#0a121d", "#0f1e2d", "#112838", "#1f3c57", "#3e6490"],
        "accent_colors": ["#3f7ca0", "#6595c5", "#9fc0ed", "#6aa8e6", "#cfe8ff"],
        "color_harmony_type": "Analogous blue-silver harmony with monochromatic dark blue support; the palette stays tightly clustered around cerulean, steel blue, and cool white.",
        "color_grading_style": "Cinematic sci-fi grading with aggressive steel-blue bias, lifted luminous mids in the emissive regions, and protected dark shadows to maintain density and drama.",
        "shadow_color_cast": "Deep navy-black with indigo undertones.",
        "highlight_color_cast": "Bright steel-blue to pale cool-white, with some near-white icy bloom at the brightest points.",
        "saturation_note": "High saturation overall, especially in the blue emission zones; blacks remain deep, which preserves intensity without washing out the palette.",
    },
]

# Track which palette was used last so we rotate evenly
_PALETTE_STATE_FILE = ROOT / ".palette_state"


def pick_palette():
    """Rotate through palettes, picking the next one each run."""
    if _PALETTE_STATE_FILE.exists():
        last = _PALETTE_STATE_FILE.read_text().strip()
    else:
        last = ""
    names = [p["name"] for p in COLOR_PALETTES]
    idx = 0
    if last in names:
        idx = (names.index(last) + 1) % len(names)
    chosen = COLOR_PALETTES[idx]
    _PALETTE_STATE_FILE.write_text(chosen["name"])
    log(f"  Palette: {chosen['name']}")
    return chosen


def inject_palette(settings_block, palette):
    """Replace the color_profile section in settings with the chosen palette."""
    color_profile = {
        "dominant_colors": palette["dominant_colors"],
        "accent_colors": palette["accent_colors"],
        "color_temperature_kelvin_estimate": 5200,
        "saturation_level": palette["saturation_note"],
        "contrast_ratio": "High contrast, with near-black structural shadows against intense luminous cores and glowing filament highlights.",
        "color_harmony_type": palette["color_harmony_type"],
        "color_grading_style": palette["color_grading_style"],
        "shadow_color_cast": palette["shadow_color_cast"],
        "highlight_color_cast": palette["highlight_color_cast"],
    }
    color_json = json.dumps(color_profile, indent=2)

    # The settings block may contain newlines in strings (invalid strict JSON)
    # Try lenient parse first, fall back to regex
    try:
        settings = json.loads(settings_block, strict=False)
        settings["color_profile"] = json.loads(color_json, strict=False)
        return json.dumps(settings, indent=2, ensure_ascii=False)
    except json.JSONDecodeError:
        log("  ⚠ Settings not valid JSON, attempting regex color swap")
        # Match "color_profile": { ... } including nested braces
        pattern = r'"color_profile"\s*:\s*\{(?:[^{}]|(?:\{(?:[^{}]|(?:\{[^{}]*\})*)*\}))*\}'
        replacement = f'"color_profile": {color_json}'
        result = re.sub(pattern, replacement, settings_block, flags=re.DOTALL)
        if result == settings_block:
            log("  ⚠ Could not find color_profile to replace — using original settings")
        return result

SUBSTACK_EMAIL_ENV = "ARCHONHQ_SUBSTACK_EMAIL"
RESEND_FROM_ENV = "RESEND_FROM_EMAIL"
RESEND_API_KEY_ENV = "RESEND_API_KEY"

# Substack paywall marker — inserted before Walkthrough section for paid articles
SUBSTACK_PAYWALL = "\n---\n*This section is for paid members. [Upgrade to read the full walkthrough →](/subscribe)*\n---\n"

LLM_MODEL = os.environ.get("PUBLISH_MODEL", "openai/gpt-4.1-nano")
LLM_URL = "https://openrouter.ai/api/v1/chat/completions"


def load_env():
    env = {}
    if ENV_FILE.exists():
        for line in ENV_FILE.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip()
    return env


def log(msg):
    print(f"[publisher] {msg}", file=sys.stderr)


# ── Hero Image Generation ──────────────────────────────────────────────────

def load_image_settings():
    """Load the <settings> block from the prompt file."""
    if not IMAGE_PROMPT_FILE.exists():
        log(f"  ✗ Image prompt file not found: {IMAGE_PROMPT_FILE}")
        return None
    text = IMAGE_PROMPT_FILE.read_text()
    # Extract everything between <settings> and </settings>
    m = re.search(r'<settings>(.*?)</settings>', text, re.DOTALL)
    if m:
        return m.group(1).strip()
    log("  ✗ No <settings> block found in prompt file")
    return None


def generate_scene_paragraph(env, idea_title, thesis, skill_spec):
    """Use LLM to generate a <scene> paragraph tailored to the article."""
    api_key = env.get("OPENROUTER_API_KEY")
    if not api_key:
        log("  ✗ No OPENROUTER_API_KEY for scene generation")
        return None

    prompt = f"""Generate a single paragraph (2-3 sentences) for an AI-generated hero image prompt.
The paragraph should vividly describe the concept of this article as a visual scene:

Title: {idea_title}
Thesis: {thesis}
Skill Spec: {skill_spec}

Rules:
- Describe the scene, not the article
- Focus on the visual metaphor of the capability (e.g., for an email→task CLI, describe streams of light flowing from an inbox into organized grid nodes)
- No humans, no text, no signage
- Make it vivid and specific enough for an image model
- Output ONLY the paragraph, no other text"""

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://archonhq.ai",
    }
    payload = {
        "model": LLM_MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.8,
        "max_tokens": 300,
    }

    try:
        r = requests.post(LLM_URL, headers=headers, json=payload, timeout=60)
        r.raise_for_status()
        return r.json()["choices"][0]["message"]["content"].strip()
    except Exception as e:
        log(f"  ✗ Scene generation failed: {e}")
        return None


def build_image_prompt(scene_paragraph, settings_block, palette=None):
    """Compose full image prompt: <scene> + <settings> with optional palette rotation."""
    if palette:
        settings_block = inject_palette(settings_block, palette)
    return f"Generate a new image for the following scene using the settings below:\n<scene>\n{scene_paragraph}\n</scene>\n\n<settings>\n{settings_block}\n</settings>"


def generate_hero_image(env, full_prompt, output_path):
    """Call OpenAI gpt-image-1 to generate the hero image at 1536x1024."""
    openai_key = env.get("OPENAI_API_KEY")
    if not openai_key:
        log("  ✗ No OPENAI_API_KEY found (needed for OpenAI image API)")
        return False

    headers = {
        "Authorization": f"Bearer {openai_key}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": OPENAI_IMAGE_MODEL,
        "prompt": full_prompt[:4000],  # OpenAI prompt limit
        "n": 1,
        "size": IMAGE_GEN_SIZE,
        "quality": IMAGE_QUALITY,
    }

    log(f"  Calling {OPENAI_IMAGE_MODEL} ({IMAGE_GEN_SIZE})...")
    try:
        r = requests.post(OPENAI_IMAGE_URL, headers=headers, json=payload, timeout=120)
        r.raise_for_status()
        data = r.json()

        if "data" in data and len(data["data"]) > 0:
            img_data = data["data"][0]
            raw_bytes = None
            if "b64_json" in img_data:
                raw_bytes = base64.b64decode(img_data["b64_json"])
            elif "url" in img_data:
                img_resp = requests.get(img_data["url"], timeout=60)
                img_resp.raise_for_status()
                raw_bytes = img_resp.content

            if raw_bytes:
                # Center-crop to 2:1 aspect ratio, then resize to final dimensions
                try:
                    from PIL import Image
                    import io
                    img = Image.open(io.BytesIO(raw_bytes))
                    w, h = img.size
                    # Target ratio = 2:1 (1100:550)
                    target_ratio = IMAGE_FINAL_W / IMAGE_FINAL_H
                    current_ratio = w / h
                    if current_ratio > target_ratio:
                        # Too wide — crop sides
                        new_w = int(h * target_ratio)
                        left = (w - new_w) // 2
                        img = img.crop((left, 0, left + new_w, h))
                    elif current_ratio < target_ratio:
                        # Too tall — crop top/bottom
                        new_h = int(w / target_ratio)
                        top = (h - new_h) // 2
                        img = img.crop((0, top, w, top + new_h))
                    img = img.resize((IMAGE_FINAL_W, IMAGE_FINAL_H), Image.LANCZOS)
                    img.save(output_path, "PNG")
                    log(f"  ✓ Hero image saved: {output_path} (cropped {w}x{h} → {IMAGE_FINAL_W}x{IMAGE_FINAL_H})")
                    return True
                except ImportError:
                    # No PIL — save raw, skip crop
                    output_path.write_bytes(raw_bytes)
                    log(f"  ✓ Hero image saved (uncropped, no PIL): {output_path}")
                    return True

        log(f"  ✗ Unexpected image response format: {list(data.keys())}")
        return False
    except Exception as e:
        log(f"  ✗ Image generation failed: {e}")
        return False


def process_hero_image(env, article_path, frontmatter_text, body_text):
    """Full pipeline: generate scene → compose prompt → generate image → save."""
    # Extract article metadata for scene generation
    title_m = re.search(r'title:\s*"(.+?)"', frontmatter_text)
    idea_id_m = re.search(r'idea_id:\s*(\S+)', frontmatter_text)

    title = title_m.group(1) if title_m else article_path.stem
    idea_id = idea_id_m.group(1) if idea_id_m else f"img_{datetime.now().strftime('%Y%m%d%H%M')}"

    # Try to get thesis/skill_spec from ideas queue
    thesis = ""
    skill_spec = ""
    ideas_queue = ROOT / "ideas_queue.json"
    if ideas_queue.exists():
        ideas = json.loads(ideas_queue.read_text()).get("ideas", [])
        for idea in ideas:
            if idea.get("id") == idea_id or idea.get("title") == title:
                thesis = idea.get("thesis", "")
                skill_spec = idea.get("skill_spec", "")
                break

    # Generate scene paragraph
    scene = generate_scene_paragraph(env, title, thesis, skill_spec)
    if not scene:
        log("  ✗ Failed to generate scene paragraph, skipping image")
        return None

    # Load settings block
    settings = load_image_settings()
    if not settings:
        log("  ✗ Failed to load image settings, skipping image")
        return None

    # Compose full prompt with palette rotation
    palette = pick_palette()
    full_prompt = build_image_prompt(scene, settings, palette=palette)

    # Generate image
    IMAGES_DIR.mkdir(parents=True, exist_ok=True)
    output_path = IMAGES_DIR / f"{idea_id}_hero.png"
    if generate_hero_image(env, full_prompt, output_path):
        return output_path
    return None


# ── Substack Publishing ─────────────────────────────────────────────────────

def publish_to_substack(env, article_path, hero_image_path=None):
    """Publish article as draft to Substack via email-to-post.
    
    Substack supports creating drafts by sending email to post@substack.com
    with the article body. This is the simplest integration path.
    """
    resend_key = env.get(RESEND_API_KEY_ENV)
    from_email = env.get(RESEND_FROM_ENV, "navi@archonhq.ai")
    substack_email = env.get(SUBSTACK_EMAIL_ENV, "post@substack.com")

    if not resend_key:
        log("  ✗ No RESEND_API_KEY found. Cannot publish to Substack.")
        log("  Set RESEND_API_KEY in ~/.hermes/.env to enable email-based publishing.")
        return False

    # Read article
    text = article_path.read_text()
    
    # Remove duplicate YAML code blocks (draft generator artifact)
    text = re.sub(r'\n*```yaml\n---\ntitle:.*?---\n```\n*', '\n', text, flags=re.DOTALL)
    
    # Remove Obsidian hero image tags (not valid for Substack)
    text = re.sub(r'!\[hero\]\(hero-images/[^\)]+\)\n*', '', text)
    
    # Remove frontmatter for the email body
    if text.startswith("---"):
        parts = text.split("---", 2)
        if len(parts) >= 3:
            frontmatter = parts[1]
            body = parts[2].strip()
            title_m = re.search(r'title:\s*"(.+?)"', frontmatter)
            title = title_m.group(1) if title_m else article_path.stem
        else:
            body = text
            title = article_path.stem
    else:
        body = text
        title = article_path.stem
    
    # Replace custom paywall marker with Substack <!--paid--> syntax
    paywall_pattern = r'\n*---\n\*This section is for paid members\..*?\n---\n*'
    body = re.sub(paywall_pattern, '\n<!--paid-->\n\n', body, flags=re.DOTALL)

    # Send via Resend
    log(f"  Sending draft to Substack: {title}")
    headers = {
        "Authorization": f"Bearer {resend_key}",
        "Content-Type": "application/json",
    }
    payload = {
        "from": f"ArchonHQ <{from_email}>",
        "to": [substack_email],
        "subject": title,
        "text": body,
    }
    
    # Attach hero image if available
    if hero_image_path and hero_image_path.exists():
        import base64
        img_b64 = base64.b64encode(hero_image_path.read_bytes()).decode()
        payload["attachments"] = [{
            "filename": hero_image_path.name,
            "content": img_b64,
            "content_type": "image/png",
        }]
        log(f"  Hero image attached: {hero_image_path.name}")

    try:
        r = requests.post("https://api.resend.com/emails", headers=headers, json=payload, timeout=30)
        r.raise_for_status()
        result = r.json()
        log(f"  ✓ Draft sent to Substack via email (ID: {result.get('id', '?')})")
        return True
    except Exception as e:
        log(f"  ✗ Failed to send to Substack: {e}")
        return False


def embed_hero_in_article(article_path, hero_image_path):
    """Insert hero image reference at the top of the article body (above title).
    
    Copies image into the articles/hero-images/ directory (inside Obsidian vault)
    and references it with a vault-relative path so Obsidian renders it.
    """
    # Copy image into vault-local directory for Obsidian
    hero_dir = ARTICLES_DIR / "hero-images"
    hero_dir.mkdir(parents=True, exist_ok=True)
    dest_name = article_path.stem.replace(" ", "-").lower() + "_hero.png"
    dest_path = hero_dir / dest_name
    import shutil
    shutil.copy2(hero_image_path, dest_path)
    
    # Vault-relative path — Obsidian resolves this relative to the vault root
    image_tag = f"![hero](hero-images/{dest_name})"
    
    text = article_path.read_text()
    
    if text.startswith("---"):
        parts = text.split("---", 2)
        if len(parts) >= 3:
            frontmatter = parts[1]
            body = parts[2]
            # Remove any existing hero image tag
            body = re.sub(r'^\s*!\[hero\]\(.*?\)\s*\n?', '', body)
            # Insert hero image at top of body
            body = f"\n{image_tag}\n\n{body}"
            article_path.write_text(f"---{frontmatter}---{body}")
            log(f"  ✓ Hero image embedded in article: hero-images/{dest_name}")
            return
    else:
        # No frontmatter — just prepend
        text = re.sub(r'^\s*!\[hero\]\(.*?\)\s*\n?', '', text)
        article_path.write_text(f"{image_tag}\n\n{text}")
        log(f"  ✓ Hero image embedded in article: hero-images/{dest_name}")
        return


def insert_paywall_marker(article_path):
    """Insert Substack paywall marker before the Walkthrough section for paid articles."""
    text = article_path.read_text()
    
    # Check if article is paid
    paywall_m = re.search(r'^paywall:\s*paid\s*$', text, re.MULTILINE)
    if not paywall_m:
        log(f"  ⊘ Free article — no paywall marker needed")
        return False
    
    # Already has a paywall marker?
    if "Upgrade to read the full walkthrough" in text:
        log(f"  ⊘ Paywall marker already present")
        return False
    
    # Find the Walkthrough section (or Solution/Implementation)
    walkthrough_m = re.search(r'\n(## (?:Walkthrough|Solution|Implementation|Step-by-Step))\n', text)
    if not walkthrough_m:
        log(f"  ⚠ No Walkthrough/Solution heading found — cannot place paywall marker")
        return False
    
    # Insert paywall marker just before the heading
    insert_pos = walkthrough_m.start()
    text = text[:insert_pos] + SUBSTACK_PAYWALL + text[insert_pos:]
    article_path.write_text(text)
    log(f"  ✓ Paywall marker inserted before '{walkthrough_m.group(1).strip()}'")
    return True


def update_article_status(article_path, status, hero_path=None):
    """Update frontmatter status and add hero image path."""
    text = article_path.read_text()
    text = re.sub(r'status:\s*\w+', f'status: {status}', text)

    if hero_path and 'hero_image:' not in text:
        # Add hero_image to frontmatter
        text = text.replace(
            f"status: {status}",
            f"status: {status}\nhero_image: {hero_path}"
        )

    article_path.write_text(text)


# ── Main ────────────────────────────────────────────────────────────────────

def find_qa_passed():
    """Find articles with status 'qa_passed' in frontmatter."""
    if not ARTICLES_DIR.exists():
        return []
    results = []
    for f in ARTICLES_DIR.glob("*.md"):
        text = f.read_text()
        if re.search(r'^status:\s*qa_passed\s*$', text, re.MULTILINE):
            results.append(f)
    return results


def find_qa_fixed():
    """Find articles with status 'qa_fixed' (need human approval)."""
    if not ARTICLES_DIR.exists():
        return []
    results = []
    for f in ARTICLES_DIR.glob("*.md"):
        text = f.read_text()
        if re.search(r'^status:\s*qa_fixed\s*$', text, re.MULTILINE):
            results.append(f)
    return results


def run(article_path=None, all_qa_passed=False, skip_image=False):
    env = load_env()

    if article_path:
        paths = [Path(article_path)]
    elif all_qa_passed:
        paths = find_qa_passed()
        log(f"Found {len(paths)} QA-passed articles")
    else:
        paths = find_qa_passed()
        if not paths:
            # Also check qa_fixed for manual approval
            fixed = find_qa_fixed()
            if fixed:
                log(f"No qa_passed articles. {len(fixed)} qa_fixed articles awaiting manual approval.")
            else:
                log("No articles ready for publishing.")
            return

    if not paths:
        log("No articles to publish.")
        return

    for path in paths:
        log(f"Processing: {path.name}")
        frontmatter, body = read_article(path)

        # Generate hero image
        hero_path = None
        if not skip_image:
            hero_path = process_hero_image(env, path, frontmatter, body)
            if hero_path:
                log(f"  ✓ Hero image: {hero_path}")
                embed_hero_in_article(path, hero_path)
            else:
                log(f"  ⚠ Hero image generation failed, continuing without image")

        # Insert paywall marker for paid articles
        insert_paywall_marker(path)

        # Publish to Substack
        if publish_to_substack(env, path, hero_path):
            update_article_status(path, "published_draft", hero_path)
            log(f"  ✓ Published as draft: {path.name}")
        else:
            log(f"  ✗ Publishing failed for {path.name}")


def read_article(path):
    """Split article into frontmatter and body."""
    text = path.read_text()
    if text.startswith("---"):
        parts = text.split("---", 2)
        if len(parts) >= 3:
            return parts[1].strip(), parts[2].strip()
    return "", text


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="ArchonHQ Substack Publisher")
    parser.add_argument("--article", type=str, help="Specific article to publish")
    parser.add_argument("--all-qa-passed", action="store_true", help="Publish all QA-passed articles")
    parser.add_argument("--skip-image", action="store_true", help="Skip hero image generation")
    args = parser.parse_args()

    run(article_path=args.article, all_qa_passed=args.all_qa_passed, skip_image=args.skip_image)
