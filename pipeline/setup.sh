#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_PATH="$SCRIPT_DIR/.venv"

if [ ! -d "$VENV_PATH" ]; then
  python3 -m venv "$VENV_PATH"
fi

"$VENV_PATH/bin/python" -m pip install --upgrade pip
"$VENV_PATH/bin/python" -m pip install -r "$SCRIPT_DIR/requirements.txt"

if command -v nvidia-smi >/dev/null 2>&1; then
  "$VENV_PATH/bin/python" -m pip install --upgrade --index-url https://download.pytorch.org/whl/cu128 torch==2.8.0 torchaudio==2.8.0 torchvision==0.23.0
fi
