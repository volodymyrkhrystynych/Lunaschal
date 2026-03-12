#!/usr/bin/env bash
# Creates the Python venv and installs STT dependencies.
# Run once: bash stt/setup.sh

set -e
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV="$SCRIPT_DIR/.venv"

echo "Creating virtual environment at $VENV ..."
python3 -m venv "$VENV"

echo "Installing Python dependencies..."
"$VENV/bin/pip" install --upgrade pip -q
"$VENV/bin/pip" install -r "$SCRIPT_DIR/requirements.txt"

# Create CUDA compatibility symlinks.
# ctranslate2 wheels are built against CUDA 12 (libcublas.so.12) but Arch
# ships CUDA 13 (libcublas.so.13). Symlink inside stt/lib/ so we don't
# touch system files. run_service.sh prepends this dir to LD_LIBRARY_PATH.
CUDA_LIB="${CUDA_LIB:-/opt/cuda/lib64}"
if [ -f "$CUDA_LIB/libcublas.so.13" ]; then
    echo "Creating CUDA 12→13 compatibility symlinks in stt/lib/ ..."
    mkdir -p "$SCRIPT_DIR/lib"
    ln -sf "$CUDA_LIB/libcublas.so.13"   "$SCRIPT_DIR/lib/libcublas.so.12"
    ln -sf "$CUDA_LIB/libcublasLt.so.13" "$SCRIPT_DIR/lib/libcublasLt.so.12"
fi

echo ""
echo "Done. System packages also required:"
echo "  sudo pacman -S wtype portaudio"
echo ""
echo "Start the STT service:    ./stt/run_service.sh"
echo "Start the voice listener: ./stt/run_listener.sh"
