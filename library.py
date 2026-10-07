"""규정집: 기관 규정·지침, 상위 법령·자치법규, 서식을 읽어 조문 단위로 다룬다.

층 구조
- org   : 기관 규정·지침 (제·개정 대상)
- upper : 상위 법령·자치법규 (조례·규칙·법률 등, 위임 근거와 영향 분석 기준)
- forms : 규정에 딸린 서식 (조문 인용 탐지 대상)
원본 파일은 읽기만 하고, 앱은 작업 폴더(data/library)의 사본만 다룬다.
"""
import hashlib
import re
from pathlib import Path

from core import regparse
from core.loaders import load_bytes, iter_folder, SUPPORTED
from core.search import BM25

LAYERS = {"org": "기관 규정·지침", "upper": "상위 법령·자치법규", "forms": "서식"}
KINDS = ["규정", "지침", "세칙", "요령", "기준", "조례", "규칙", "시행령", "시행규칙", "법률", "법"]
SELF_WORD = {"규정": "규정", "지침": "지침", "세칙": "세칙", "요령": "요령", "기준": "기준", "조례": "조례",
             "규칙": "규칙", "시행령": "영", "시행규칙": "규칙", "법률": "법", "법": "법"}

RE_ALIAS = re.compile(r"「([^」]{1,60})」\s*(?:제\d+조(?:의\d+)?(?:제\d+항)?(?:제\d+호)?\s*)?\(\s*이하\s*[\"“”']([^\"“”']{1,15})[\"“”']\s*(?:이)?라\s*한다\s*\)")
RE_FORMREF = re.compile(r"별지\s*제?\s*(\d+)\s*호\s*서식")
RE_APPXREF = re.compile(r"별표\s*(\d+)")


def norm_name(name, org=""):
    n = re.sub(r"[「」\s·ㆍ]", "", name or "")
    if org:
        n = n.replace(re.sub(r"\s", "", org), "")
    return n


def kind_of(title):
    t = re.sub(r"\s", "", title or "")
    for k in ["시행규칙", "시행령", "조례", "규칙", "지침", "세칙", "요령", "기준", "규정", "법률", "법"]:
        if t.endswith(k):
            return k
    return "규정"


def doc_key(layer, rel):
    return layer + "-" + hashlib.md5(rel.encode("utf-8")).hexdigest()[:8]


class RefFinder:
    """조문 인용 찾기: 「규정명」 제n조, 약칭(이하 "조례"라 한다) 제n조, 같은 규정 제n조, 이 규정 제n조, 제n조."""

    def __init__(self, aliases=None):
        self.aliases = aliases or {}  # 약칭 -> 정식 이름
        alias_alt = "|".join(re.escape(a) for a in sorted(self.aliases, key=len, reverse=True))
        pre = r"(?:(?P<law>「[^」]{1,60}」)(?:\s*\(\s*이하[^)]{0,30}\))?|(?P<same>같은\s*(?:법|영|조례|규칙|규정|지침))|(?P<self>이\s*(?:법|영|조례|규칙|규정|지침|세칙)))"
        if alias_alt:
            pre = pre[:-1] + rf"|(?P<alias>(?<![가-힣])(?:{alias_alt})))"
        self.rx = re.compile(pre + r"?\s*제(?P<no>\d+)조(?:의(?P<sub>\d+))?(?P<detail>(?:\s*제\d+항)?(?:\s*제\d+호)?(?:\s*[가-하]목)?)")

    def find(self, text):
        out = []
        last_law = None
        for m in self.rx.finditer(text):
            law = None
            if m.group("law"):
                law = m.group("law").strip("「」 ")
                last_law = law
            elif m.groupdict().get("alias"):
                law = self.aliases[m.group("alias")]
                last_law = law
            elif m.group("same"):
                law = last_law
            if re.search(r"예\s*[:：)]\s*$|예시\s*[:：]?\s*$", text[max(0, m.start() - 6): m.start()]):
                continue  # "(예: 제5조의2)" 같은 보기는 인용이 아니다
            start = m.start("no") - 1
            out.append({
                "law": law, "key": regparse.akey(int(m.group("no")), int(m.group("sub") or 0)),
                "detail": re.sub(r"\s", "", m.group("detail") or ""), "raw": m.group(0).strip(),
                "start": m.start(), "end": m.end(), "num_start": start, "num_end": m.end("sub") if m.group("sub") else m.end("no") + 1,
            })
        return out


class Entry:
    """규정집의 문서 하나."""

    def __init__(self, layer, rel, doc, org_name=""):
        self.layer = layer
        self.rel = rel
        self.id = doc_key(layer, rel)
        self.doc = doc
        self.ext = Path(rel).suffix.lower()
        self.reg = None
        self.form_of = None  # 서식이면 소속 규정 이름
        self.form_no = None
        self.text_lines = [(b.loc, b.text) for b in doc.blocks]
        if layer == "forms":
            m = re.match(r"^\[([^\]]+)\]\s*별지\s*(\d+)\s*(.*)$", Path(rel).stem)
            if m:
                self.form_of, self.form_no, self.name = m.group(1).strip(), int(m.group(2)), m.group(3).strip()
            else:
                self.name = Path(rel).stem
            self.title = self.name
        else:
            self.reg = regparse.parse(self.text_lines, source=rel)
            self.title = self.reg.title or Path(rel).stem
            if not self.reg.articles:
                self.title = Path(rel).stem
        self.kind = kind_of(self.title) if layer != "forms" else "서식"
        self.short = self.title.replace(org_name, "").strip() if org_name else self.title
        self.aliases = {}
        full = "\n".join(t for _, t in self.text_lines)
        for m in RE_ALIAS.finditer(full):
            self.aliases[m.group(2)] = m.group(1)

    @property
    def self_word(self):
        return SELF_WORD.get(self.kind, "규정")

    def summary(self):
        d = {"id": self.id, "layer": self.layer, "rel": self.rel, "title": self.title, "short": self.short,
             "kind": self.kind, "ext": self.ext, "error": self.doc.error}
        if self.reg:
            d["n_articles"] = len(self.reg.articles)
            d["chapters"] = self.reg.chapters()
        if self.layer == "forms":
            d["form_of"], d["form_no"] = self.form_of, self.form_no
        return d

    def detail(self):
        d = self.summary()
        if self.reg:
            d["articles"] = [{"key": a.key, "label": a.label, "title": a.title, "chapter": a.chapter,
                              "text": a.to_text(), "deleted": a.deleted} for a in self.reg.articles]
            d["addenda"] = self.reg.addenda
            d["appendices"] = self.reg.appendices
            d["preamble"] = self.reg.preamble
        d["lines"] = [t for _, t in self.text_lines] if self.layer == "forms" or not (self.reg and self.reg.articles) else []
        return d


class Library:
    def __init__(self, root, org_name=""):
        self.root = Path(root)
        self.org_name = org_name
        self.entries = []

    def load(self):
        self.entries = []
        for layer in LAYERS:
            base = self.root / layer
            if not base.exists():
                continue
            for rel, p in iter_folder(base):
                if p.suffix.lower() not in SUPPORTED:
                    continue
                doc = load_bytes(p.read_bytes(), rel)
                self.entries.append(Entry(layer, rel, doc, self.org_name))
        return self

    # ------------------------------------------------------------ 찾기
    def get(self, eid):
        return next((e for e in self.entries if e.id == eid), None)

    def regs(self, layer=None):
        return [e for e in self.entries if e.reg and e.reg.articles and (layer is None or e.layer == layer)]

    def forms(self):
        return [e for e in self.entries if e.layer == "forms"]

    def by_name(self, name):
        """규정 이름(표기가 조금 달라도)으로 찾기."""
        n = norm_name(name, self.org_name)
        if not n:
            return None
        for e in self.regs():
            if n in (norm_name(e.title, self.org_name), norm_name(e.short, self.org_name)):
                return e
        return None

    def guess(self, text):
        """문장 속에서 언급한 규정을 찾는다(가장 긴 이름 우선)."""
        t = re.sub(r"\s", "", text)
        best = None
        for e in self.regs():
            for n in {norm_name(e.short, self.org_name), norm_name(e.title, self.org_name)}:
                if n and n in t and (best is None or len(n) > best[0]):
                    best = (len(n), e)
        return best[1] if best else None

    def aliases_for(self, entry):
        al = dict(entry.aliases)
        return al

    def refs_in(self, entry, text):
        return RefFinder(self.aliases_for(entry)).find(text)

    def units(self, entry):
        """인용을 찾을 단위 글: (위치 표시, 조 key 또는 None, 글)"""
        out = []
        if entry.reg and entry.reg.articles:
            for a in entry.reg.articles:
                out.append((a.label, a.key, "\n".join(a.body_lines())))
            if entry.reg.appendices:
                out.append(("별표", None, "\n".join(entry.reg.appendices)))
        else:
            for loc, t in entry.text_lines:
                out.append((loc, None, t))
        return out

    def citations_of(self, target, keys=None):
        """target 규정의 조문을 인용하는 다른 문서(규정·서식)의 위치 목록."""
        tnames = {norm_name(target.title, self.org_name), norm_name(target.short, self.org_name)}
        res = []
        for e in self.entries:
            if e is target:
                continue
            for loc, akey, text in self.units(e):
                for r in self.refs_in(e, text):
                    law = r["law"]
                    if law is None and e.layer == "forms" and e.form_of and norm_name(e.form_of, self.org_name) in tnames:
                        law = target.short  # 서식의 '같은 규정' 은 소속 규정
                    if law is None or norm_name(law, self.org_name) not in tnames:
                        continue
                    if keys is not None and r["key"] not in keys:
                        continue
                    res.append({"entry": e, "loc": loc, "article": akey, "text": text, "ref": r})
        return res

    def search_articles(self, query, k=8, layers=("org", "upper"), exclude=None):
        items = []
        for e in self.regs():
            if e.layer not in layers or (exclude and e.id == exclude):
                continue
            for a in e.reg.articles:
                if not a.deleted:
                    items.append((e, a))
        if not items:
            return []
        bm = BM25(items, lambda x: f"{x[1].title} {x[1].title} {x[1].plain}")
        return [(it[0], it[1], s) for it, s in bm.search(query, k)]

    def search_regs(self, query, k=5, exclude=None):
        regs = [e for e in self.regs() if not (exclude and e.id == exclude)]
        if not regs:
            return []
        bm = BM25(regs, lambda e: (e.title + " ") * 3 + " ".join(a.title + " " + a.plain for a in e.reg.articles))
        return bm.search(query, k)


def regulation_text(reg):
    return reg.to_text()
