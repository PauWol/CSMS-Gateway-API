#!/usr/bin/env bash
set -euo pipefail

# Colours
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

info()    { echo -e "${GREEN}[setup]${NC}  $*"; }
warn()    { echo -e "${YELLOW}[setup]${NC}  $*"; }
error()   { echo -e "${RED}[setup]${NC}  $*" >&2; exit 1; }

# check for uv (https://astral.sh/uv/) and install if missing
if command -v uv &>/dev/null; then
    info "uv already installed → $(uv --version)"
else
    warn "uv not found – installing via official installer..."
    curl -LsSf https://astral.sh/uv/install.sh | sh

    # Make uv available in the current shell session
    export PATH="$HOME/.local/bin:$PATH"

    command -v uv &>/dev/null \
        || error "uv installation failed – please install manually: https://docs.astral.sh/uv/"

    info "uv installed → $(uv --version)"
fi

# create virtual environment if it doesn't exist
VENV_DIR=".venv"

if [ -d "$VENV_DIR" ]; then
    warn "Virtual environment already exists at $VENV_DIR – skipping creation"
else
    info "Creating virtual environment at $VENV_DIR..."
    uv venv
    info "Virtual environment created"
fi

# sync dependencies
if [ -f "pyproject.toml" ]; then
    info "Installing dependencies from pyproject.toml..."
    uv sync
elif [ -f "requirements.txt" ]; then
    info "Installing dependencies from requirements.txt..."
    uv pip install -r requirements.txt
else
    error "No pyproject.toml or requirements.txt found – cannot install dependencies"
fi

# final instructions
echo ""
info "Setup complete. Activate the environment and start the server:"
echo ""
echo "    source .venv/bin/activate"
echo "    python start.py"
echo ""