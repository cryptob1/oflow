#!/usr/bin/env bash
#
# Oflow Setup Script
# Automates installation of dependencies and configuration
#
set -euo pipefail

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

print_status() { echo -e "${BLUE}==>${NC} $1"; }
print_success() { echo -e "${GREEN}✓${NC} $1"; }
print_warning() { echo -e "${YELLOW}⚠${NC} $1"; }
print_error() { echo -e "${RED}✗${NC} $1"; }

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

echo -e "${BLUE}"
echo "╔═══════════════════════════════════════╗"
echo "║       Oflow Setup Script        ║"
echo "╚═══════════════════════════════════════╝"
echo -e "${NC}"

# -----------------------------------------------------------------------------
# Check system dependencies
# -----------------------------------------------------------------------------
print_status "Checking system dependencies..."

MISSING_DEPS=()
REQUIRED_DEPS=()  # These must be installed for the app to work

check_cmd() {
    if ! command -v "$1" &>/dev/null; then
        MISSING_DEPS+=("$2")
        return 1
    fi
    return 0
}

check_required() {
    if ! command -v "$1" &>/dev/null; then
        REQUIRED_DEPS+=("$2")
        MISSING_DEPS+=("$2")
        return 1
    fi
    return 0
}

check_cmd python3 python && print_success "Python found: $(python3 --version)" || true
check_required wtype wtype || true  # Required for typing text into windows
check_cmd notify-send libnotify || true
check_cmd pactl "pulseaudio-utils or pipewire-pulse" || true

if [[ ${#MISSING_DEPS[@]} -gt 0 ]]; then
    if [[ ${#REQUIRED_DEPS[@]} -gt 0 ]]; then
        print_error "Missing required packages: ${REQUIRED_DEPS[*]}"
        echo ""
        echo "These packages are required for oflow to work:"
        echo -e "  ${YELLOW}wtype${NC} - Types transcribed text into your active window"
        echo ""
    fi
    if [[ ${#MISSING_DEPS[@]} -gt ${#REQUIRED_DEPS[@]} ]]; then
        print_warning "Missing optional packages: $(echo "${MISSING_DEPS[*]}" | tr ' ' '\n' | grep -v "$(echo "${REQUIRED_DEPS[*]}" | tr ' ' '\n')" | tr '\n' ' ')"
    fi
    echo ""
    echo "Install with:"
    echo -e "  ${YELLOW}sudo pacman -S ${MISSING_DEPS[*]}${NC}"
    echo ""

    if [[ ${#REQUIRED_DEPS[@]} -gt 0 ]]; then
        # Required deps - strongly encourage installation
        read -p "Install required packages now? (requires sudo) [Y/n] " -n 1 -r
        echo
        if [[ ! $REPLY =~ ^[Nn]$ ]]; then
            sudo pacman -S --noconfirm "${MISSING_DEPS[@]}"
            print_success "System dependencies installed"
        else
            print_error "Cannot continue without required packages. Please install them and run setup again."
            exit 1
        fi
    else
        # Only optional deps missing
        read -p "Install optional packages? (requires sudo) [y/N] " -n 1 -r
        echo
        if [[ $REPLY =~ ^[Yy]$ ]]; then
            sudo pacman -S --noconfirm "${MISSING_DEPS[@]}"
            print_success "System dependencies installed"
        else
            print_warning "Skipping optional packages"
        fi
    fi
else
    print_success "All system dependencies present"
fi

# -----------------------------------------------------------------------------
# Install uv if needed
# -----------------------------------------------------------------------------
print_status "Checking for uv package manager..."

if ! command -v uv &>/dev/null; then
    # Check common install locations
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

# -----------------------------------------------------------------------------
# Create virtual environment and install dependencies
# -----------------------------------------------------------------------------
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

# -----------------------------------------------------------------------------
# Configure .env
# -----------------------------------------------------------------------------
print_status "Configuring environment..."

if [[ ! -f ".env" ]]; then
    cp .env.example .env
    print_success "Created .env from template"
    
    echo ""
    print_warning "API key required!"
    echo ""
    echo "Choose your transcription backend:"
    echo "  1) OpenAI (recommended - 100% accuracy, ~\$0.005/use)"
    echo "  2) Gemini via OpenRouter (cheaper - 30% consistency, ~\$0.0001/use)"
    echo "  3) Skip (configure manually later)"
    echo ""
    read -p "Enter choice [1-3]: " -n 1 -r
    echo
    
    case $REPLY in
        1)
            read -p "Enter your OpenAI API key (sk-...): " api_key
            if [[ -n "$api_key" ]]; then
                sed -i "s|OPENAI_API_KEY=.*|OPENAI_API_KEY=$api_key|" .env
                sed -i "s|USE_OPENAI_DIRECT=.*|USE_OPENAI_DIRECT=true|" .env
                sed -i "s|USE_OPENROUTER_GEMINI=.*|USE_OPENROUTER_GEMINI=false|" .env
                print_success "OpenAI configured"
            fi
            ;;
        2)
            read -p "Enter your OpenRouter API key (sk-or-v1-...): " api_key
            if [[ -n "$api_key" ]]; then
                sed -i "s|OPENROUTER_API_KEY=.*|OPENROUTER_API_KEY=$api_key|" .env
                sed -i "s|USE_OPENAI_DIRECT=.*|USE_OPENAI_DIRECT=false|" .env
                sed -i "s|USE_OPENROUTER_GEMINI=.*|USE_OPENROUTER_GEMINI=true|" .env
                print_success "Gemini/OpenRouter configured"
            fi
            ;;
        *)
            print_warning "Skipped - edit .env manually before running"
            ;;
    esac
else
    print_success ".env already exists"
fi

# -----------------------------------------------------------------------------
# Hyprland keybindings
# -----------------------------------------------------------------------------
echo ""
print_status "Hyprland keybinding setup..."

HYPR_BINDING="bind = SUPER, I, exec, $SCRIPT_DIR/.venv/bin/python $SCRIPT_DIR/oflow.py start
bindr = SUPER, I, exec, $SCRIPT_DIR/.venv/bin/python $SCRIPT_DIR/oflow.py stop"

echo ""
echo "Add these lines to ~/.config/hypr/bindings.conf:"
echo ""
echo -e "${YELLOW}$HYPR_BINDING${NC}"
echo ""

BINDINGS_FILE="$HOME/.config/hypr/bindings.conf"
if [[ -f "$BINDINGS_FILE" ]]; then
    if grep -q "oflow" "$BINDINGS_FILE"; then
        print_success "Keybindings already configured"
    else
        read -p "Add keybindings automatically? [y/N] " -n 1 -r
        echo
        if [[ $REPLY =~ ^[Yy]$ ]]; then
            echo "" >> "$BINDINGS_FILE"
            echo "# Oflow voice dictation" >> "$BINDINGS_FILE"
            echo "$HYPR_BINDING" >> "$BINDINGS_FILE"
            print_success "Keybindings added to $BINDINGS_FILE"
            print_warning "Run 'hyprctl reload' to apply"
        fi
    fi
else
    print_warning "Hyprland config not found at $BINDINGS_FILE"
    echo "Add the keybindings manually to your Hyprland config"
fi

# -----------------------------------------------------------------------------
# Done!
# -----------------------------------------------------------------------------
echo ""
echo -e "${GREEN}╔═══════════════════════════════════════╗${NC}"
echo -e "${GREEN}║         Setup Complete!               ║${NC}"
echo -e "${GREEN}╚═══════════════════════════════════════╝${NC}"
echo ""
echo "Quick start:"
echo -e "  ${BLUE}source .venv/bin/activate${NC}"
echo -e "  ${BLUE}python oflow.py${NC}        # Start server"
echo ""
echo "Or use the Makefile:"
echo -e "  ${BLUE}make run${NC}                     # Start server"
echo -e "  ${BLUE}make test${NC}                    # Run tests"
echo ""
echo "Then press Super+I to start dictating!"
