@echo off
chcp 65001 > nul
cd /d "%~dp0"
rem 실제 동작하는 Python 3.10 이상을 찾는다 (Microsoft Store 바로가기 python.exe는 건너뜀)
set PY=
python -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>nul
if not errorlevel 1 set PY=python
if not defined PY (
  py -3 -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>nul
  if not errorlevel 1 set PY=py -3
)
if not defined PY (
  echo Python 3.10 이상을 찾지 못했습니다.
  echo https://www.python.org/downloads/ 에서 설치하고, 설치 첫 화면에서 "Add python.exe to PATH"를 체크하세요.
  pause
  exit /b 1
)
if not exist .venv\Scripts\python.exe (
  echo 처음 실행: 실행 환경을 준비합니다...
  %PY% -m venv .venv
  if errorlevel 1 (
    echo 실행 환경을 만들지 못했습니다.
    pause
    exit /b 1
  )
)
call .venv\Scripts\activate.bat
rem 인터넷이 막힌 PC: 같은 폴더의 wheels\ 에 미리 받아 둔 설치 파일이 있으면 그것으로 설치
if exist wheels (
  python -m pip install -q --disable-pip-version-check --no-index --find-links wheels -r requirements.txt
) else (
  python -m pip install -q --disable-pip-version-check -r requirements.txt
)
if errorlevel 1 (
  echo 주의: 라이브러리 설치에 실패했습니다. 인터넷 연결을 확인하세요. 한글·PDF·엑셀 파일 읽기가 동작하지 않을 수 있습니다.
  echo 인터넷이 막힌 PC라면 README의 '인터넷이 막힌 PC' 안내를 보세요.
)
python run.py
pause
