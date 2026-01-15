#!/usr/bin/env bash
set -euo pipefail

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

print_status() { echo -e "${BLUE}==>${NC} $1"; }
print_success() { echo -e "${GREEN}✓${NC} $1"; }
print_warning() { echo -e "${YELLOW}⚠${NC} $1"; }
print_error() { echo -e "${RED}✗${NC} $1"; }

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

echo -e "${BLUE}"
echo "╔═══════════════════════════════════════╗"
echo "║         Oflow Setup Script            ║"
echo "╚═══════════════════════════════════════╝"
echo -e "${NC}"

print_status "Checking system dependencies..."

MISSING_DEPS=()

if ! command -v wtype &>/dev/null; then
    MISSING_DEPS+=("wtype")
    print_error "wtype not found (required for typing text)"
else
    print_success "wtype found"
fi

if ! command -v python3 &>/dev/null; then
    MISSING_DEPS+=("python")
    print_error "python3 not found"
else
    print_success "Python found: $(python3 --version)"
fi

if [[ ${#MISSING_DEPS[@]} -gt 0 ]]; then
    echo ""
    echo "Install missing packages:"
    echo -e "  ${YELLOW}sudo pacman -S ${MISSING_DEPS[*]}${NC}"
    echo ""
    read -p "Install now? (requires sudo) [Y/n] " -n 1 -r
    echo
    if [[ ! $REPLY =~ ^[Nn]$ ]]; then
        sudo pacman -S --noconfirm "${MISSING_DEPS[@]}"
        print_success "Dependencies installed"
    else
        print_error "Cannot continue without required packages."
        exit 1
    fi
fi

print_status "Checking for uv package manager..."

if ! command -v uv &>/dev/null; then
    if [[ -f "$HOME/.local/bin/uv" ]]; then
        export PATH="$HOME/.local/bin:$PATH"
    elif [[ -f "$HOME/.cargo/bin/uv" ]]; then
        export PATH="$HOME/.cargo/bin:$PATH"
    fi
fi

if ! command -v uv &>/dev/null; then
    print_status "Installing uv..."
    curl -LsSf https://astral.sh/uv/install.sh | sh
    export PATH="$HOME/.local/bin:$PATH"
    print_success "uv installed"
else
    print_success "uv found: $(uv --version)"
fi

print_status "Setting up Python environment..."

if [[ ! -d ".venv" ]]; then
    uv venv
    print_success "Virtual environment created"
else
    print_success "Virtual environment exists"
fi

source .venv/bin/activate

print_status "Installing Python dependencies..."
uv pip install -q -e .
print_success "Dependencies installed"

echo ""
echo -e "${GREEN}╔═══════════════════════════════════════╗${NC}"
echo -e "${GREEN}║         Setup Complete!               ║${NC}"
echo -e "${GREEN}╚═══════════════════════════════════════╝${NC}"
echo ""
echo "For development:"
echo -e "  ${BLUE}make dev${NC}              # Run frontend + backend with hot reload"
echo ""
echo "For production install:"
echo -e "  ${BLUE}make install${NC}          # Build and install to ~/.local/bin"
echo ""
echo "After install, press ${YELLOW}Super+D${NC} to start dictating!"
echo "Configure your Groq API key in Settings (click the ○ icon in Waybar)."
