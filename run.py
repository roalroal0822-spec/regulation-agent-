"""실행: python run.py  (브라우저가 자동으로 열립니다)"""
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
for p in (HERE, HERE.parent, HERE.parent.parent):
    if (p / "core").is_dir():
        sys.path.insert(0, str(p))
        break
sys.path.insert(0, str(HERE))

from app import main  # noqa: E402

if __name__ == "__main__":
    main()
