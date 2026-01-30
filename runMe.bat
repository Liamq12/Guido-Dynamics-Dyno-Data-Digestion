@echo off
setlocal ENABLEEXTENSIONS

rem --- Get current folder ---
set SCRIPT_DIR=%~dp0
cd /d "%SCRIPT_DIR%"

echo Welcome to Team Guido Dynamics!
echo Starting Services...

net stop grafana

rem -- Make sure user has influx db 
python -m pip install influxdb-client
python -m pip install rich
python -m pip install tk

rem --- Start InfluxDB and Grafana in separate windows ---
start "" "%SCRIPT_DIR%influxdb2-2.7.12-windows\influxd.exe"
copy /Y "%SCRIPT_DIR%grafana.ini" "%ProgramFiles%\GrafanaLabs\grafana\conf\grafana.ini"
start "" "%ProgramFiles%\GrafanaLabs\grafana\bin\grafana-server.exe" --config="%ProgramFiles%\GrafanaLabs\grafana\conf\grafana.ini" --homepath="%ProgramFiles%\GrafanaLabs\grafana"

rem --- Start Python script ---
echo Starting UDP Ingest...
start "UDP Ingest" python "%SCRIPT_DIR%main.py"

echo Starting User Terminal...
rem start "UserTerminal" python "%SCRIPT_DIR%UserTerminal.py"
explorer.exe "%SCRIPT_DIR%start_terminal.bat"

echo All processes started. Press any key to stop them...
pause

rem --- Cleanup routine ---
echo Stopping Python script...
taskkill /IM python.exe /F

echo Stopping Grafana...
taskkill /IM grafana-server.exe /F /T

echo Stopping InfluxDB...
taskkill /IM influxd.exe /F

echo Cleanup complete.
exit /b
