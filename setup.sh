#!/usr/bin/env bash
# ── ArchonHQ Content Engine — One-Command Installer ────────────────────────
# Run: ./setup.sh
# This script checks prerequisites, creates directories, configures .env,
# validates environment, tests API connectivity, and installs dependencies.

set -euo pipefail

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m' # No Color

ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"
SCRIPTS_DIR="$ROOT_DIR/scripts"
ENV_FILE="$ROOT_DIR/.env"
ENV_EXAMPLE="$ROOT_DIR/.env.example"
HERMES_ENV="$HOME/.hermes/.env"

info()  { echo -e "${CYAN}[setup]${NC} $*"; }
ok()    { echo -e "${GREEN}[setup]${NC} ✓ $*"; }
warn()  { echo -e "${YELLOW}[setup]${NC} ⚠ $*"; }
fail()  { echo -e "${RED}[setup]${NC} ✗ $*"; }

# ── 1. Check Python 3.10+ ─────────────────────────────────────────────────

info "Checking Python version..."
if ! command -v python3 &>/dev/null; then
    fail "Python 3 is not installed. Install Python 3.10+ and try again."
    exit 1
fi

PY_VERSION=$(python3 -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
PY_MAJOR=$(python3 -c "import sys; print(sys.version_info.major)")
PY_MINOR=$(python3 -c "import sys; print(sys.version_info.minor)")

if [ "$PY_MAJOR" -lt 3 ] || { [ "$PY_MAJOR" -eq 3 ] && [ "$PY_MINOR" -lt 10 ]; }; then
    fail "Python 3.10+ required, found $PY_VERSION"
    exit 1
fi
ok "Python $PY_VERSION found"

# ── 2. Create required directories ─────────────────────────────────────────

info "Creating required directories..."
mkdir -p "$ROOT_DIR/qa_reports"
mkdir -p "$ROOT_DIR/metrics"
mkdir -p "$ROOT_DIR/social"
mkdir -p "$ROOT_DIR/images"
mkdir -p "$ROOT_DIR/series_themes"
mkdir -p "$ROOT_DIR/growth_reports"
ok "Directories created: qa_reports/, metrics/, social/, images/, series_themes/, growth_reports/"

# ── 2b. Brand/content onboarding ───────────────────────────────────────────

if [ ! -f "$ROOT_DIR/config.yaml" ] || [ ! -f "$ROOT_DIR/content_profile.md" ]; then
    if [ -t 0 ]; then
        info "Running brand/content onboarding..."
        python3 "$SCRIPTS_DIR/onboard.py"
        ok "Onboarding complete: config.yaml, content_profile.md, and series theme created"
    else
        warn "Skipping interactive onboarding because stdin is not a terminal."
        warn "Run later: python3 scripts/onboard.py"
    fi
else
    ok "Content profile already exists"
fi

# ── 3. Configure .env ──────────────────────────────────────────────────────

info "Configuring environment..."
if [ ! -f "$ENV_FILE" ]; then
    if [ -f "$ENV_EXAMPLE" ]; then
        cp "$ENV_EXAMPLE" "$ENV_FILE"
        warn "Created .env from .env.example"
        warn "EDIT $ENV_FILE with your API keys before continuing!"
    else
        fail ".env.example not found — cannot create .env"
        exit 1
    fi
else
    ok ".env already exists"
fi

# Also copy to ~/.hermes/.env if it doesn't exist (scripts load from there)
if [ ! -f "$HERMES_ENV" ] && [ -f "$ENV_FILE" ]; then
    mkdir -p "$(dirname "$HERMES_ENV")"
    cp "$ENV_FILE" "$HERMES_ENV"
    ok "Copied .env to $HERMES_ENV (scripts load from this path)"
fi

# ── 4. Load environment and validate required vars ─────────────────────────

info "Validating environment variables..."

# Load from .env file
if [ -f "$ENV_FILE" ]; then
    set -a
    while IFS='=' read -r key value; do
        key=$(echo "$key" | xargs)
        # Skip comments and empty lines
        [[ -z "$key" || "$key" =~ ^# ]] && continue
        # Only set if not already set in environment
        if [ -z "${!key:-}" ]; then
            export "$key=$value"
        fi
    done < <(grep -v '^\s*#' "$ENV_FILE" | grep -v '^\s*$')
    set +a
fi

# Also load from ~/.hermes/.env if it exists
if [ -f "$HERMES_ENV" ]; then
    set -a
    while IFS='=' read -r key value; do
        key=$(echo "$key" | xargs)
        [[ -z "$key" || "$key" =~ ^# ]] && continue
        if [ -z "${!key:-}" ]; then
            export "$key=$value"
        fi
    done < <(grep -v '^\s*#' "$HERMES_ENV" | grep -v '^\s*$')
    set +a
fi

REQUIRED_VARS=("OPENROUTER_API_KEY")
OPTIONAL_VARS=(
    "OPENAI_API_KEY"
    "RESEND_API_KEY"
    "RESEND_FROM_EMAIL"
    "ARCHONHQ_SUBSTACK_EMAIL"
    "ARCHONHQ_DEVTO_API_KEY"
    "IDEA_MODEL"
    "DRAFT_MODEL"
    "PUBLISH_MODEL"
    "DISTRIBUTE_MODEL"
)

MISSING_REQUIRED=0
for var in "${REQUIRED_VARS[@]}"; do
    if [ -z "${!var:-}" ]; then
        fail "Required: $var is not set"
        MISSING_REQUIRED=1
    else
        ok "Required: $var is set"
    fi
done

for var in "${OPTIONAL_VARS[@]}"; do
    if [ -z "${!var:-}" ]; then
        warn "Optional: $var is not set (some features will be disabled)"
    else
        ok "Optional: $var is set"
    fi
done

if [ "$MISSING_REQUIRED" -eq 1 ]; then
    fail "Required environment variables are missing."
    fail "Edit $ENV_FILE and run this script again."
    exit 1
fi

# ── 5. Test API connectivity ───────────────────────────────────────────────

info "Testing OpenRouter API connectivity..."
OPENROUTER_RESPONSE=$(python3 -c "
import requests, sys
try:
    r = requests.post(
        'https://openrouter.ai/api/v1/chat/completions',
        headers={
            'Authorization': 'Bearer $OPENROUTER_API_KEY',
            'Content-Type': 'application/json',
        },
        json={
            'model': 'deepseek/deepseek-chat-v3-0324',
            'messages': [{'role': 'user', 'content': 'Say OK'}],
            'max_tokens': 5,
        },
        timeout=30,
    )
    if r.status_code == 200:
        print('OK')
    else:
        print(f'HTTP_{r.status_code}', file=sys.stderr)
        sys.exit(1)
except Exception as e:
    print(str(e), file=sys.stderr)
    sys.exit(1)
" 2>&1) || true

if [ "$OPENROUTER_RESPONSE" = "OK" ]; then
    ok "OpenRouter API connectivity verified"
else
    warn "OpenRouter API test failed: $OPENROUTER_RESPONSE"
    warn "Check your OPENROUTER_API_KEY and network connectivity"
fi

# ── 6. Install Python dependencies ─────────────────────────────────────────

info "Installing Python dependencies..."

# Collect all third-party imports from scripts
# Found: requests (idea_gen, draft_gen, substack_pub, distribution_engine, gen_heroes, llm_prose_fix)
#         PIL/Pillow (substack_pub, gen_heroes - optional, for image cropping)
# All other imports are stdlib (json, os, re, sys, argparse, subprocess, pathlib, datetime, etc.)

python3 -m pip install --user --quiet requests Pillow 2>/dev/null || \
    python3 -m pip install --user requests Pillow

ok "Python dependencies installed: requests, Pillow"

# ── 7. Make scripts executable ─────────────────────────────────────────────

info "Making scripts executable..."
if [ -d "$SCRIPTS_DIR" ]; then
    chmod +x "$SCRIPTS_DIR"/*.py 2>/dev/null || true
    ok "All scripts in scripts/ are executable"
else
    warn "scripts/ directory not found at $SCRIPTS_DIR"
fi

# ── 8. Summary ─────────────────────────────────────────────────────────────

echo ""
echo -e "${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${GREEN}  ArchonHQ Content Engine — Setup Complete!${NC}"
echo -e "${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo ""
echo "  Next steps:"
echo ""
echo "  1. Edit your .env if you haven't already:"
echo "     nano $ENV_FILE"
echo ""
echo "  2. Test the pipeline (dry run):"
echo "     python3 scripts/idea_generator.py --dry-run"
echo ""
echo "  3. Generate your first idea:"
echo "     python3 scripts/idea_generator.py"
echo ""
echo "  4. Generate a draft from the best idea:"
echo "     python3 scripts/draft_generator.py --auto-approve-latest"
echo ""
echo "  5. Run QA on the draft:"
echo "     python3 scripts/qa_engine.py --all"
echo ""
echo "  6. Publish to Substack:"
echo "     python3 scripts/substack_publisher.py --all-qa-passed"
echo ""
echo "  7. Distribute:"
echo "     python3 scripts/distribution_engine.py --all-published"
echo ""
echo "  Set up cron for daily automation:"
echo "     0 6  * * * cd $ROOT_DIR && python3 scripts/idea_generator.py"
echo "     0 7  * * * cd $ROOT_DIR && python3 scripts/draft_generator.py --auto-approve-latest"
echo "     0 8  * * * cd $ROOT_DIR && python3 scripts/qa_engine.py --all"
echo "     0 9  * * * cd $ROOT_DIR && python3 scripts/substack_publisher.py --all-qa-passed"
echo "     0 10 * * * cd $ROOT_DIR && python3 scripts/distribution_engine.py --all-published"
echo ""
echo -e "${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
