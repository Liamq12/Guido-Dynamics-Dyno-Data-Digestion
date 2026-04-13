#!/usr/bin/env bash

set -euo pipefail

# --- Get current folder ---
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

echo "Welcome to Team Guido Dynamics!"
echo "Starting Services..."


# --- Set up virtual environment ---
VENV_DIR="$SCRIPT_DIR/venv"

if [ ! -d "$VENV_DIR" ]; then
    echo "Creating virtual environment..."
    python3 -m venv "$VENV_DIR"
fi

echo "Activating virtual environment..."
source "$VENV_DIR/bin/activate"

# --- Install required Python packages ---
echo "Installing dependencies..."
pip install -r "$SCRIPT_DIR/requirements.txt"

# --- Start Python UDP ingest script ---
echo "Starting UDP Ingest..."
"$VENV_DIR/bin/python" "$SCRIPT_DIR/main.py" &
PYTHON_PID=$!


echo "All processes started. Press any key to stop them..."
read -n 1 -s

# --- Cleanup routine ---
echo "Stopping Python script..."
kill "$PYTHON_PID" 2>/dev/null || true

echo "Cleanup complete."
