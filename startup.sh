#!/usr/bin/env bash
set -euo pipefail

# ── Resolve the project directory (works whether called by crontab or directly)
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# ── Logging (crontab has no terminal, so tee to a log file)
LOG_FILE="$SCRIPT_DIR/startup.log"
exec > >(tee -a "$LOG_FILE") 2>&1

# ── Colours (suppressed when not a tty)
if [ -t 1 ]; then
  GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; NC='\033[0m'
else
  GREEN=''; YELLOW=''; RED=''; NC=''
fi

info()  { echo -e "${GREEN}[startup]${NC}  $(date '+%H:%M:%S')  $*"; }
warn()  { echo -e "${YELLOW}[startup]${NC}  $(date '+%H:%M:%S')  $*"; }
error() { echo -e "${RED}[startup]${NC}  $(date '+%H:%M:%S')  $*" >&2; exit 1; }

info "=== Boot startup triggered ==="

# ── Full PATH for crontab environments (uv is typically in ~/.local/bin)
export PATH="$HOME/.local/bin:/usr/local/bin:/usr/bin:/bin:$PATH"

# ── Locate uv
UV_BIN="$(command -v uv 2>/dev/null || true)"
if [ -z "$UV_BIN" ]; then
  # Fallback: check the common install location directly
  [ -x "$HOME/.local/bin/uv" ] && UV_BIN="$HOME/.local/bin/uv" \
    || error "uv not found – run setup.sh first to install dependencies"
fi
info "Using uv: $UV_BIN"

# ── Confirm .venv exists
if [ ! -d "$SCRIPT_DIR/.venv" ]; then
  error ".venv not found in $SCRIPT_DIR – run 'uv sync' first"
fi

# ── Confirm start.py exists
if [ ! -f "$SCRIPT_DIR/start.py" ]; then
  error "start.py not found in $SCRIPT_DIR"
fi

# ── Confirm network.py exists
if [ ! -f "$SCRIPT_DIR/network.py" ]; then
  error "network.py not found in $SCRIPT_DIR"
fi

# ✅ Run network.py as a subprocess — waits for it to finish, then continues
info "Launching network.py with uv..."
"$UV_BIN" run --python "$SCRIPT_DIR/.venv/bin/python" "$SCRIPT_DIR/network.py"

info "Launching start.py with uv..."
exec "$UV_BIN" run --python "$SCRIPT_DIR/.venv/bin/python" "$SCRIPT_DIR/start.py"