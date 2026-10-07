@echo off
chcp 65001 > nul
cd /d "%~dp0"
where ollama >nul 2>nul
if errorlevel 1 (
  echo Ollama가 필요합니다. https://ollama.com/download 에서 설치한 뒤 다시 실행하세요.
  pause
  exit /b 1
)
where python >nul 2>nul
if errorlevel 1 (
  echo Python 3.10 이상이 필요합니다.
  pause
  exit /b 1
)
set M1=%~1
set M2=%~2
if "%M1%"=="" set M1=gemma3:4b
if "%M2%"=="" set M2=exaone3.5:2.4b
if "%OLLAMA_MODELS%"=="" echo 참고: 모델 파일은 C 드라이브 사용자 폴더(.ollama)에 저장됩니다. 다른 드라이브를 쓰려면 README의 OLLAMA_MODELS 안내를 보세요.
echo 모델 2종으로 주요 기능을 검증합니다: %M1%, %M2%
echo 처음에는 모델을 내려받습니다. 모델마다 수 GB이며 시간이 걸립니다.
ollama pull %M1%
if errorlevel 1 goto fail
ollama pull %M2%
if errorlevel 1 goto fail
if not exist .venv python -m venv .venv
call .venv\Scripts\activate.bat
python -m pip install -q --disable-pip-version-check -r requirements.txt
python tools\verify_models.py --base-url http://localhost:11434/v1 --models %M1% %M2%
echo.
echo 결과 보고서: %CD%\verify_report.md
pause
exit /b 0
:fail
echo 모델을 내려받지 못했습니다. 모델 이름과 인터넷 연결을 확인하세요.
pause
exit /b 1
