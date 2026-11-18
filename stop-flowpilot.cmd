@echo off
setlocal
cd /d "%~dp0"
docker compose -p flowpilot-quick -f compose.quick.yml down
if errorlevel 1 (
  pause
  exit /b 1
)
echo FlowPilot stopped. Data and model receipts were retained.
pause
