"""여러 형식의 문서를 읽어 '블록(위치가 붙은 문장 단위)'으로 바꾼다.

지원 형식: 한글(.hwpx, .hwp), PDF, 엑셀(.xlsx/.xlsm), 워드(.docx), 파워포인트(.pptx), 메일(.eml), 텍스트(.txt/.md/.csv)
원본 파일은 읽기만 하고 절대 수정·이동·삭제하지 않는다.
"""
import csv
import email
import email.policy
import hashlib
import io
import re
import struct
import zipfile
import zlib
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field, asdict
from pathlib import Path

SUPPORTED = {".hwpx", ".hwp", ".pdf", ".xlsx", ".xlsm", ".docx", ".pptx", ".eml", ".txt", ".md", ".csv"}


def skip_reason(ext):
    if ext.lower() == ".ppt":
        return "파워포인트 97~2003 형식(.ppt)은 읽지 못합니다. PowerPoint에서 .pptx로 저장해 넣어 주세요"
    return "지원하지 않는 형식"


@dataclass
class Block:
    """원문의 한 조각(문단·표 행·페이지 줄). 출처 표시의 최소 단위."""
    id: str
    doc_id: str
    file: str
    loc: str
    text: str

    def cite(self, quote=None):
        q = quote if quote is not None else self.text
        if len(q) > 160:
            q = q[:157] + "..."
        return {"block_id": self.id, "file": self.file, "loc": self.loc, "quote": q}


@dataclass
class Document:
    id: str
    name: str
    relpath: str
    ext: str
    blocks: list = field(default_factory=list)
    tables: list = field(default_factory=list)  # [{"name":..., "header":[...], "rows":[[...]], "loc_prefix":...}]
    meta: dict = field(default_factory=dict)
    error: str = ""

    @property
    def text(self):
        return "\n".join(b.text for b in self.blocks)

    def to_dict(self, with_blocks=False):
        d = {
            "id": self.id, "name": self.name, "relpath": self.relpath, "ext": self.ext,
            "meta": self.meta, "error": self.error, "n_blocks": len(self.blocks),
            "chars": sum(len(b.text) for b in self.blocks),
        }
        if with_blocks:
            d["blocks"] = [asdict(b) for b in self.blocks]
        return d


def _doc_id(relpath):
    return "d" + hashlib.md5(relpath.encode("utf-8")).hexdigest()[:8]


def _decode(data):
    for enc in ("utf-8-sig", "cp949", "euc-kr", "utf-16"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def _clean(s):
    s = s.replace(" ", " ").replace("\t", " ")
    s = re.sub(r"[ 　]+", " ", s)
    return s.strip()


def _local(tag):
    return tag.rsplit("}", 1)[-1]


# ---------------------------------------------------------------- 형식별 읽기

def _read_text(data, name):
    out = []
    for i, line in enumerate(_decode(data).splitlines(), 1):
        t = _clean(line)
        if t:
            out.append((f"{i}행", t))
    return out, []


def _read_csv(data, name):
    text = _decode(data)
    rows = [r for r in csv.reader(io.StringIO(text))]
    rows = [[_clean(c) for c in r] for r in rows if any(c.strip() for c in r)]
    if not rows:
        return [], []
    header = rows[0]
    out = []
    for i, r in enumerate(rows[1:], 2):
        out.append((f"{i}행", _row_text(header, r)))
    return out, [{"name": Path(name).stem, "header": header, "rows": rows[1:], "loc_prefix": "",
                  "row_locs": [f"{i}행" for i in range(2, len(rows) + 1)]}]


def _row_text(header, row):
    parts = []
    for i, v in enumerate(row):
        if v in (None, ""):
            continue
        h = header[i] if i < len(header) and header[i] else ""
        parts.append(f"{h}: {v}" if h else str(v))
    return " / ".join(parts)


def _xml_paragraphs(root, p_tag, t_tag, tbl_tag, tr_tag, tc_tag):
    """문서 순서대로 (종류, 텍스트) 를 낸다. 표는 행 단위로 '셀 | 셀' 로 합친다."""
    def para_text(p):
        return "".join(t.text or "" for t in p.iter() if _local(t.tag) == t_tag)

    def walk(node):
        for child in node:
            tag = _local(child.tag)
            if tag == tbl_tag:
                rows = []
                for tr in child.iter():
                    if _local(tr.tag) != tr_tag:
                        continue
                    cells = []
                    for tc in tr:
                        if _local(tc.tag) != tc_tag:
                            continue
                        cells.append(" / ".join(t for t in (
                            _clean(para_text(p)) for p in tc.iter() if _local(p.tag) == p_tag) if t))
                    rows.append(cells)
                yield ("table", rows)
            elif tag == p_tag:
                # 문단 안에 표가 들어있는 경우(한글) 표를 먼저 처리
                inner_tbls = top_tables(child)
                if inner_tbls:
                    own = []
                    for run in child:
                        for t in run:
                            if _local(t.tag) == t_tag and t.text:
                                own.append(t.text)
                    if _clean("".join(own)):
                        yield ("p", _clean("".join(own)))
                    for tbl in inner_tbls:
                        yield from walk_tbl(tbl)
                else:
                    t = _clean(para_text(child))
                    if t:
                        yield ("p", t)
            else:
                yield from walk(child)

    def top_tables(node):
        found = []
        for c in node:
            if _local(c.tag) == tbl_tag:
                found.append(c)
            else:
                found.extend(top_tables(c))
        return found

    def walk_tbl(tbl):
        wrapper = ET.Element("w")
        wrapper.append(tbl)
        yield from walk(wrapper)

    yield from walk(root)


def _emit_xml(items, tables, tname):
    out = []
    pi = ti = 0
    for kind, val in items:
        if kind == "p":
            pi += 1
            out.append((f"문단 {pi}", val))
        else:
            ti += 1
            rows = [r for r in val if any(r)]
            if not rows:
                continue
            header = rows[0]
            tables.append({"name": f"{tname} 표{ti}", "header": header, "rows": rows[1:],
                           "loc_prefix": f"표{ti} ",
                           "row_locs": [f"표{ti} {ri}행" for ri in range(2, len(rows) + 1)]})
            for ri, r in enumerate(rows, 1):
                txt = " | ".join(c for c in r if c)
                if txt:
                    out.append((f"표{ti} {ri}행", txt))
    return out


def _read_hwpx(data, name):
    z = zipfile.ZipFile(io.BytesIO(data))
    secs = sorted([n for n in z.namelist() if re.match(r"Contents/section\d+\.xml", n)],
                  key=lambda n: int(re.findall(r"\d+", n)[-1]))
    out, tables = [], []
    for s in secs:
        root = ET.fromstring(z.read(s))
        items = list(_xml_paragraphs(root, "p", "t", "tbl", "tr", "tc"))
        out.extend(_emit_xml(items, tables, Path(name).stem))
    return out, tables


def _read_docx(data, name):
    z = zipfile.ZipFile(io.BytesIO(data))
    root = ET.fromstring(z.read("word/document.xml"))
    tables = []
    items = list(_xml_paragraphs(root, "p", "t", "tbl", "tr", "tc"))
    return _emit_xml(items, tables, Path(name).stem), tables


def _pptx_slides(z):
    """발표 순서대로 슬라이드 파일 경로를 낸다(presentation.xml의 sldIdLst 순서)."""
    names = set(z.namelist())
    try:
        rels = ET.fromstring(z.read("ppt/_rels/presentation.xml.rels"))
        target = {r.get("Id"): "ppt/" + r.get("Target").lstrip("/").removeprefix("ppt/") for r in rels}
        pres = ET.fromstring(z.read("ppt/presentation.xml"))
        order = [target.get(e.get("{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id"))
                 for e in pres.iter() if _local(e.tag) == "sldId"]
        order = [o for o in order if o in names]
        if order:
            return order
    except (KeyError, ET.ParseError):
        pass
    return sorted((n for n in names if re.match(r"ppt/slides/slide\d+\.xml$", n)),
                  key=lambda n: int(re.findall(r"\d+", n)[-1]))


def _read_pptx(data, name):
    z = zipfile.ZipFile(io.BytesIO(data))
    names = set(z.namelist())
    out, tables = [], []
    for si, path in enumerate(_pptx_slides(z), 1):
        sub = []
        items = list(_xml_paragraphs(ET.fromstring(z.read(path)), "p", "t", "tbl", "tr", "tc"))
        for loc, text in _emit_xml(items, sub, f"{Path(name).stem} 슬라이드{si}"):
            out.append((f"슬라이드 {si} {loc}", text))
        for t in sub:
            t["loc_prefix"] = f"슬라이드 {si} " + t["loc_prefix"]
            t["row_locs"] = [f"슬라이드 {si} " + x for x in t["row_locs"]]
        tables.extend(sub)
        # 발표자 메모: 슬라이드 rels에서 notesSlide를 찾는다
        rel = path.replace("slides/", "slides/_rels/") + ".rels"
        if rel in names:
            for r in ET.fromstring(z.read(rel)):
                if r.get("Type", "").endswith("/notesSlide"):
                    npath = "ppt/" + r.get("Target").replace("../", "")
                    if npath in names:
                        ni = 0
                        for e in ET.fromstring(z.read(npath)).iter():
                            if _local(e.tag) == "p":
                                t = _clean("".join(x.text or "" for x in e.iter() if _local(x.tag) == "t"))
                                if t and not t.isdigit():  # 쪽 번호 자리 제외
                                    ni += 1
                                    out.append((f"슬라이드 {si} 메모 {ni}", t))
    return out, tables


# HWP 5.0 바이너리: 레코드 구조에서 문단 텍스트(HWPTAG_PARA_TEXT=67)만 꺼낸다.
_HWP_CHAR_CTRL = {0, 10, 13, 24, 25, 26, 27, 28, 29, 30, 31}


def _hwp_para_text(payload):
    chars = []
    i = 0
    n = len(payload) // 2
    while i < n:
        c = struct.unpack_from("<H", payload, i * 2)[0]
        if c < 32:
            if c in _HWP_CHAR_CTRL:
                if c in (10, 13):
                    chars.append("\n")
                i += 1
            else:
                i += 8  # 인라인/확장 컨트롤은 8 WCHAR 차지
                if c == 9:
                    chars.append(" ")
            continue
        chars.append(chr(c))
        i += 1
    return "".join(chars)


def _read_hwp(data, name):
    import olefile
    ole = olefile.OleFileIO(io.BytesIO(data))
    header = ole.openstream("FileHeader").read()
    flags = struct.unpack_from("<I", header, 36)[0]
    compressed = bool(flags & 1)
    if flags & 2:
        raise ValueError("암호가 걸린 한글 파일은 읽을 수 없습니다")
    secs = sorted([e for e in ole.listdir() if len(e) == 2 and e[0] == "BodyText"],
                  key=lambda e: int(re.findall(r"\d+", e[1])[0]))
    out = []
    pi = 0
    for e in secs:
        raw = ole.openstream(e).read()
        if compressed:
            raw = zlib.decompress(raw, -15)
        pos = 0
        while pos + 4 <= len(raw):
            h = struct.unpack_from("<I", raw, pos)[0]
            tag = h & 0x3FF
            size = (h >> 20) & 0xFFF
            pos += 4
            if size == 0xFFF:
                size = struct.unpack_from("<I", raw, pos)[0]
                pos += 4
            if tag == 67:
                for line in _hwp_para_text(raw[pos:pos + size]).split("\n"):
                    t = _clean(line)
                    if t:
                        pi += 1
                        out.append((f"문단 {pi}", t))
            pos += size
    return out, []


def _read_pdf(data, name):
    from pypdf import PdfReader
    r = PdfReader(io.BytesIO(data))
    out = []
    for pno, page in enumerate(r.pages, 1):
        text = page.extract_text() or ""
        for li, line in enumerate(text.splitlines(), 1):
            t = _clean(line)
            if t:
                out.append((f"p.{pno} {li}줄", t))
    return out, []


def _read_xlsx(data, name):
    import openpyxl
    wb = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    out, tables = [], []
    for ws in wb.worksheets:
        rows = []
        for r in ws.iter_rows(values_only=True):
            cells = ["" if v is None else _clean(str(v)) for v in r]
            while cells and cells[-1] == "":
                cells.pop()
            rows.append(cells)
        # 앞쪽 빈 행 제외, 첫 비어있지 않은 행을 머리행으로 본다
        idx = [i for i, r in enumerate(rows) if any(r)]
        if not idx:
            continue
        # 제목 행(셀 1개)은 건너뛰고, 값이 충분히 찬 첫 행을 머리행으로 본다
        widest = max(sum(1 for c in rows[i] if c) for i in idx)
        hi = next(i for i in idx if sum(1 for c in rows[i] if c) >= max(2, widest // 2))
        for i in idx:
            if i < hi:
                out.append((f"{ws.title}!{i + 1}행", " ".join(c for c in rows[i] if c)))
        header = rows[hi]
        body = [(i + 1, rows[i]) for i in idx if i > hi]
        tables.append({"name": ws.title, "header": header, "rows": [r for _, r in body],
                       "loc_prefix": f"{ws.title}!", "row_locs": [f"{ws.title}!{rn}행" for rn, _ in body]})
        out.append((f"{ws.title}!{hi + 1}행", " | ".join(c for c in header if c)))
        for rn, r in body:
            out.append((f"{ws.title}!{rn}행", _row_text(header, r)))
    return out, tables


def _strip_html(s):
    s = re.sub(r"(?is)<(script|style).*?</\1>", " ", s)
    s = re.sub(r"(?i)<br\s*/?>|</p>|</div>|</tr>", "\n", s)
    s = re.sub(r"<[^>]+>", " ", s)
    return s


def _read_eml(data, name):
    msg = email.message_from_bytes(data, policy=email.policy.default)
    meta = {k: str(msg.get(k, "")) for k in ("From", "To", "Cc", "Date", "Subject")}
    body = msg.get_body(preferencelist=("plain", "html"))
    text = ""
    if body is not None:
        text = body.get_content()
        if body.get_content_type() == "text/html":
            text = _strip_html(text)
    attachments = [p.get_filename() for p in msg.iter_attachments() if p.get_filename()]
    meta["attachments"] = attachments
    out = []
    for k in ("Subject", "From", "To", "Date"):
        if meta.get(k):
            out.append((f"머리글 {k}", f"{k}: {meta[k]}"))
    for i, line in enumerate(text.splitlines(), 1):
        t = _clean(line)
        if t and not t.startswith(">"):
            out.append((f"본문 {i}행", t))
    return out, [], meta


READERS = {
    ".txt": _read_text, ".md": _read_text, ".csv": _read_csv,
    ".hwpx": _read_hwpx, ".hwp": _read_hwp, ".docx": _read_docx, ".pptx": _read_pptx,
    ".pdf": _read_pdf, ".xlsx": _read_xlsx, ".xlsm": _read_xlsx,
}


def load_bytes(data, relpath):
    """파일 내용(bytes)과 상대경로로 Document를 만든다."""
    relpath = relpath.replace("\\", "/")
    ext = Path(relpath).suffix.lower()
    did = _doc_id(relpath)
    doc = Document(id=did, name=Path(relpath).name, relpath=relpath, ext=ext)
    try:
        if ext == ".eml":
            items, tables, meta = _read_eml(data, relpath)
            doc.meta.update(meta)
        elif ext in READERS:
            items, tables = READERS[ext](data, relpath)
        else:
            doc.error = "지원하지 않는 형식"
            return doc
    except Exception as e:  # 한 파일이 깨져도 나머지는 계속 읽는다
        doc.error = f"읽기 실패: {e}"
        return doc
    doc.tables = tables
    for i, (loc, text) in enumerate(items):
        doc.blocks.append(Block(id=f"{did}-{i}", doc_id=did, file=relpath, loc=loc, text=text))
    return doc


def iter_folder(folder):
    """폴더 안 파일을 (상대경로, 절대경로)로 낸다. 숨김·임시 파일은 건너뛴다."""
    folder = Path(folder)
    for p in sorted(folder.rglob("*")):
        if not p.is_file():
            continue
        rel = p.relative_to(folder).as_posix()
        if any(part.startswith((".", "~$")) for part in p.relative_to(folder).parts):
            continue
        yield rel, p


def load_folder(folder):
    docs, skipped = [], []
    for rel, p in iter_folder(folder):
        if p.suffix.lower() not in SUPPORTED:
            skipped.append({"file": rel, "reason": skip_reason(p.suffix)})
            continue
        with open(p, "rb") as f:  # 읽기 전용으로만 연다
            data = f.read()
        doc = load_bytes(data, rel)
        if doc.error:
            skipped.append({"file": rel, "reason": doc.error})
        docs.append(doc)
    return docs, skipped
