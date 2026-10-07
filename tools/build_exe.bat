@echo off
rem Windows 실행 파일(폴더형) 만들기 - Windows PC에서 실행하세요.
rem 결과: dist\regulation-agent\regulation-agent.exe (폴더째 복사해 쓰고, 작업 데이터는 그 폴더 안 data\ 에 저장)
chcp 65001 > nul
cd /d "%~dp0\.."
if not exist .venv python -m venv .venv
call .venv\Scripts\activate.bat
python -m pip install -q -r requirements.txt pyinstaller
pyinstaller --noconfirm --onedir --name regulation-agent --add-data "static;static" --add-data "core\static;core\static" --add-data "sample_data;sample_data" --collect-data hwpx run.py
echo 완료: dist\regulation-agent\regulation-agent.exe
pause
