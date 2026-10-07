"""규정·지침·법령 텍스트를 장/조/항/호/목 구조로 읽고, 다시 글로 쓰고, 비교한다.

서무비서(절차 추출)와 규정 제·개정 에이전트(조문 편집·신구대비)가 함께 쓴다.
"""
import copy
import difflib
import re
from dataclasses import dataclass, field

CIRCLED = "①②③④⑤⑥⑦⑧⑨⑩⑪⑫⑬⑭⑮⑯⑰⑱⑲⑳"
GANADA = "가나다라마바사아자차카타파하"

RE_CHAPTER = re.compile(r"^제\s*(\d+)\s*장\s*(.*)$")
RE_SECTION = re.compile(r"^제\s*(\d+)\s*절\s*(.*)$")
RE_ARTICLE = re.compile(r"^제\s*(\d+)\s*조(?:\s*의\s*(\d+))?\s*(?:\(([^)]*)\))?\s*(.*)$")
RE_PARA = re.compile(r"^([①-⑳])\s*(.*)$")
RE_ITEM = re.compile(r"^(\d+)(?:의(\d+))?\.\s*(.*)$")
RE_SUBITEM = re.compile(r"^([가-하])\.\s*(.*)$")
RE_ADDENDA = re.compile(r"^부\s*칙\s*(.*)$")
RE_APPENDIX = re.compile(r"^\[?(별표|별지)\s*(?:제?\s*(\d+)\s*호)?")


def akey(no, sub=0):
    return f"{no}의{sub}" if sub else str(no)


def alabel(no, sub=0):
    return f"제{no}조의{sub}" if sub else f"제{no}조"


def parse_key(key):
    m = re.match(r"^(\d+)(?:의(\d+))?$", str(key))
    if not m:
        raise ValueError(f"조 번호 형식 오류: {key}")
    return int(m.group(1)), int(m.group(2) or 0)


@dataclass
class Item:
    no: str
    text: str
    subitems: list = field(default_factory=list)  # [(가, text)]


@dataclass
class Para:
    no: int  # 0 이면 항 번호 없는 본문
    text: str
    items: list = field(default_factory=list)


@dataclass
class Article:
    no: int
    sub: int = 0
    title: str = ""
    chapter: str = ""
    paras: list = field(default_factory=list)
    deleted: bool = False
    loc: str = ""  # 원문 위치(출처 표시용)

    @property
    def key(self):
        return akey(self.no, self.sub)

    @property
    def label(self):
        return alabel(self.no, self.sub)

    @property
    def header(self):
        return f"{self.label}({self.title})" if self.title else self.label

    def body_lines(self):
        lines = []
        for p in self.paras:
            prefix = CIRCLED[p.no - 1] + " " if p.no else ""
            lines.append(prefix + p.text)
            for it in p.items:
                lines.append(f"  {it.no}. {it.text}")
                for s_no, s_text in it.subitems:
                    lines.append(f"    {s_no}. {s_text}")
        return lines

    def to_text(self):
        if self.deleted:
            return f"{self.label} 삭제"
        lines = self.body_lines()
        if not lines:
            return self.header
        return "\n".join([f"{self.header} {lines[0].strip()}"] + lines[1:])

    @property
    def plain(self):
        return " ".join(l.strip() for l in self.body_lines())


@dataclass
class Regulation:
    title: str = ""
    preamble: list = field(default_factory=list)
    articles: list = field(default_factory=list)
    addenda: list = field(default_factory=list)  # 부칙 원문 줄
    appendices: list = field(default_factory=list)  # 별표/별지 줄
    source: str = ""

    def get(self, key):
        key = str(key).replace("제", "").replace("조", "").replace(" ", "")
        for a in self.articles:
            if a.key == key:
                return a
        return None

    def index_of(self, key):
        for i, a in enumerate(self.articles):
            if a.key == key:
                return i
        return -1

    def to_text(self):
        out = []
        if self.title:
            out += [self.title, ""]
        out += self.preamble
        chapter = None
        for a in self.articles:
            if a.chapter and a.chapter != chapter:
                out += ["", a.chapter]
                chapter = a.chapter
            out.append(a.to_text())
        if self.addenda:
            out += [""] + self.addenda
        if self.appendices:
            out += [""] + self.appendices
        return "\n".join(out).strip() + "\n"

    def chapters(self):
        seen = []
        for a in self.articles:
            if a.chapter and a.chapter not in seen:
                seen.append(a.chapter)
        return seen


def parse(lines, title=None, source=""):
    """lines: 문자열 목록 또는 (loc, text) 목록."""
    reg = Regulation(source=source)
    cur_art = None
    cur_para = None
    cur_item = None
    chapter = ""
    mode = "body"
    norm = []
    for x in lines:
        loc, text = (x if isinstance(x, tuple) else ("", x))
        for t in str(text).split("\n"):
            t = t.strip()
            if t:
                norm.append((loc, t))
    for loc, t in norm:
        if mode == "addenda":
            if RE_APPENDIX.match(t):
                mode = "appendix"
                reg.appendices.append(t)
            else:
                reg.addenda.append(t)
            continue
        if mode == "appendix":
            reg.appendices.append(t)
            continue
        if RE_ADDENDA.match(t) and len(t) < 40:
            mode = "addenda"
            reg.addenda.append(t)
            continue
        m = RE_CHAPTER.match(t)
        if m and len(t) < 40:
            chapter = f"제{m.group(1)}장 {m.group(2).strip()}".strip()
            continue
        if RE_SECTION.match(t) and len(t) < 40:
            continue
        m = RE_ARTICLE.match(t)
        if m and (m.group(3) is not None or m.group(4).startswith(("①", "삭제")) or not m.group(4)):
            no, sub = int(m.group(1)), int(m.group(2) or 0)
            cur_art = Article(no=no, sub=sub, title=(m.group(3) or "").strip(), chapter=chapter, loc=loc)
            reg.articles.append(cur_art)
            cur_para = cur_item = None
            rest = m.group(4).strip()
            if rest.startswith("삭제") or (m.group(3) or "").strip() == "삭제":
                cur_art.deleted = True
                continue
            if rest:
                pm = RE_PARA.match(rest)
                if pm:
                    cur_para = Para(no=CIRCLED.index(pm.group(1)) + 1, text=pm.group(2).strip())
                else:
                    cur_para = Para(no=0, text=rest)
                cur_art.paras.append(cur_para)
            continue
        if cur_art is None:
            if not reg.title and title is None:
                reg.title = t
            else:
                reg.preamble.append(t)
            continue
        pm = RE_PARA.match(t)
        if pm:
            cur_para = Para(no=CIRCLED.index(pm.group(1)) + 1, text=pm.group(2).strip())
            cur_art.paras.append(cur_para)
            cur_item = None
            continue
        im = RE_ITEM.match(t)
        if im:
            if cur_para is None:
                cur_para = Para(no=0, text="")
                cur_art.paras.append(cur_para)
            no = im.group(1) + (f"의{im.group(2)}" if im.group(2) else "")
            cur_item = Item(no=no, text=im.group(3).strip())
            cur_para.items.append(cur_item)
            continue
        sm = RE_SUBITEM.match(t)
        if sm and cur_item is not None:
            cur_item.subitems.append((sm.group(1), sm.group(2).strip()))
            continue
        # 이어지는 줄
        if cur_item is not None:
            if cur_item.subitems:
                n, s = cur_item.subitems[-1]
                cur_item.subitems[-1] = (n, s + " " + t)
            else:
                cur_item.text += " " + t
        elif cur_para is not None:
            cur_para.text = (cur_para.text + " " + t).strip()
        else:
            cur_para = Para(no=0, text=t)
            cur_art.paras.append(cur_para)
    if title:
        reg.title = title
    return reg


def parse_text(text, title=None, source=""):
    return parse(text.splitlines(), title=title, source=source)


# ---------------------------------------------------------------- 인용(참조) 탐지

RE_REF = re.compile(r"(「[^」]{1,60}」\s*)?제(\d+)조(?:의(\d+))?((?:제\d+항)?(?:제\d+호)?(?:[가-하]목)?)")


def find_refs(text):
    """조문 인용을 찾는다. 반환: [{"key","external","law","span","raw"}]"""
    out = []
    for m in RE_REF.finditer(text):
        law = (m.group(1) or "").strip().strip("「」 ")
        out.append({
            "key": akey(int(m.group(2)), int(m.group(3) or 0)),
            "detail": m.group(4) or "",
            "external": bool(law),
            "law": law,
            "span": (m.start(2) - 1, m.end()),
            "raw": m.group(0),
        })
    return out


def internal_refs(reg):
    """규정 안에서 다른 조문을 인용하는 곳 목록."""
    res = []
    for a in reg.articles:
        for r in find_refs(a.plain):
            if not r["external"]:
                res.append({"from": a.key, "from_label": a.label, "to": r["key"], "raw": r["raw"]})
    for i, line in enumerate(reg.addenda + reg.appendices):
        for r in find_refs(line):
            if not r["external"]:
                res.append({"from": "부칙/별표", "from_label": "부칙·별표", "to": r["key"], "raw": r["raw"], "line": line})
    return res


def replace_refs(text, mapping, law_name=None):
    """조 번호 이동표(mapping: 옛 key -> 새 key 또는 None(삭제))에 따라 인용을 고친다.

    law_name 이 주어지면 「law_name」 제n조 형태의 외부 인용만 고치고,
    없으면 「」 없는 내부 인용만 고친다.
    """
    changes = []

    def sub(m):
        law = (m.group(1) or "").strip().strip("「」 ")
        is_target = (law == law_name) if law_name else (not law)
        if not is_target:
            return m.group(0)
        old = akey(int(m.group(2)), int(m.group(3) or 0))
        if old not in mapping or mapping[old] == old:
            return m.group(0)
        new = mapping[old]
        if new is None:
            changes.append((old, None))
            return m.group(0)  # 삭제된 조문 인용은 사람이 확인하도록 그대로 두고 보고
        n_no, n_sub = parse_key(new)
        changes.append((old, new))
        return (m.group(1) or "") + alabel(n_no, n_sub) + (m.group(4) or "")

    return RE_REF.sub(sub, text), changes


# ---------------------------------------------------------------- 비교(신구대비)

def _tok(s):
    return re.findall(r"\s+|[가-힣]+|[A-Za-z]+|\d+|.", s)


def mark_diff(old, new):
    """두 문장의 다른 부분을 __밑줄__ 로 표시해 (old_marked, new_marked) 를 돌려준다."""
    a, b = _tok(old), _tok(new)
    sm = difflib.SequenceMatcher(a=a, b=b, autojunk=False)
    oa, nb = [], []
    for op, i1, i2, j1, j2 in sm.get_opcodes():
        sa, sb = "".join(a[i1:i2]), "".join(b[j1:j2])
        if op == "equal":
            oa.append(sa)
            nb.append(sb)
        else:
            oa.append(_wrap(sa))
            nb.append(_wrap(sb))
    return "".join(oa), "".join(nb)


def _wrap(seg):
    """밑줄 표시를 줄마다 따로 붙인다(줄바꿈을 넘는 표시는 화면·문서에서 깨짐)."""
    out = []
    for line in seg.split("\n"):
        core = line.strip()
        if core:
            lead = line[: len(line) - len(line.lstrip())]
            trail = line[len(line.rstrip()):]
            out.append(f"{lead}__{core}__{trail}")
        else:
            out.append(line)
    return "\n".join(out)


def _para_lines(a):
    """조문을 비교 단위(항 단위 줄 묶음)로 나눈다."""
    units = []
    for p in a.paras:
        prefix = CIRCLED[p.no - 1] + " " if p.no else ""
        lines = [prefix + p.text]
        for it in p.items:
            lines.append(f"  {it.no}. {it.text}")
            for s_no, s_text in it.subitems:
                lines.append(f"    {s_no}. {s_text}")
        units.append("\n".join(lines))
    return units


def compare(old_reg, new_reg, mapping=None):
    """신구조문대비표 행 목록을 만든다.

    mapping: 옛 조 key -> 새 조 key (조 번호 이동 시). 없으면 같은 key 끼리 비교.
    반환: [{"old": 현행(표시), "new": 개정안(표시), "kind": 변경|신설|삭제, "key_old", "key_new"}]
    """
    mapping = mapping or {}
    rows = []
    used_new = set()
    new_by_key = {a.key: a for a in new_reg.articles}
    for oa in old_reg.articles:
        nk = mapping.get(oa.key, oa.key)
        na = new_by_key.get(nk) if nk else None
        if na is None or (na.deleted and not oa.deleted):
            rows.append({"kind": "삭제", "key_old": oa.key, "key_new": nk,
                         "old": oa.to_text(), "new": f"{na.label} 삭제" if na else "<삭 제>"})
            if na:
                used_new.add(na.key)
            continue
        used_new.add(na.key)
        if oa.to_text() == na.to_text():
            continue
        # 머리(조 번호·제목) 비교
        oh, nh = mark_diff(oa.header, na.header) if oa.header != na.header else (oa.header, na.header)
        ou, nu = _para_lines(oa), _para_lines(na)
        sm = difflib.SequenceMatcher(a=ou, b=nu, autojunk=False)
        old_parts, new_parts = [oh], [nh]
        for op, i1, i2, j1, j2 in sm.get_opcodes():
            if op == "equal":
                old_parts += ["(생 략)"] * (1 if i2 > i1 else 0)
                new_parts += ["(현행과 같음)"] * (1 if j2 > j1 else 0)
            elif op == "replace" and (i2 - i1) == (j2 - j1):
                for x, y in zip(ou[i1:i2], nu[j1:j2]):
                    mx, my = mark_diff(x, y)
                    old_parts.append(mx)
                    new_parts.append(my)
            else:
                for x in ou[i1:i2]:
                    old_parts.append(_wrap(x))
                if i2 == i1:
                    old_parts.append("<신 설>")
                for y in nu[j1:j2]:
                    new_parts.append(_wrap(y))
                if j2 == j1:
                    new_parts.append("<삭 제>")
        rows.append({"kind": "변경", "key_old": oa.key, "key_new": na.key,
                     "old": "\n".join(_squash(old_parts)), "new": "\n".join(_squash(new_parts))})
    for na in new_reg.articles:
        if na.key in used_new:
            continue
        rows.append({"kind": "신설", "key_old": None, "key_new": na.key,
                     "old": "<신 설>", "new": _wrap(na.to_text())})
    # 조 순서대로 정렬
    order = {a.key: i for i, a in enumerate(new_reg.articles)}
    rows.sort(key=lambda r: order.get(r["key_new"], 10_000 + old_reg.index_of(r["key_old"] or "")))
    if old_reg.addenda != new_reg.addenda and new_reg.addenda:
        if old_reg.addenda:
            o, n = mark_diff("\n".join(old_reg.addenda), "\n".join(new_reg.addenda))
        else:
            o, n = "<신 설>", _wrap("\n".join(new_reg.addenda))
        rows.append({"kind": "부칙", "key_old": None, "key_new": None, "old": o, "new": n})
    return rows


def _squash(parts):
    out = []
    for p in parts:
        if out and p in ("(생 략)", "(현행과 같음)") and out[-1] == p:
            continue
        out.append(p)
    return out


def clone(reg):
    return copy.deepcopy(reg)
