"""자료를 가져오는 경로(소스). 앱은 소스 종류와 관계없이 Document 목록만 받는다.

- LocalFolderSource : PC의 폴더를 읽기 전용으로 읽는다(원본 그대로 둠)
- UploadSource      : 브라우저에서 올린 파일을 작업 폴더에 '사본'으로 저장해 읽는다
- DocSystemSource   : 문서관리시스템 API 연계를 가정한 구조(목록 → 본문 조회)
"""
import base64
import json
import urllib.parse
import urllib.request
from pathlib import Path

from .loaders import SUPPORTED, load_bytes, load_folder


class LocalFolderSource:
    kind = "local"

    def __init__(self, folder):
        self.folder = Path(folder).expanduser()
        if not self.folder.is_dir():
            raise FileNotFoundError(f"폴더를 찾을 수 없습니다: {self.folder}")

    def describe(self):
        return {"kind": self.kind, "path": str(self.folder)}

    def load(self):
        return load_folder(self.folder)


class UploadSource:
    kind = "upload"

    def __init__(self, files, dest):
        """files: [{"path": 상대경로, "b64": base64 내용}], dest: 사본을 저장할 폴더"""
        self.dest = Path(dest)
        self.dest.mkdir(parents=True, exist_ok=True)
        for f in files:
            rel = Path(f["path"].replace("\\", "/"))
            parts = [p for p in rel.parts if p not in ("..", "/", "")]
            target = self.dest.joinpath(*parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(base64.b64decode(f["b64"]))

    def describe(self):
        return {"kind": self.kind, "path": str(self.dest)}

    def load(self):
        return load_folder(self.dest)


class DocSystemSource:
    """문서시스템 API 연계 가정 구조.

    GET {base}/documents?owner=...        -> {"documents":[{"id","title","filename","created","category"}]}
    GET {base}/documents/{id}/content     -> {"filename","b64"}
    실제 연계 시 이 클래스만 기관 API 규격에 맞게 바꾸면 된다.
    """
    kind = "docsystem"

    def __init__(self, base_url, owner="", token_env=None):
        self.base = base_url.rstrip("/")
        self.owner = owner

    def describe(self):
        return {"kind": self.kind, "path": self.base, "owner": self.owner}

    def _get(self, url):
        with urllib.request.urlopen(url, timeout=30) as r:
            return json.loads(r.read().decode("utf-8"))

    def load(self):
        q = urllib.parse.urlencode({"owner": self.owner}) if self.owner else ""
        listing = self._get(f"{self.base}/documents?{q}")
        docs, skipped = [], []
        for meta in listing.get("documents", []):
            fn = meta.get("filename", "")
            if Path(fn).suffix.lower() not in SUPPORTED:
                skipped.append({"file": fn, "reason": "지원하지 않는 형식"})
                continue
            content = self._get(f"{self.base}/documents/{urllib.parse.quote(str(meta['id']))}/content")
            rel = f"{meta.get('category', '문서시스템')}/{fn}"
            doc = load_bytes(base64.b64decode(content["b64"]), rel)
            doc.meta.update({"docsystem_id": meta["id"], "title": meta.get("title", ""),
                             "created": meta.get("created", "")})
            if doc.error:
                skipped.append({"file": rel, "reason": doc.error})
            docs.append(doc)
        return docs, skipped
