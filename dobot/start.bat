@echo off
rem Dobot Magician ピック＆プレース 起動スクリプト（Windows）
rem   start.bat          設定が済んでいれば実演画面、まだならウィザード
rem   start.bat wizard   初期設定ウィザード
rem   start.bat demo     実演画面（config.json のポートとカメラで。ブラウザが開く）
rem   start.bat dry      実機なしの実演（動作確認）
rem   start.bat check    読み取りだけ（カメラ一覧・ポート候補・テスト）
setlocal
cd /d "%~dp0"
chcp 65001 >nul
set PYTHONIOENCODING=utf-8

where uv >nul 2>nul
if errorlevel 1 (
  echo uv が見つかりません。https://docs.astral.sh/uv/ の手順でインストールしてから再実行してください。
  exit /b 1
)
uv sync -q

set MODE=%1
if "%MODE%"=="" (
  uv run python -c "import json,os,sys;c=json.load(open('config.json',encoding='utf-8'));sys.exit(0 if c['robot'].get('port') and os.path.exists(c.get('homography_path','assets/homography.json')) else 1)" >nul 2>nul
  if errorlevel 1 (set MODE=wizard) else (set MODE=demo)
  echo → %MODE% を起動します（config.json のポートと較正の有無で判断）
)

if "%MODE%"=="wizard" uv run python setup_wizard.py & goto :eof
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
echo 使い方: start.bat [wizard^|demo^|dry^|check]
exit /b 2
