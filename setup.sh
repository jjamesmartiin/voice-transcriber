#!/usr/bin/env bash
set -euo pipefail

# ==============================================================================
# Voice Transcriber (VT) - Setup Script
# Supports: Linux (Debian/Ubuntu, Fedora, Arch, openSUSE) & macOS (Darwin)
# ==============================================================================

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# Formatting / Colors
CYAN='\033[0;36m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
BOLD='\033[1m'
NC='\033[0m' # No Color

if [ ! -t 1 ]; then
    CYAN=''
    GREEN=''
    YELLOW=''
    RED=''
    BOLD=''
    NC=''
fi

step() {
    echo -e "\n${CYAN}${BOLD}[SETUP]${NC} $1"
}

ok() {
    echo -e "${GREEN}[OK]${NC} $1"
}

warn() {
    echo -e "${YELLOW}[WARN]${NC} $1"
}

err() {
    echo -e "${RED}[ERROR]${NC} $1"
}

# ------------------------------------------------------------------------------
# Flag parsing
# ------------------------------------------------------------------------------
NO_DEV=false
FETCH_MODEL=false

show_help() {
    cat << 'EOF'
Voice Transcriber Setup Script

Usage: ./setup.sh [OPTIONS]

Options:
  --no-dev        Skip development dependencies (pytest, ruff, pyinstaller)
  --fetch         Pre-download and verify Cohere model weights (~2.8 GB)
  -h, --help      Show this help message and exit

Supported Platforms:
  - Linux (Debian/Ubuntu, Fedora/RHEL, Arch/Manjaro, openSUSE)
  - macOS (Apple Silicon & Intel via Homebrew)
EOF
    exit 0
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --no-dev)
            NO_DEV=true
            shift
            ;;
        --fetch)
            FETCH_MODEL=true
            shift
            ;;
        -h|--help)
            show_help
            ;;
        *)
            err "Unknown option: $1"
            echo "Run './setup.sh --help' for available options."
            exit 1
            ;;
    esac
done

echo -e "${CYAN}${BOLD}======================================================${NC}"
echo -e "${CYAN}${BOLD}     Voice Transcriber (VT) Environment Setup         ${NC}"
echo -e "${CYAN}${BOLD}======================================================${NC}"

# ------------------------------------------------------------------------------
# 1. OS & Platform Detection
# ------------------------------------------------------------------------------
OS_TYPE="$(uname -s)"

case "$OS_TYPE" in
    Linux)
        ok "Detected OS: Linux ($(uname -m))"
        ;;
    Darwin)
        ok "Detected OS: macOS / Darwin ($(uname -m))"
        ;;
    *)
        warn "Detected unrecognized OS: $OS_TYPE. Proceeding with generic Unix setup."
        ;;
esac

# ------------------------------------------------------------------------------
# 2. System Package & Dependency Checks
# ------------------------------------------------------------------------------
step "Checking system packages..."

if [ "$OS_TYPE" = "Linux" ]; then
    DISTRO_ID=""
    DISTRO_LIKE=""
    if [ -f /etc/os-release ]; then
        # shellcheck disable=SC1091
        . /etc/os-release
        DISTRO_ID="${ID:-}"
        DISTRO_LIKE="${ID_LIKE:-}"
    fi

    MISSING_PACKAGES=()

    # Check clipboard utility
    if [ -n "${WAYLAND_DISPLAY:-}" ] || [ "${XDG_SESSION_TYPE:-}" = "wayland" ]; then
        if ! command -v wl-copy >/dev/null 2>&1; then
            MISSING_PACKAGES+=("wl-clipboard")
        fi
    else
        if ! command -v xclip >/dev/null 2>&1 && ! command -v xsel >/dev/null 2>&1; then
            MISSING_PACKAGES+=("xclip")
        fi
    fi

    # Check typing injector (ydotool for Wayland, xdotool for X11)
    if [ -n "${WAYLAND_DISPLAY:-}" ] || [ "${XDG_SESSION_TYPE:-}" = "wayland" ]; then
        if ! command -v ydotool >/dev/null 2>&1; then
            MISSING_PACKAGES+=("ydotool")
        fi
    else
        if ! command -v xdotool >/dev/null 2>&1; then
            MISSING_PACKAGES+=("xdotool")
        fi
    fi

    if [ ${#MISSING_PACKAGES[@]} -gt 0 ]; then
        warn "Some recommended system utilities are missing: ${MISSING_PACKAGES[*]}"
        echo "  Install them using your package manager for full clipboard and injection support:"
        case "$DISTRO_ID" in
            ubuntu|debian|pop|mint|elementary)
                echo "    sudo apt-get update && sudo apt-get install -y libportaudio2 libasound2-plugins xclip wl-clipboard xdotool ydotool python3-venv python3-pip"
                ;;
            fedora|rhel|centos)
                echo "    sudo dnf install -y portaudio alsa-lib xclip wl-clipboard xdotool ydotool python3-pip"
                ;;
            arch|manjaro|endeavouros)
                echo "    sudo pacman -S --needed portaudio alsa-lib xclip wl-clipboard xdotool ydotool python-pip"
                ;;
            opensuse*|suse)
                echo "    sudo zypper install -y portaudio alsa xclip wl-clipboard xdotool ydotool python3-pip"
                ;;
            *)
                echo "    Please install: portaudio, alsa-lib, xclip / wl-clipboard, xdotool / ydotool"
                ;;
        esac
    else
        ok "All recommended system CLI utilities are available"
    fi

elif [ "$OS_TYPE" = "Darwin" ]; then
    if ! command -v brew >/dev/null 2>&1; then
        warn "Homebrew is not detected. We recommend installing Homebrew from https://brew.sh"
    else
        ok "Homebrew detected"
        if ! brew list portaudio >/dev/null 2>&1 && [ ! -f /opt/homebrew/include/portaudio.h ] && [ ! -f /usr/local/include/portaudio.h ]; then
            warn "PortAudio is not installed. Microphone capture requires PortAudio."
            echo "  Run: brew install portaudio"
        else
            ok "PortAudio is installed"
        fi
    fi
fi

# ------------------------------------------------------------------------------
# 3. Python 3.10+ Detection
# ------------------------------------------------------------------------------
step "Checking Python version..."

PYTHON_CANDIDATES=("python3" "python" "python3.13" "python3.12" "python3.11" "python3.10")
PYTHON_BIN=""

for candidate in "${PYTHON_CANDIDATES[@]}"; do
    if command -v "$candidate" >/dev/null 2>&1; then
        if "$candidate" -c "import sys; exit(0 if sys.version_info >= (3, 10) else 1)" 2>/dev/null; then
            PYTHON_BIN="$candidate"
            break
        fi
    fi
done

if [ -z "$PYTHON_BIN" ]; then
    err "Python 3.10+ was not found on your PATH."
    if [ "$OS_TYPE" = "Darwin" ]; then
        echo "  Install Python via Homebrew: brew install python@3.12"
    elif [ "$OS_TYPE" = "Linux" ]; then
        echo "  Install Python via your package manager (e.g. sudo apt install python3 python3-venv python3-pip)"
    fi
    exit 1
fi

PY_VERSION_STR="$($PYTHON_BIN --version 2>&1)"
ok "Found compatible Python: $PYTHON_BIN ($PY_VERSION_STR)"

# ------------------------------------------------------------------------------
# 4. Virtual Environment Creation / Verification
# ------------------------------------------------------------------------------
step "Setting up virtual environment (.venv)..."

VENV_DIR="$SCRIPT_DIR/.venv"
VENV_PYTHON="$VENV_DIR/bin/python"
VENV_PIP="$VENV_DIR/bin/pip"

if [ ! -d "$VENV_DIR" ] || [ ! -x "$VENV_PYTHON" ]; then
    echo "Creating virtual environment at $VENV_DIR..."
    "$PYTHON_BIN" -m venv "$VENV_DIR"
    ok "Created virtual environment in .venv"
else
    ok "Existing virtual environment found in .venv"
fi

step "Upgrading base packaging tools..."
"$VENV_PYTHON" -m pip install --quiet --upgrade pip setuptools wheel
ok "Base packaging tools (pip, setuptools, wheel) are up to date"

# ------------------------------------------------------------------------------
# 5. Intelligent PyTorch Installation
# ------------------------------------------------------------------------------
step "Configuring PyTorch..."

if [ "$OS_TYPE" = "Darwin" ]; then
    # macOS: PyPI standard torch includes Apple Silicon MPS support
    echo "Installing PyTorch for macOS (MPS acceleration enabled on Apple Silicon)..."
    "$VENV_PIP" install --quiet torch
    ok "PyTorch installed successfully (macOS / MPS)"
elif [ "$OS_TYPE" = "Linux" ]; then
    HAS_NVIDIA=false
    if command -v nvidia-smi >/dev/null 2>&1; then
        if nvidia-smi >/dev/null 2>&1; then
            HAS_NVIDIA=true
        fi
    fi

    if [ "$HAS_NVIDIA" = true ]; then
        echo "NVIDIA GPU detected. Installing PyTorch with CUDA support..."
        if "$VENV_PIP" install --quiet torch --index-url https://download.pytorch.org/whl/cu124; then
            ok "PyTorch with CUDA 12.4 installed successfully"
        elif "$VENV_PIP" install --quiet torch --index-url https://download.pytorch.org/whl/cu121; then
            ok "PyTorch with CUDA 12.1 installed successfully"
        else
            warn "CUDA PyTorch installation failed, falling back to default PyPI wheel..."
            "$VENV_PIP" install --quiet torch
            ok "PyTorch installed (CPU fallback)"
        fi
    else
        echo "No NVIDIA GPU detected; installing standard PyTorch..."
        "$VENV_PIP" install --quiet torch
        ok "PyTorch installed successfully"
    fi
else
    "$VENV_PIP" install --quiet torch
    ok "PyTorch installed successfully"
fi

# ------------------------------------------------------------------------------
# 6. Install Project Dependencies
# ------------------------------------------------------------------------------
step "Installing project dependencies..."

if [ "$NO_DEV" = true ]; then
    echo "Installing core dependencies from requirements.txt..."
    "$VENV_PIP" install --quiet -r requirements.txt
    ok "Installed core dependencies"
else
    echo "Installing core and development dependencies from requirements-dev.txt..."
    "$VENV_PIP" install --quiet -r requirements-dev.txt
    ok "Installed core and development dependencies"
fi

# ------------------------------------------------------------------------------
# 7. Permissions Check & System Setup
# ------------------------------------------------------------------------------
step "Checking permissions and hardware access..."

if [ "$OS_TYPE" = "Linux" ]; then
    CURRENT_USER="${USER:-$(whoami)}"
    IN_INPUT_GROUP=false
    if groups "$CURRENT_USER" 2>/dev/null | grep -qw "input" || groups 2>/dev/null | grep -qw "input"; then
        IN_INPUT_GROUP=true
    fi

    if [ "$IN_INPUT_GROUP" = true ]; then
        ok "User '$CURRENT_USER' is in the 'input' group"
    else
        warn "User '$CURRENT_USER' is NOT in the 'input' group."
        echo "  Global hotkeys require /dev/input/ access."
        echo "  Run: sudo usermod -a -G input $CURRENT_USER"
        echo "  Then log out and log back in for changes to take effect."
    fi

    # Check /dev/uinput permissions
    if [ -e /dev/uinput ]; then
        if [ -w /dev/uinput ]; then
            ok "uinput write access verified (/dev/uinput)"
        else
            warn "/dev/uinput is not writable by current user."
            echo "  To allow synthetic key injection without root, add a udev rule:"
            echo '  echo '\''KERNEL=="uinput", MODE="0660", GROUP="input", OPTIONS+="static_node=uinput"'\'' | sudo tee /etc/udev/rules.d/99-uinput.rules'
            echo '  sudo udevadm control --reload-rules && sudo udevadm trigger'
        fi
    fi

elif [ "$OS_TYPE" = "Darwin" ]; then
    ok "macOS Permissions Reminder:"
    echo "  1. Accessibility: Grant your terminal app (Terminal/iTerm2/Kitty/Alacritty/Ghostty)"
    echo "     access in System Settings -> Privacy & Security -> Accessibility."
    echo "  2. Microphone: Allow microphone access when prompted on first launch"
    echo "     in System Settings -> Privacy & Security -> Microphone."
fi

# ------------------------------------------------------------------------------
# 8. Model Weights Acquisition (--fetch)
# ------------------------------------------------------------------------------
if [ "$FETCH_MODEL" = true ]; then
    step "Pre-downloading and verifying Cohere ASR model weights (~2.8 GB)..."
    "$VENV_PYTHON" src/model_download.py
    ok "Model weights are installed and verified"
fi

# ------------------------------------------------------------------------------
# 9. Completion Summary
# ------------------------------------------------------------------------------
echo -e "\n${GREEN}${BOLD}======================================================${NC}"
echo -e "${GREEN}${BOLD}  Voice Transcriber Setup Complete!                   ${NC}"
echo -e "${GREEN}${BOLD}======================================================${NC}"
echo "To launch Voice Transcriber:"
echo "  ./run.sh"
echo ""
echo "To check status or send control commands:"
echo "  ./run.sh status"
echo "  ./run.sh toggle"
echo ""
