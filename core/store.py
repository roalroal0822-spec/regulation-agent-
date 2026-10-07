"""작업 데이터 저장소 (JSON 파일). 데이터베이스 설치 없이 동작한다.

원본 자료와 분리된 data/ 폴더에만 쓴다.
"""
import json
import os
import tempfile
import threading
import time
from pathlib import Path

_lock = threading.Lock()


class Store:
    def __init__(self, root):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def path(self, name):
        p = (self.root / name).resolve()
        if self.root.resolve() not in p.parents and p != self.root.resolve():
            raise ValueError("잘못된 경로")
        return p

    def read(self, name, default=None):
        p = self.path(name)
        if not p.exists():
            return default
        return json.loads(p.read_text(encoding="utf-8"))

    def write(self, name, obj):
        p = self.path(name)
        p.parent.mkdir(parents=True, exist_ok=True)
        with _lock:
            fd, tmp = tempfile.mkstemp(dir=p.parent, suffix=".tmp")
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(obj, f, ensure_ascii=False, indent=1)
            os.replace(tmp, p)

    def append_log(self, name, entry):
        p = self.path(name)
        p.parent.mkdir(parents=True, exist_ok=True)
        entry = {"at": time.strftime("%Y-%m-%d %H:%M:%S"), **entry}
        with _lock, open(p, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        return entry

    def read_log(self, name):
        p = self.path(name)
        if not p.exists():
            return []
        return [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines() if l.strip()]

    def list(self, sub=""):
        d = self.path(sub) if sub else self.root
        if not d.exists():
            return []
        return sorted(x.name for x in d.iterdir())

    def remove_tree(self, sub):
        import shutil
        d = self.path(sub)
        if d.exists() and d != self.root.resolve():
            shutil.rmtree(d)
