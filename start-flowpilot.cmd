@echo off
setlocal
cd /d "%~dp0"
where docker >nul 2>&1
if errorlevel 1 (
  echo Install Docker Desktop first: https://www.docker.com/products/docker-desktop/
  pause
  exit /b 1
)
docker info >nul 2>&1
if errorlevel 1 (
  echo Start Docker Desktop and try again.
  pause
  exit /b 1
)
if not exist ".env" copy ".env.example" ".env" >nul
echo Starting FlowPilot. The first run builds the Vue and Python application.
echo To use DeepSeek, edit .env locally and set AI_PROVIDER=deepseek and DEEPSEEK_API_KEY.
docker compose -p flowpilot-quick -f compose.quick.yml up --build -d --wait
if errorlevel 1 (
  echo Startup failed. Recent service logs:
  docker compose -p flowpilot-quick -f compose.quick.yml logs --tail=80
  pause
  exit /b 1
)
start "" "http://127.0.0.1:8000/console/"
echo FlowPilot is running. Use stop-flowpilot.cmd to stop it.
pause
