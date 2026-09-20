@echo off
rem Dobot Magician pick-and-place launcher (Windows). ASCII only: cmd.exe reads batch files as the OEM code page.
rem   start.bat          wizard if not configured yet, otherwise the demo panel (browser opens)
rem   start.bat wizard   first-time setup wizard
rem   start.bat demo     demo panel using config.json (port, camera)
rem   start.bat dry      demo panel without hardware (synthetic camera)
rem   start.bat check    read-only: camera list, candidate ports, offline tests
setlocal
cd /d "%~dp0"
set PYTHONIOENCODING=utf-8

where uv >nul 2>nul
if errorlevel 1 (
  echo uv not found. Install it from https://docs.astral.sh/uv/ and run this script again.
  exit /b 1
)
uv sync -q

set MODE=%1
if "%MODE%"=="" (
  uv run python -c "import json,os,sys;c=json.load(open('config.json',encoding='utf-8'));sys.exit(0 if c['robot'].get('port') and os.path.exists(c.get('homography_path','assets/homography.json')) else 1)" >nul 2>nul
  if errorlevel 1 (set MODE=wizard) else (set MODE=demo)
  echo Starting %MODE% ^(decided from config.json: port and calibration^)
)

if "%MODE%"=="wizard" (
  uv run python setup_wizard.py
  goto :eof
)
if "%MODE%"=="demo" (
  start "" http://127.0.0.1:8790
  uv run python demo.py --config config.json --robot pydobot
  goto :eof
)
if "%MODE%"=="dry" (
  start "" http://127.0.0.1:8790
  uv run python demo.py --dry-run --no-llm
  goto :eof
)
if "%MODE%"=="check" (
  uv run python check_camera.py --list
  uv run python check_robot.py --list
  uv run pytest -q
  goto :eof
)
echo usage: start.bat [wizard^|demo^|dry^|check]
exit /b 2
