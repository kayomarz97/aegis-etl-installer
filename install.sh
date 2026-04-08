#!/usr/bin/env bash
# Aegis-ETL Installer
# Usage: curl -sL https://raw.githubusercontent.com/kayomarz97/aegis-etl-installer/master/install.sh | bash

set -e

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; CYAN='\033[0;36m'; NC='\033[0m'

echo -e "${CYAN}"
echo "  ╔═══════════════════════════════════════╗"
echo "  ║         Aegis-ETL Installer           ║"
echo "  ║   On-premise Enterprise Doc Ingestor  ║"
echo "  ╚═══════════════════════════════════════╝"
echo -e "${NC}"

# --- Prerequisites check ---
for cmd in docker git python3; do
  if ! command -v "$cmd" &>/dev/null; then
    echo -e "${RED}Error: '$cmd' is required but not installed.${NC}"
    case "$cmd" in
      docker) echo "  Install Docker: https://docs.docker.com/engine/install/" ;;
      python3) echo "  Install Python 3.9+: https://www.python.org/downloads/" ;;
    esac
    exit 1
  fi
done

if ! docker compose version &>/dev/null; then
  echo -e "${RED}Error: Docker Compose v2 is required.${NC}"
  echo "  Install: https://docs.docker.com/compose/install/"
  exit 1
fi

echo -e "${GREEN}✓ Prerequisites OK${NC}"

# --- Choose install directory ---
INSTALL_DIR="${AEGIS_INSTALL_DIR:-$HOME/aegis-etl}"

if [ -d "$INSTALL_DIR" ]; then
  echo -e "${YELLOW}Directory $INSTALL_DIR already exists.${NC}"
  read -r -p "  Use it anyway? (existing .env will be preserved) [Y/n]: " ans
  ans="${ans:-Y}"
  if [[ ! "$ans" =~ ^[Yy] ]]; then
    echo "Aborted."
    exit 0
  fi
else
  mkdir -p "$INSTALL_DIR"
fi

cd "$INSTALL_DIR"
echo -e "${GREEN}✓ Install directory: $INSTALL_DIR${NC}"

# --- Download installer files ---
# VPS is primary (no CDN caching). GitHub is fallback.
VPS_URL="http://161.97.93.228:8181"
GH_URL="https://raw.githubusercontent.com/kayomarz97/aegis-etl-installer/master"

_download() {
  local f="$1"
  mkdir -p "$(dirname "$f")"
  if curl -fsSL --connect-timeout 5 "$VPS_URL/$f" -o "$f" 2>/dev/null; then
    return 0
  fi
  echo -e "  ${YELLOW}VPS unreachable — falling back to GitHub for $f${NC}"
  curl -fsSL "$GH_URL/$f" -o "$f"
}

echo "  Downloading installer files..."
for f in docker-compose.yml ollama-entrypoint.sh cli/setup.py; do
  _download "$f"
done
chmod +x ollama-entrypoint.sh

echo -e "${GREEN}✓ Files downloaded${NC}"

# --- Install Python deps ---
echo "  Installing Python dependencies..."
python3 -m pip install --quiet --upgrade pip
python3 -m pip install --quiet rich requests 2>/dev/null || true

echo -e "${GREEN}✓ Python deps ready${NC}"

# --- Launch wizard ---
echo ""
echo -e "${CYAN}Starting setup wizard...${NC}"
echo ""
python3 cli/setup.py
