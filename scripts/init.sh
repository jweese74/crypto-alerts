#!/usr/bin/env bash
# ──────────────────────────────────────────────────────────────────────────────
# Crypto Alert System — first-run helper
#
# Usage:  bash scripts/init.sh
#
# This script:
#   1. Copies .env.example → .env if .env does not exist
#   2. Generates a secure SECRET_KEY automatically
#   3. Prompts for the remaining required values
#   4. Starts the stack with docker compose up -d
# ──────────────────────────────────────────────────────────────────────────────

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

cd "$PROJECT_DIR"

echo ""
echo "📡  Crypto Alert System — First-run Setup"
echo "────────────────────────────────────────────"

# ── .env setup ────────────────────────────────────────────────────────────────
if [ -f .env ]; then
    echo "✅  .env already exists — skipping copy."
else
    cp .env.example .env
    echo "✅  .env created from .env.example"
fi

# ── Generate SECRET_KEY if it is still the placeholder ────────────────────────
if grep -q "change-me-generate-with-openssl" .env; then
    KEY=$(openssl rand -hex 32)
    sed -i "s|change-me-generate-with-openssl-rand-hex-32|${KEY}|g" .env
    echo "✅  SECRET_KEY generated automatically."
fi

# ── Prompt for required values if still placeholder ───────────────────────────
prompt_replace() {
    local label="$1"
    local key="$2"
    local placeholder="$3"
    if grep -q "$placeholder" .env; then
        read -rp "   Enter $label: " value
        sed -i "s|${placeholder}|${value}|g" .env
    fi
}

echo ""
echo "Required values:"
prompt_replace "POSTGRES_PASSWORD (database password)" \
    "POSTGRES_PASSWORD" "change-me-strong-password"
prompt_replace "FIRST_ADMIN_EMAIL (your login email)" \
    "FIRST_ADMIN_EMAIL" "admin@example.com"
prompt_replace "FIRST_ADMIN_PASSWORD (must be 8+ chars, mixed case + digit)" \
    "FIRST_ADMIN_PASSWORD" "ChangeMe1!"

# ── Start the stack ───────────────────────────────────────────────────────────
echo ""
echo "🚀  Starting the stack…"
docker compose up -d

echo ""
echo "────────────────────────────────────────────"
echo "✅  Done!  Open http://localhost:8000 in your browser."
echo "    Log in with the admin email and password you just set."
echo ""
