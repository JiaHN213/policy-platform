@echo off
cd /d "%~dp0"
if not exist ".env.compose" (
  echo Missing .env.compose. Run: python scripts\prepare_compose.py
  pause
  exit /b 1
)
docker compose --env-file .env.compose -f compose.yaml -f compose.dev.yaml up -d --build
if errorlevel 1 (
  echo Startup failed. Please keep this window for troubleshooting.
) else (
  echo Ready: http://127.0.0.1:8080
)
pause
