@echo off
chcp 65001 > nul
cd /d "%~dp0"
where python >nul 2>nul
if errorlevel 1 (
  echo Python 3.10 이상이 필요합니다. https://www.python.org/downloads/ 에서 설치하세요. 설치할 때 "Add python.exe to PATH"를 체크하세요.
  pause
  exit /b 1
)
if not exist .venv (
  echo 처음 실행: 실행 환경을 준비합니다...
  python -m venv .venv
)
call .venv\Scripts\activate.bat
python -m pip install -q --disable-pip-version-check -r requirements.txt
python run.py
pause
