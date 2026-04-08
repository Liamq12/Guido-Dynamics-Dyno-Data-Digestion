#!/usr/bin/env bash
set -euo pipefail
 
# --- Get current folder ---
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"
 
echo "Welcome to Team Guido Dynamics!"
echo "Starting Services..."
 
# Stop Grafana if running
sudo systemctl stop grafana-server 2>/dev/null || true
 
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
 
# --- Start InfluxDB in a new terminal ---
echo "Starting InfluxDB..."
"$SCRIPT_DIR/influxdb2-2.7.12-linux/influxd" &
INFLUX_PID=$!
 
# --- Copy Grafana config and start Grafana ---
sudo cp "$SCRIPT_DIR/grafana.ini" /etc/grafana/grafana.ini
sudo systemctl start grafana-server
 
# --- Start Python UDP ingest script ---
echo "Starting UDP Ingest..."
"$VENV_DIR/bin/python" "$SCRIPT_DIR/main.py" &
PYTHON_PID=$!
 
# --- Open user terminal ---
echo "Starting User Terminal..."
if command -v x-terminal-emulator &>/dev/null; then
    x-terminal-emulator -e bash "$SCRIPT_DIR/start_terminal.sh" &
elif command -v gnome-terminal &>/dev/null; then
    gnome-terminal -- bash "$SCRIPT_DIR/start_terminal.sh" &
elif command -v xterm &>/dev/null; then
    xterm -e bash "$SCRIPT_DIR/start_terminal.sh" &
else
    echo "No terminal emulator found. Skipping user terminal launch."
fi
 
echo "All processes started. Press any key to stop them..."
read -n 1 -s
 
# --- Cleanup routine ---
echo "Stopping Python script..."
kill "$PYTHON_PID" 2>/dev/null || true
 
echo "Stopping Grafana..."
sudo systemctl stop grafana-server 2>/dev/null || true
 
echo "Stopping InfluxDB..."
kill "$INFLUX_PID" 2>/dev/null || true
 
echo "Cleanup complete."
 