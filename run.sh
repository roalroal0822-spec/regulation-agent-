#!/usr/bin/env bash
cd "$(dirname "$0")"
PY=${PYTHON:-python3}
if [ ! -d .venv ]; then
  echo "처음 실행: 실행 환경을 준비합니다..."
  $PY -m venv .venv || { echo "Python 3.10 이상이 필요합니다"; exit 1; }
fi
. .venv/bin/activate
python -m pip install -q --disable-pip-version-check -r requirements.txt
python run.py "$@"
