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
# NetworkManager polkit rule (allows pi user to manage networking without sudo)
info "Setting up NetworkManager permissions..."
POLKIT_DIR="/etc/polkit-1/localauthority/50-local.d"
POLKIT_FILE="$POLKIT_DIR/networkmanager.pkla"
if [ -f "$POLKIT_FILE" ]; then
warn "NetworkManager polkit rule already exists – skipping"
else
sudo mkdir -p "$POLKIT_DIR"
sudo tee "$POLKIT_FILE" > /dev/null <<EOF
[Allow pi user to manage NetworkManager]
Identity=unix-user:pi
Action=org.freedesktop.NetworkManager.*
ResultAny=yes
ResultInactive=yes
ResultActive=yes
EOF
sudo systemctl restart polkit
info "NetworkManager permissions configured"
fi
# ask about port 80 setup
echo ""
read -p "Do you want to serve the app on port 80? (y/n) " -n 1 -r
echo ""
if [[ $REPLY =~ ^[Yy]$ ]]; then
info "Setting up port 80 serving..."
# Get Python executable path
REAL_PYTHON=$(readlink -f /usr/bin/python3)
info "Using Python: $REAL_PYTHON"
# Set setcap capability to allow binding to port 80
info "Setting CAP_NET_BIND_SERVICE capability (this requires sudo)..."
sudo setcap cap_net_bind_service=+ep "$REAL_PYTHON" \
|| error "Failed to set capability. Check your sudo permissions."
info "Capability set successfully"
# Update .env file
ENV_FILE=".env"
if [ -f "$ENV_FILE" ]; then
    grep -v "^PORT=" "$ENV_FILE" > "$ENV_FILE.tmp" && mv "$ENV_FILE.tmp" "$ENV_FILE"
fi
echo "PORT=80" >> "$ENV_FILE"
info ".env updated with PORT=80"

# ── NEW: set capability on the venv python too ──
VENV_PYTHON=$(readlink -f "$VENV_DIR/bin/python")
info "Setting CAP_NET_BIND_SERVICE on venv Python: $VENV_PYTHON"
sudo setcap cap_net_bind_service=+ep "$VENV_PYTHON" \
    || error "Failed to set capability on venv Python."
info "Capability set on venv Python"
echo ""
info "Port 80 setup complete! You can now start the server without sudo:"
echo ""
echo "    source .venv/bin/activate"
echo "    uv run start.py"
echo ""
info "The app will be served on http://localhost"
else
echo ""
info "Setup complete. Activate the environment and start the server:"
echo ""
echo "    source .venv/bin/activate"
echo "    uv run start.py"
echo ""
info "The app will be served on http://localhost:8000"
fi
# ── Ask about running on boot via crontab
echo ""
read -p "Do you want the app to start automatically on boot? (y/n) " -n 1 -r
echo ""
if [[ $REPLY =~ ^[Yy]$ ]]; then
  STARTUP_SCRIPT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/startup.sh"

  # Ensure startup.sh exists and is executable
  if [ ! -f "$STARTUP_SCRIPT" ]; then
    error "startup.sh not found at $STARTUP_SCRIPT – make sure it is in the same directory as setup.sh"
  fi
  chmod +x "$STARTUP_SCRIPT"
  info "Made startup.sh executable"

  # Build the crontab entry (10s delay gives networking time to come up after boot)
  CRON_ENTRY="@reboot sleep 10 && $STARTUP_SCRIPT"

  # Add only if not already present
  if crontab -l 2>/dev/null | grep -qF "$STARTUP_SCRIPT"; then
    warn "Boot crontab entry already exists – skipping"
  else
    # Append to existing crontab (or create fresh one)
    ( crontab -l 2>/dev/null; echo "$CRON_ENTRY" ) | crontab -
    info "Crontab entry added:"
    echo ""
    echo "    $CRON_ENTRY"
    echo ""
    info "The app will start automatically after every reboot."
    info "Logs will be written to: $(dirname "$STARTUP_SCRIPT")/startup.log"
  fi
else
  info "Skipping boot startup setup."
  echo ""
  echo "    You can enable it later by running:"
  echo "    crontab -e"
  echo "    and adding:  @reboot sleep 10 && $(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/startup.sh"
  echo ""
fi