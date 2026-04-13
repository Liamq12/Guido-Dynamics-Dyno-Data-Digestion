@echo off
cd %~dp0
pip install -r "$SCRIPT_DIR/requirements.txt"
start "User Terminal" python "UserTerminal.py"
exit