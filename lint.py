"""규정집 전체 점검: 인용 오류, 번호 체계, 규정명·용어 불일치, 규정 간 충돌, 서식·별표 누락."""
import re
from itertools import combinations

from core import regparse
from core.textutil import strip_josa

from drafting import josa

from library import RefFinder, RE_FORMREF, RE_APPXREF, norm_name

LEVELS = {"오류": 0, "주의": 1, "참고": 2}
DEFAULT_TERMS = [["이용자", "사용자"], ["누리집", "홈페이지"], ["신청인", "신청자"], ["이사장", "사장"]]
NATIONAL = re.compile(r"(?:법|법률|시행령|시행규칙)$")
RX_DEADLINE = re.compile(r"((?:[가-힣]+\s+){0,2}[가-힣]*(?:날|때)\s*부터|(?:[가-힣]+\s+){0,1}[가-힣]+\s*전까지|(?:[가-힣]+\s+){0,1}[가-힣]+\s*후)\s*(\d+)\s*일\s*(이내|전까지)?")
STOP = {"마친", "부터", "출장자", "이사장", "공단", "직원", "제출", "작성", "하여야", "한다", "이내", "별지", "서식", "다음", "경우", "따른", "해당", "규정", "날부터"}


def _issue(level, kind, doc, loc, msg, fix=None, entry_id=None):
    return {"level": level, "kind": kind, "doc": doc, "loc": loc, "message": msg, "fix": fix, "entry_id": entry_id}


def check(lib, terms=None):
    terms = terms or DEFAULT_TERMS
    issues = []
    regs = lib.regs()
    for e in regs:
        issues += _numbering(e)
        issues += _internal_refs(e)
        issues += _terms(e, terms)
        issues += _appendix(e, lib)
    issues += _external_refs(lib)
    issues += _conflicts(lib)
    issues += _forms(lib)
    issues.sort(key=lambda x: (LEVELS[x["level"]], x["kind"], x["doc"]))
    summary = {}
    for i in issues:
        summary[i["level"]] = summary.get(i["level"], 0) + 1
    return {"issues": issues, "summary": summary, "n_docs": len(lib.entries), "n_regs": len(regs),
            "n_articles": sum(len(e.reg.articles) for e in regs)}


def _numbering(e):
    out = []
    seen = set()
    prev = None
    for a in e.reg.articles:
        if a.key in seen:
            out.append(_issue("오류", "조 번호", e.short, a.label, f"{a.label}가 두 번 나옵니다.", entry_id=e.id))
        seen.add(a.key)
        if prev and a.sub == 0 and a.no != prev.no + 1 and a.no > prev.no:
            missing = ", ".join(f"제{n}조" for n in range(prev.no + 1, a.no))
            out.append(_issue("오류", "조 번호", e.short, a.label, f"{prev.label} 다음이 {a.label}입니다. {missing}가 빠졌습니다(삭제된 조라면 '제○조 삭제'로 남겨야 합니다).", entry_id=e.id))
        if prev and (a.no, a.sub) <= (prev.no, prev.sub):
            out.append(_issue("오류", "조 번호", e.short, a.label, f"{a.label}가 {prev.label}보다 뒤에 있습니다(순서 오류).", entry_id=e.id))
        nums = [p.no for p in a.paras if p.no]
        if nums and nums != list(range(1, len(nums) + 1)):
            out.append(_issue("오류", "항 번호", e.short, a.label, f"항 번호가 {', '.join(regparse.CIRCLED[n - 1] for n in nums)} 순서입니다.", entry_id=e.id))
        for p in a.paras:
            inos = [int(re.match(r"\d+", it.no).group()) for it in p.items if re.match(r"^\d+$", it.no)]
            if inos and inos != list(range(1, len(inos) + 1)):
                out.append(_issue("오류", "호 번호", e.short, a.label, f"호 번호가 {', '.join(map(str, inos))} 순서입니다.", entry_id=e.id))
        if not a.title and not a.deleted:
            out.append(_issue("참고", "조 제목", e.short, a.label, "조 제목이 없습니다.", entry_id=e.id))
        prev = a
    return out


def _internal_refs(e):
    out = []
    keys = {a.key: a for a in e.reg.articles}
    rf = RefFinder(e.aliases)
    units = [(a.label, a, "\n".join(a.body_lines())) for a in e.reg.articles]
    if e.reg.appendices:
        units.append(("별표", None, "\n".join(e.reg.appendices)))
    for loc, a, text in units:
        for r in rf.find(text):
            if r["law"] is not None:
                continue
            t = keys.get(r["key"])
            lab = regparse.alabel(*regparse.parse_key(r["key"]))
            if t is None:
                out.append(_issue("오류", "인용 오류", e.short, loc, f"없는 조({lab})를 인용합니다: '{_around(text, r)}'", entry_id=e.id))
            elif t.deleted:
                out.append(_issue("오류", "인용 오류", e.short, loc, f"삭제된 조({lab})를 인용합니다.", entry_id=e.id))
            elif a is not None and t is a and not r["detail"]:
                out.append(_issue("주의", "인용 오류", e.short, loc, f"자기 조({lab})를 인용합니다. 다른 조를 가리키려던 것인지 확인하세요.", entry_id=e.id))
            else:
                m = re.match(r"제(\d+)항", r["detail"] or "")
                if m and int(m.group(1)) > max([p.no for p in t.paras] or [1]):
                    out.append(_issue("오류", "인용 오류", e.short, loc, f"{lab}에는 제{m.group(1)}항이 없습니다.", entry_id=e.id))
    return out


def _around(text, r, w=18):
    """인용 앞뒤 문맥을 어절 단위로 자른다."""
    a, b = max(0, r["start"] - w), min(len(text), r["end"] + w)
    while a > 0 and not text[a - 1].isspace():
        a -= 1
    while b < len(text) and not text[b].isspace():
        b += 1
    s = text[a:b].replace("\n", " ").strip()
    return ("…" if a > 0 else "") + s + ("…" if b < len(text) else "")


def _external_refs(lib):
    out = []
    for e in lib.entries:
        rf = RefFinder(e.aliases)
        names = set()
        for loc, akey, text in lib.units(e):
            for m in re.finditer(r"「([^」]{1,60})」", text):
                names.add((m.group(1), loc))
            for r in rf.find(text):
                if not r["law"]:
                    continue
                t = lib.by_name(r["law"])
                if t is None:
                    continue
                ta = t.reg.get(r["key"])
                lab = regparse.alabel(*regparse.parse_key(r["key"]))
                if ta is None:
                    out.append(_issue("오류", "인용 오류", e.short, loc, f"「{t.short}」에 없는 조({lab})를 인용합니다.", entry_id=e.id))
                elif ta.deleted:
                    out.append(_issue("오류", "인용 오류", e.short, loc, f"「{t.short}」의 삭제된 조({lab})를 인용합니다.", entry_id=e.id))
        for name, loc in sorted(names):
            t = lib.by_name(name)
            if t is None:
                if NATIONAL.search(name):
                    continue  # 국가 법령은 규정집에 없을 수 있다
                if norm_name(name, lib.org_name) == norm_name(e.title, lib.org_name):
                    continue
                out.append(_issue("주의", "인용 규정 없음", e.short, loc, f"{josa('「' + name + '」', '이/가')} 규정집에 없습니다. 이름이 바뀌었거나 폐지된 규정인지 확인하세요.", entry_id=e.id))
            elif name != t.short and name != t.title:
                out.append(_issue("주의", "규정명 표기", e.short, loc, f"「{name}」 → 정식 이름은 「{t.short}」입니다.",
                                  fix={"find": f"「{name}」", "repl": f"「{t.short}」"}, entry_id=e.id))
    return out


def _terms(e, terms):
    out = []
    defined = set(re.findall(r"[\"“]([^\"”]{1,15})[\"”]\s*(?:이)?란", e.reg.to_text()))
    for group in terms:
        main = next((t for t in group if t in defined), None)
        for a in e.reg.articles:
            text = "\n".join(a.body_lines())
            for t in group:
                if t == (main or group[0]) or not re.search(rf"(?<![가-힣]){t}(?=[은는이가을를의에과와도로]|\s|$)", text):
                    continue
                used_main = main or (group[0] if re.search(group[0], e.reg.to_text()) else None)
                if not used_main:
                    continue
                why = f"정의한 용어 '{main}'" if main else f"같은 규정 안에서 '{used_main}'"
                out.append(_issue("주의", "용어 불일치", e.short, a.label, f"'{t}'를 썼습니다. {josa(why, '과/와')} 맞추는 것이 좋습니다.",
                                  fix={"find": t, "repl": used_main}, entry_id=e.id))
    return out


def _appendix(e, lib):
    out = []
    text = e.reg.to_text()
    have = set(int(n) for n in re.findall(r"\[?별표\s*(\d+)\]?", "\n".join(e.reg.appendices)))
    for a in e.reg.articles:
        for m in RE_APPXREF.finditer("\n".join(a.body_lines())):
            if int(m.group(1)) not in have:
                out.append(_issue("주의", "별표 없음", e.short, a.label, f"{josa('별표 ' + m.group(1), '을/를')} 인용하지만 규정 본문에 별표가 없습니다(별도 파일인지 확인).", entry_id=e.id))
    for line in e.reg.appendices:
        m = re.match(r"^\[별표\s*(\d+)\].*\(제(\d+)조(?:의(\d+))?\s*관련\)", line)
        if m:
            k = regparse.akey(int(m.group(2)), int(m.group(3) or 0))
            a = e.reg.get(k)
            if a is None or f"별표 {m.group(1)}" not in "\n".join(a.body_lines()).replace("별표" + m.group(1), "별표 " + m.group(1)):
                out.append(_issue("오류", "별표 관련 조", e.short, "별표", f"[별표 {m.group(1)}]이 '제{k}조 관련'으로 되어 있으나 그 조가 별표를 인용하지 않습니다.", entry_id=e.id))
    return out


def _forms(lib):
    out = []
    forms = lib.forms()
    for e in lib.regs("org"):
        sn = norm_name(e.short, lib.org_name)
        mine = {f.form_no for f in forms if f.form_of and norm_name(f.form_of, lib.org_name) == sn}
        for a in e.reg.articles:
            for m in RE_FORMREF.finditer("\n".join(a.body_lines())):
                n = int(m.group(1))
                if n not in mine:
                    out.append(_issue("참고", "서식 없음", e.short, a.label, f"별지 제{n}호서식이 규정집 서식 폴더에 없습니다. (서식 파일 이름 예: [{e.short}] 별지{n} 서식이름.hwpx)", entry_id=e.id))
    for f in forms:
        if f.form_of and lib.by_name(f.form_of) is None:
            out.append(_issue("주의", "서식 소속", f.short, "파일 이름", f"서식의 소속 규정 {josa('「' + f.form_of + '」', '이/가')} 규정집에 없습니다.", entry_id=f.id))
    return out


def _deadlines(e):
    out = []
    for a in e.reg.articles:
        for path_text in a.body_lines():
            for m in RX_DEADLINE.finditer(path_text):
                anchor = re.sub(r"\s+", " ", m.group(1)).strip()
                words = {strip_josa(w) for w in re.findall(r"[가-힣]{2,}", path_text)} - STOP
                out.append({"article": a, "anchor": anchor, "n": int(m.group(2)), "words": words, "text": path_text.strip()})
    return out


def _conflicts(lib):
    """같은 일을 정한 두 규정의 기한이 다르면 충돌로 본다(기준 구절 같음 + 핵심어 겹침)."""
    out = []
    regs = lib.regs("org")
    dl = {e.id: _deadlines(e) for e in regs}
    for e1, e2 in combinations(regs, 2):
        for x in dl[e1.id]:
            for y in dl[e2.id]:
                if x["anchor"] != y["anchor"] or x["n"] == y["n"]:
                    continue
                common = x["words"] & y["words"]
                if len(common) < 2:
                    continue
                key_words = ", ".join(sorted(common)[:4])
                out.append(_issue("오류", "규정 간 충돌", e1.short, x["article"].label,
                                  f"{e1.short} {x['article'].label}는 '{x['anchor']} {x['n']}일', {e2.short} {y['article'].label}는 '{y['anchor']} {y['n']}일'로 기한이 다릅니다. (겹치는 말: {key_words})",
                                  entry_id=e1.id))
    return out
